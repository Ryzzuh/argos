"""Stateless runner for CI.

A cloud runner keeps nothing between invocations, so the diff that makes this
tool useful has to live in a small JSON file the workflow commits back to the
repository. Only what the next run needs to reach a conclusion goes in it —
never a credential.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from . import health, notify
from .config import SEAT_STATE, TZ, booking_url
from .scrape import sweep


def load_state() -> dict:
    try:
        return json.loads(SEAT_STATE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    SEAT_STATE.parent.mkdir(parents=True, exist_ok=True)
    SEAT_STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def build_state(reports) -> dict:
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "showtimes": {
            r.showtime_id: {
                "starts_at": r.starts_at.isoformat(),
                "bookable": sorted(s.seat_id for s in r.free_bookable),
                "labels": {s.seat_id: f"{s.row_label}{s.seat_label}"
                           for s in r.free_bookable},
                "total": r.total,
            }
            for r in reports
        },
    }


def diff(previous: dict, current: dict) -> tuple[dict, list]:
    """Returns (newly freed seats per showtime, newly added sessions)."""
    old = previous.get("showtimes") or {}
    new = current["showtimes"]

    freed: dict[str, list[str]] = {}
    added: list[tuple[str, dict]] = []

    for showtime_id, info in new.items():
        if showtime_id not in old:
            # A session with no previous state cannot be diffed. If it already
            # has seats, it is a newly added screening, which is the single
            # most valuable thing this can find on a sold-out run.
            if old and info["bookable"]:
                added.append((showtime_id, info))
            continue
        fresh = sorted(set(info["bookable"]) - set(old[showtime_id]["bookable"]))
        if fresh:
            freed[showtime_id] = fresh
    return freed, added


def _when(iso: str) -> str:
    return f"{datetime.fromisoformat(iso).astimezone(TZ):%a %-d %b, %-I:%M%p}"


def announce(freed: dict, added: list, current: dict) -> None:
    blocks = []
    for showtime_id, seat_ids in freed.items():
        info = current["showtimes"][showtime_id]
        labels = ", ".join(info["labels"].get(s, s) for s in seat_ids)
        blocks.append(f"*{_when(info['starts_at'])}* — {len(seat_ids)} seat(s): "
                      f"`{labels}`\n[Book now]({booking_url(showtime_id)})")
    if blocks:
        total = sum(len(v) for v in freed.values())
        notify.send(
            f"🎟 *{total} seat{'' if total == 1 else 's'} just opened* — "
            f"THE ODYSSEY, IMAX 70mm\n\n" + "\n\n".join(blocks) +
            "\n\n_Seats go fast; this is a notification, not a hold._",
            disable_preview=False)

    if added:
        parts = [f"*{_when(info['starts_at'])}* — {len(info['bookable'])} of "
                 f"{info['total']} seats free\n[Book now]({booking_url(sid)})"
                 for sid, info in added]
        notify.send(
            f"🆕 *{len(added)} new session{'' if len(added) == 1 else 's'} added* — "
            f"THE ODYSSEY, IMAX 70mm\n\n" + "\n\n".join(parts),
            disable_preview=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stateless seat check for CI")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    health.claim_single_instance()
    with health.guard("sweep"):
        reports, summary = asyncio.run(sweep(verbose=False))

    current = build_state(reports)
    previous = load_state()
    freed, added = diff(previous, current)

    if warnings := summary["warnings"]:
        health.report_failure("schema", "Sweep completed but the data did not look "
                              "the way it should:\n" + "\n".join(f"• {w}" for w in warnings))
    else:
        health.report_recovery("schema")
    health.report_recovery("sweep")

    print(f"{summary['showtimes']} sessions, {summary['bookable_total']} bookable seat(s); "
          f"{sum(len(v) for v in freed.values())} newly freed, {len(added)} new session(s)")

    if args.dry_run:
        print(json.dumps({"freed": freed, "added": [a[0] for a in added]}, indent=2))
        return

    if freed or added:
        announce(freed, added, current)

    save_state(current)
    health.write_heartbeat("ok", showtimes=summary["showtimes"],
                           bookable=summary["bookable_total"])
    health.maybe_send_digest(
        f"{summary['bookable_total']} bookable seat(s) across {summary['showtimes']} "
        "sessions. Cloud monitor is running normally.")


if __name__ == "__main__":
    main()
