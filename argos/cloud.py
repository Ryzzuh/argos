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
from .films import load as load_films
from .zone import Zone, effective, format_runs, qualify
from .scrape import sweep


def _state_path(film_id: str):
    """One baseline per film: a shared file would diff films against each other."""
    return SEAT_STATE.with_name(f"seats-{film_id}.json")


def load_state(film_id: str) -> dict:
    try:
        return json.loads(_state_path(film_id).read_text())
    except Exception:
        return {}


def save_state(state: dict, film_id: str) -> None:
    path = _state_path(film_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def build_state(reports) -> dict:
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "showtimes": {
            r.showtime_id: {
                "starts_at": r.starts_at.isoformat(),
                "bookable": sorted(s.seat_id for s in r.free_bookable),
                "labels": {s.seat_id: f"{s.row_label}{s.seat_label}"
                           for s in r.free_bookable},
                "rows": {s.seat_id: s.row_label for s in r.free_bookable},
                "total": r.total,
            }
            for r in reports
        },
    }


def _seats(info: dict) -> list[tuple[str, str, str]]:
    """(seat_id, row_label, label) for a showtime's free seats in saved state."""
    rows, labels = info.get("rows") or {}, info.get("labels") or {}
    # State written before rows were recorded: the label starts with the row.
    return [(sid, rows.get(sid) or labels.get(sid, "?")[:1], labels.get(sid, sid))
            for sid in info["bookable"]]


def diff(previous: dict, current: dict,
         zone: Zone | None = None) -> tuple[dict, list]:
    """Returns (newly qualifying seats per showtime, newly added sessions).

    The zone is applied to both sides of the comparison, so changing it cannot
    make old seats look newly released.
    """
    old = previous.get("showtimes") or {}
    new = current["showtimes"]

    freed: dict[str, list[str]] = {}
    added: list[tuple[str, dict]] = []

    for showtime_id, info in new.items():
        now = qualify(zone, _seats(info))
        if showtime_id not in old:
            # A session with no previous state cannot be diffed. If it already
            # has seats, it is a newly added screening, which is the single
            # most valuable thing this can find on a sold-out run.
            if old and now:
                added.append((showtime_id, {**info, "bookable": sorted(now)}))
            continue
        fresh = sorted(now - qualify(zone, _seats(old[showtime_id])))
        if fresh:
            freed[showtime_id] = fresh
    return freed, added


def _when(iso: str) -> str:
    return f"{datetime.fromisoformat(iso).astimezone(TZ):%a %-d %b, %-I:%M%p}"


def announce(freed: dict, added: list, current: dict, title: str,
             zone: Zone | None = None) -> None:
    where = " in your zone" if zone else ""
    together = zone.together if zone else 1
    blocks = []
    for showtime_id, seat_ids in freed.items():
        info = current["showtimes"][showtime_id]
        if together > 1:
            chosen = [t for t in _seats(info) if t[0] in set(seat_ids)]
            labels = format_runs(chosen)
        else:
            labels = ", ".join(info["labels"].get(s, s) for s in seat_ids)
        blocks.append(f"*{_when(info['starts_at'])}* — {len(seat_ids)} seat(s): "
                      f"`{labels}`\n[Book now]({booking_url(showtime_id)})")
    if blocks:
        total = sum(len(v) for v in freed.values())
        notify.send(
            f"🎟 *{total} seat{'' if total == 1 else 's'} just opened{where}* — "
            f"{title}\n\n" + "\n\n".join(blocks) +
            "\n\n_Seats go fast; this is a notification, not a hold._",
            disable_preview=False)

    if added:
        parts = [f"*{_when(info['starts_at'])}* — "
                 + (f"{len(info['bookable'])} seats in your zone" if zone else
                    f"{len(info['bookable'])} of {info['total']} seats free")
                 + f"\n[Book now]({booking_url(sid)})"
                 for sid, info in added]
        notify.send(
            f"🆕 *{len(added)} new session{'' if len(added) == 1 else 's'} added* — "
            f"{title}\n\n" + "\n\n".join(parts),
            disable_preview=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stateless seat check for CI")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    health.claim_single_instance()
    films = load_films()
    if not films:
        print("no films enabled in films.toml — nothing to monitor")
        health.write_heartbeat("ok", showtimes=0, bookable=0, films=0)
        return
    film = films[0]
    with health.guard(f"sweep:{film.id}"):
        reports, summary = asyncio.run(sweep(film.id, verbose=False,
                                             lead_days=film.lead_days))

    current = build_state(reports)
    previous = load_state(film.id)
    zone = effective(film.zone, film.id, film.name)
    freed, added = diff(previous, current, zone)

    if warnings := summary["warnings"]:
        health.report_failure(f"schema:{film.id}", "Sweep completed but the data did not look "
                              "the way it should:\n" + "\n".join(f"• {w}" for w in warnings))
    else:
        health.report_recovery(f"schema:{film.id}")
    health.report_recovery(f"sweep:{film.id}")

    print(f"{summary['showtimes']} sessions, {summary['bookable_total']} bookable seat(s); "
          f"{sum(len(v) for v in freed.values())} newly freed, {len(added)} new session(s)")

    if args.dry_run:
        print(json.dumps({"freed": freed, "added": [a[0] for a in added]}, indent=2))
        return

    if freed or added:
        announce(freed, added, current, film.name, zone)

    save_state(current, film.id)
    health.write_heartbeat("ok", showtimes=summary["showtimes"],
                           bookable=summary["bookable_total"])
    health.maybe_send_digest(
        f"{summary['bookable_total']} bookable seat(s) across {summary['showtimes']} "
        "sessions. Cloud monitor is running normally.")


if __name__ == "__main__":
    main()
