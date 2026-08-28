"""SQLite history.

Only non-sold seats and per-showtime rollups are kept, so a sweep every 30
minutes stays small indefinitely while still supporting "what changed since
last time" and a trend over days.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at       TEXT NOT NULL,
    showtime_count INTEGER NOT NULL,
    bookable_total INTEGER NOT NULL,
    partial        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS showtimes (
    id             TEXT PRIMARY KEY,
    business_date  TEXT NOT NULL,
    starts_at      TEXT NOT NULL,
    screen_id      TEXT,
    seat_layout_id TEXT,
    first_seen     TEXT
);
CREATE TABLE IF NOT EXISTS rollups (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    showtime_id   TEXT NOT NULL,
    bookable      INTEGER NOT NULL,
    accessible    INTEGER NOT NULL,
    house         INTEGER NOT NULL,
    sold          INTEGER NOT NULL,
    total         INTEGER NOT NULL,
    sold_out_flag INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, showtime_id)
);
CREATE TABLE IF NOT EXISTS free_seats (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    showtime_id TEXT NOT NULL,
    seat_id     TEXT NOT NULL,
    row_label   TEXT,
    seat_label  TEXT,
    seat_type   TEXT,
    area        TEXT,
    PRIMARY KEY (snapshot_id, showtime_id, seat_id)
);
CREATE INDEX IF NOT EXISTS idx_rollups_showtime ON rollups(showtime_id);
CREATE INDEX IF NOT EXISTS idx_free_showtime ON free_seats(showtime_id);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        # Older databases predate first_seen, which is how a newly added session
        # is told apart from one that was merely out of scope last run.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(showtimes)")}
        if "first_seen" not in cols:
            conn.execute("ALTER TABLE showtimes ADD COLUMN first_seen TEXT")
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_snapshot(reports, *, partial: bool = False) -> int:
    """Persist one sweep. Returns the new snapshot id."""
    taken_at = datetime.now(timezone.utc).isoformat()
    bookable_total = sum(r.bookable_count for r in reports)

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO snapshots (taken_at, showtime_count, bookable_total, partial)"
            " VALUES (?, ?, ?, ?)",
            (taken_at, len(reports), bookable_total, int(partial)),
        )
        snap = cur.lastrowid

        # first_seen is written once and never overwritten, so it marks the
        # sweep on which a session first appeared in the schedule.
        conn.executemany(
            "INSERT INTO showtimes"
            " (id, business_date, starts_at, screen_id, seat_layout_id, first_seen)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            "   business_date=excluded.business_date,"
            "   starts_at=excluded.starts_at,"
            "   screen_id=excluded.screen_id,"
            "   seat_layout_id=excluded.seat_layout_id",
            [(r.showtime_id, r.business_date, r.starts_at.isoformat(),
              r.screen_id, r.seat_layout_id, taken_at) for r in reports],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO rollups"
            " (snapshot_id, showtime_id, bookable, accessible, house, sold, total, sold_out_flag)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(snap, r.showtime_id, r.bookable_count, len(r.free_accessible),
              r.house, r.sold, r.total, int(r.is_sold_out_flag)) for r in reports],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO free_seats"
            " (snapshot_id, showtime_id, seat_id, row_label, seat_label, seat_type, area)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(snap, r.showtime_id, s.seat_id, s.row_label, s.seat_label,
              s.seat_type, s.area)
             for r in reports for s in (r.free_bookable + r.free_accessible)],
        )
    return snap


def latest_snapshots(limit: int = 2) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM snapshots ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def bookable_seats_in(snapshot_id: int) -> dict[str, set[str]]:
    """{showtime_id: {seat_id, ...}} for genuinely bookable free seats."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT showtime_id, seat_id FROM free_seats"
            " WHERE snapshot_id = ? AND seat_type = 'Normal'", (snapshot_id,)
        ).fetchall()
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(row["showtime_id"], set()).add(row["seat_id"])
    return out


def snapshot_rows(snapshot_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT r.*, s.business_date, s.starts_at FROM rollups r"
            " JOIN showtimes s ON s.id = r.showtime_id"
            " WHERE r.snapshot_id = ? ORDER BY s.starts_at", (snapshot_id,)
        ).fetchall()


def free_seat_rows(snapshot_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM free_seats WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchall()


def sessions_first_seen_in(snapshot_id: int) -> list[sqlite3.Row]:
    """Sessions that appeared in the schedule for the first time on this sweep.

    A newly added screening is the most valuable thing this tool can find, and
    it looks nothing like a seat release: the whole auditorium is free at once.
    """
    with connect() as conn:
        snap = conn.execute("SELECT taken_at FROM snapshots WHERE id = ?",
                            (snapshot_id,)).fetchone()
        if snap is None:
            return []
        return conn.execute(
            "SELECT s.*, r.bookable, r.total FROM showtimes s"
            " JOIN rollups r ON r.showtime_id = s.id AND r.snapshot_id = ?"
            " WHERE s.first_seen = ? ORDER BY s.starts_at",
            (snapshot_id, snap["taken_at"]),
        ).fetchall()


def is_first_ever_snapshot(snapshot_id: int) -> bool:
    """True for the very first sweep, where every session is trivially new."""
    with connect() as conn:
        row = conn.execute("SELECT MIN(id) AS first FROM snapshots").fetchone()
    return row is not None and row["first"] == snapshot_id


def latest_full_snapshot() -> sqlite3.Row | None:
    """The newest snapshot that covered the whole schedule.

    The 10-minute watchlist run only checks the next fortnight, so rendering
    "the latest snapshot" would shrink the report page to a partial view. The
    widest recent sweep is the one worth publishing.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots ORDER BY id DESC LIMIT 12").fetchall()
    if not rows:
        return None
    # Full sweeps cover ~77 sessions and watchlist runs ~38, so the two are far
    # apart. Counting down as sessions start means the newest full sweep is
    # usually a little smaller than the widest, hence a threshold rather than
    # an exact match.
    widest = max(r["showtime_count"] for r in rows)
    return next((r for r in rows if r["showtime_count"] >= widest * 0.75), rows[0])
