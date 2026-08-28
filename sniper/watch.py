"""Run a sweep and alert when a genuinely bookable seat appears.

Alerting is restricted to seats of type Normal. The wheelchair and companion
spaces in row M are free on most sessions permanently, so including them would
mean an alert every single run and a monitor nobody trusts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from . import health, notify, report, store
from .config import ALERT_STATE, TZ, booking_url
from .scrape import sweep_and_persist


def _state() -> dict:
    try:
        return json.loads(ALERT_STATE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
    ALERT_STATE.write_text(json.dumps(state, indent=2, default=str))


def find_new_seats() -> dict[str, set[str]]:
    """Bookable seats present in the newest snapshot but not the one before.

    Only showtimes appearing in *both* snapshots are compared, so a narrow
    watchlist run followed by a full sweep doesn't report every previously
    unchecked session as a new opening.
    """
    snaps = store.latest_snapshots(2)
    if len(snaps) < 2:
        return {}

    current = store.bookable_seats_in(snaps[0]["id"])
    previous = store.bookable_seats_in(snaps[1]["id"])
    checked_before = {r["showtime_id"] for r in store.snapshot_rows(snaps[1]["id"])}

    new: dict[str, set[str]] = {}
    for showtime_id, seats in current.items():
        if showtime_id not in checked_before:
            continue
        fresh = seats - previous.get(showtime_id, set())
        if fresh:
            new[showtime_id] = fresh
    return new


def find_new_sessions() -> list:
    """Screenings added to the schedule since the last sweep, with seats free.

    These are invisible to the seat diff: a session that did not exist before
    has no previous state to differ from, so every seat in it is "unchanged"
    from the second sweep onward. IMAX adding extra 70mm screenings for a
    sold-out run is the likeliest way a ticket becomes available at all, so it
    gets its own detector.
    """
    snaps = store.latest_snapshots(1)
    if not snaps or store.is_first_ever_snapshot(snaps[0]["id"]):
        return []                       # first run: everything is trivially new
    return [r for r in store.sessions_first_seen_in(snaps[0]["id"]) if r["bookable"]]


def _describe(showtime_id: str, seat_ids: set[str], snap: int | None = None) -> str:
    if snap is None:
        snap = store.latest_snapshots(1)[0]["id"]
    rows = {r["showtime_id"]: r for r in store.snapshot_rows(snap)}
    seats = [s for s in store.free_seat_rows(snap)
             if s["showtime_id"] == showtime_id and s["seat_id"] in seat_ids]
    seats.sort(key=lambda s: (s["row_label"],
                              int(s["seat_label"]) if str(s["seat_label"]).isdigit() else 0))
    labels = ", ".join(f"{s['row_label']}{s['seat_label']}" for s in seats)

    row = rows.get(showtime_id)
    when = "unknown time"
    if row:
        dt = datetime.fromisoformat(row["starts_at"]).astimezone(TZ)
        when = f"{dt:%a %-d %b, %-I:%M%p}"
    return (f"*{when}* — {len(seats)} seat(s): `{labels}`\n"
            f"[Book now]({booking_url(showtime_id)})")


def alert_new_sessions(sessions: list) -> None:
    blocks = []
    for r in sessions:
        dt = datetime.fromisoformat(r["starts_at"]).astimezone(TZ)
        blocks.append(f"*{dt:%a %-d %b, %-I:%M%p}* — {r['bookable']} of "
                      f"{r['total']} seats free\n[Book now]({booking_url(r['id'])})")
    text = (f"🆕 *{len(sessions)} new session"
            f"{'' if len(sessions) == 1 else 's'} added* — THE ODYSSEY, IMAX 70mm\n\n"
            + "\n\n".join(blocks))
    if not notify.send(text, disable_preview=False):
        health.log_error(f"could not deliver new-session alert: {text[:120]}")


def alert(new: dict[str, set[str]], snap: int | None = None,
          *, replay: bool = False) -> None:
    total = sum(len(v) for v in new.values())
    blocks = [_describe(sid, seats, snap) for sid, seats in new.items()]
    if replay:
        # A replay must never be mistaken for a live opening.
        head = (f"🧪 *Test alert (replay of snapshot #{snap})* — this is what a real "
                f"one looks like. These seats are not necessarily still free.\n\n")
        tail = ""
    else:
        head = f"🎟 *{total} seat{'' if total == 1 else 's'} just opened* — THE ODYSSEY, IMAX 70mm\n\n"
        tail = "\n\n_Seats go fast; this is a notification, not a hold._"
    text = head + "\n\n".join(blocks) + tail
    if not notify.send(text, disable_preview=False):
        health.log_error(f"could not deliver seat alert: {text[:120]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch for released seats")
    parser.add_argument("--within-days", type=int, default=None,
                        help="only check sessions this many days out")
    parser.add_argument("--dry-run", action="store_true",
                        help="sweep and diff, but send nothing")
    parser.add_argument("--force-alert", action="store_true",
                        help="send an alert for whatever is currently free, to test the path")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--replay", type=int, metavar="SNAPSHOT",
                        help="re-send the alert for a past snapshot, to verify "
                             "formatting and delivery without waiting for a release")
    args = parser.parse_args()

    if args.replay is not None:
        seats = store.bookable_seats_in(args.replay)
        if not seats:
            raise SystemExit(f"snapshot #{args.replay} had no bookable seats to replay")
        if args.dry_run:
            for sid, ids in seats.items():
                print(_describe(sid, ids, args.replay))
        else:
            alert(seats, args.replay, replay=True)
            print(f"replayed snapshot #{args.replay} to Telegram")
        return

    with health.guard("sweep"):
        summary = asyncio.run(sweep_and_persist(verbose=False,
                                                within_days=args.within_days))

    fresh_sessions = find_new_sessions()
    if fresh_sessions:
        print(f"{len(fresh_sessions)} newly added session(s) with seats free")
        if args.dry_run:
            for r in fresh_sessions:
                print(f"  {r['starts_at']}  {r['bookable']}/{r['total']} free")
        else:
            alert_new_sessions(fresh_sessions)

    new = find_new_seats()

    if args.force_alert and not new:
        # Test the delivery path against whatever is genuinely free right now.
        snap = store.latest_snapshots(1)[0]["id"]
        new = store.bookable_seats_in(snap)

    if new:
        total = sum(len(v) for v in new.values())
        print(f"{total} newly bookable seat(s) across {len(new)} session(s)")
        if args.dry_run:
            for sid, seats in new.items():
                print("  " + _describe(sid, seats).replace("\n", " | "))
        else:
            alert(new)
    else:
        print(f"no change — {summary['bookable_total']} bookable seat(s) "
              f"across {summary['showtimes']} session(s)")

    if not args.no_report:
        report.main()

    health.maybe_send_digest(
        f"{summary['bookable_total']} bookable seat(s) across "
        f"{summary['showtimes']} sessions. Monitor is running normally."
    )


if __name__ == "__main__":
    main()
