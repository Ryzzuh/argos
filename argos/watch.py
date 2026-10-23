"""Run a sweep and alert when a genuinely bookable seat appears.

Alerting is restricted to seats of type Normal. The wheelchair and companion
spaces in row M are free on most sessions permanently, so including them would
mean an alert every single run and a monitor nobody trusts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime

from . import health, notify, report, store, timing
from .config import ALERT_STATE, TZ, booking_url
from .films import Film, load as load_films
from .scrape import sweep_and_persist
from .zone import Zone, effective, format_runs, newly_qualifying, qualify


def _state() -> dict:
    try:
        return json.loads(ALERT_STATE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
    ALERT_STATE.write_text(json.dumps(state, indent=2, default=str))


def find_new_seats(film_id: str, zone: Zone | None = None) -> dict[str, set[str]]:
    """Seats that qualify in the newest snapshot but did not in the one before.

    Only showtimes appearing in *both* snapshots are compared, so a narrow
    watchlist run followed by a full sweep doesn't report every previously
    unchecked session as a new opening. The zone is applied to both snapshots,
    so changing it cannot make old seats look newly released.
    """
    snaps = store.latest_snapshots(2, film_id)
    if len(snaps) < 2:
        return {}
    comparable = {r["showtime_id"] for r in store.snapshot_rows(snaps[1]["id"])}
    return newly_qualifying(store.bookable_details_in(snaps[1]["id"]),
                            store.bookable_details_in(snaps[0]["id"]),
                            zone, comparable)


def find_new_sessions(film_id: str, zone: Zone | None = None) -> list[dict]:
    """Screenings added to the schedule since the last sweep, with seats free.

    These are invisible to the seat diff: a session that did not exist before
    has no previous state to differ from, so every seat in it is "unchanged"
    from the second sweep onward. IMAX adding extra 70mm screenings for a
    sold-out run is the likeliest way a ticket becomes available at all, so it
    gets its own detector.
    """
    snaps = store.latest_snapshots(1, film_id)
    if not snaps or store.is_first_snapshot_for_film(snaps[0]["id"], film_id):
        return []            # a film's first sweep: everything is trivially new
    sessions = [dict(r) for r in store.sessions_first_seen_in(snaps[0]["id"])
                if r["bookable"]]
    if zone is None:
        return sessions
    # A new session with 400 free seats and none in your zone is not news.
    details = store.bookable_details_in(snaps[0]["id"])
    kept = []
    for r in sessions:
        n = len(qualify(zone, details.get(r["id"], [])))
        if n:
            kept.append({**r, "bookable": n})
    return kept


def _describe(showtime_id: str, seat_ids: set[str], snap: int | None = None,
              together: int = 1) -> str:
    if snap is None:
        raise ValueError("a snapshot id is required: the latest one globally may "
                         "belong to a different film")
    rows = {r["showtime_id"]: r for r in store.snapshot_rows(snap)}
    seats = [s for s in store.free_seat_rows(snap)
             if s["showtime_id"] == showtime_id and s["seat_id"] in seat_ids]
    seats.sort(key=lambda s: (s["row_label"],
                              int(s["seat_label"]) if str(s["seat_label"]).isdigit() else 0))
    if together > 1:
        labels = format_runs([(s["seat_id"], s["row_label"], s["seat_label"])
                              for s in seats])
    else:
        labels = ", ".join(f"{s['row_label']}{s['seat_label']}" for s in seats)

    row = rows.get(showtime_id)
    when = "unknown time"
    if row:
        dt = datetime.fromisoformat(row["starts_at"]).astimezone(TZ)
        when = f"{dt:%a %-d %b, %-I:%M%p}"
    return (f"*{when}* — {len(seats)} seat(s): `{labels}`\n"
            f"[Book now]({booking_url(showtime_id)})")


def alert_new_sessions(sessions: list, title: str | None = None,
                       zone: Zone | None = None) -> None:
    blocks = []
    for r in sessions:
        dt = datetime.fromisoformat(r["starts_at"]).astimezone(TZ)
        free = (f"{r['bookable']} seats in your zone" if zone
                else f"{r['bookable']} of {r['total']} seats free")
        blocks.append(f"*{dt:%a %-d %b, %-I:%M%p}* — {free}\n"
                      f"[Book now]({booking_url(r['id'])})")
    text = (f"🆕 *{len(sessions)} new session"
            f"{'' if len(sessions) == 1 else 's'} added* — "
            f"{title or 'IMAX Melbourne'}\n\n" + "\n\n".join(blocks))
    if not notify.send(text, disable_preview=False):
        health.log_error(f"could not deliver new-session alert: {text[:120]}")


def alert(new: dict[str, set[str]], snap: int | None = None,
          *, replay: bool = False, title: str | None = None,
          zone: Zone | None = None) -> None:
    total = sum(len(v) for v in new.values())
    together = zone.together if zone else 1
    blocks = [_describe(sid, seats, snap, together) for sid, seats in new.items()]
    if replay:
        # A replay must never be mistaken for a live opening.
        head = (f"🧪 *Test alert (replay of snapshot #{snap})* — this is what a real "
                f"one looks like. These seats are not necessarily still free.\n\n")
        tail = ""
    else:
        where = " in your zone" if zone else ""
        head = (f"🎟 *{total} seat{'' if total == 1 else 's'} just opened{where}* — "
                f"{title or 'IMAX Melbourne'}\n\n")
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
    parser.add_argument("--full", action="store_true",
                        help="sweep every date, ignoring each film's within_days")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--film-id", default=None,
                        help="watch just this film, ignoring films.toml")
    parser.add_argument("--replay", type=int, metavar="SNAPSHOT",
                        help="re-send the alert for a past snapshot, to verify "
                             "formatting and delivery without waiting for a release")
    args = parser.parse_args()

    health.claim_single_instance()
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

    # Guarded: a malformed films.toml must alert, not crash into a log nobody reads.
    with health.guard("config"):
        films = ([Film(id=args.film_id, name=args.film_id)] if args.film_id
                 else load_films())
    if not films:
        # Every film disabled is a deliberate state, not a fault: say so and
        # exit cleanly rather than alerting about having nothing to do.
        print("no films enabled in films.toml — nothing to monitor")
        health.write_heartbeat("ok", showtimes=0, bookable=0, films=0)
        return
    started = time.monotonic()
    totals, failed, costs = [], [], []
    for film in films:
        film_started = time.monotonic()
        try:
            summary = sweep_one(film, args)
            totals.append((film, summary))
            costs.append({"id": film.id, "name": film.name,
                          "seconds": round(time.monotonic() - film_started, 1),
                          "requests": summary.get("requests"),
                          "throttled": summary.get("throttled_requests")})
        except SystemExit:
            # guard() has already reported it and exits the process; letting that
            # propagate would leave every later film unchecked.
            failed.append(film)

    for film, summary in totals:
        report_film(film, summary, args)
    if not args.no_report:
        report.main()

    # Measure only a run shaped like the scheduled jobs. Dry runs and one-off
    # overrides cost something different and would muddy the history.
    if totals and not failed and not _adhoc(args):
        timing.record_and_check("full" if args.full else "watch",
                                time.monotonic() - started, costs)

    alive = ", ".join(
        f"{f.name}: {_idle_reason(s)}" if s.get("nothing_in_scope")
        else f"{f.name}: {s['bookable_total']}" for f, s in totals)
    if totals:
        health.maybe_send_digest(
            f"Bookable seats — {alive}. Monitor running normally. "
            f"{timing.summary_line()}".rstrip())
    if failed:
        raise SystemExit(1)      # after the films that did work have been reported


def _adhoc(args) -> bool:
    """A run the scheduled jobs would not make: a test or a one-off override."""
    return bool(args.dry_run or args.film_id or args.within_days is not None)


def sweep_one(film: Film, args) -> dict:
    """One film's sweep. Each gets its own guard so one failing does not stop
    the rest of the watch list being checked."""
    # --full is the whole schedule. An explicit --within-days wins over a film's
    # own window; otherwise each film uses the window in films.toml.
    if args.full:
        within = None
    else:
        within = args.within_days if args.within_days is not None else film.within_days
    with health.guard(f"sweep:{film.id}"):
        return asyncio.run(sweep_and_persist(
            film.id, verbose=False, within_days=within, lead_days=film.lead_days))


def _idle_reason(summary: dict) -> str:
    """Why a film was not swept, in words that say when it will be."""
    if summary.get("wakes_on"):
        return (f"not yet showing - first session {summary['opens_on']} "
                f"({summary['days_away']} days away); watching starts "
                f"{summary['wakes_on']}")
    if summary.get("opens_on"):
        return (f"nothing within its window - first session {summary['opens_on']} "
                f"({summary['days_away']} days away)")
    return "no sessions on sale yet"


def report_film(film: Film, summary: dict, args) -> None:
    if summary.get("nothing_in_scope"):
        print(f"[{film.name}] {_idle_reason(summary)}")
        return

    snaps = store.latest_snapshots(1, film.id)
    snap = snaps[0]["id"] if snaps else None
    # Validated against the real seat layout, and ignored (loudly) if it can
    # never match: a typo should cost noise, not silence.
    zone = effective(film.zone, film.id, film.name)

    fresh_sessions = find_new_sessions(film.id, zone)
    if fresh_sessions:
        print(f"[{film.name}] {len(fresh_sessions)} newly added session(s) with seats free")
        if args.dry_run:
            for r in fresh_sessions:
                print(f"  {r['starts_at']}  {r['bookable']}/{r['total']} free")
        else:
            alert_new_sessions(fresh_sessions, film.name, zone)

    new = find_new_seats(film.id, zone)

    if args.force_alert and not new and snap is not None:
        # Test the delivery path against whatever is genuinely free right now.
        new = store.bookable_seats_in(snap)

    if new:
        total = sum(len(v) for v in new.values())
        label = " in your zone" if zone else ""
        print(f"[{film.name}] {total} newly bookable seat(s){label} "
              f"across {len(new)} session(s)")
        if args.dry_run:
            for sid, seats in new.items():
                print("  " + _describe(sid, seats, snap,
                                       zone.together if zone else 1).replace("\n", " | "))
        else:
            alert(new, snap, title=film.name, zone=zone)
    else:
        print(f"[{film.name}] no change — {summary['bookable_total']} bookable "
              f"seat(s) across {summary['showtimes']} session(s)")


if __name__ == "__main__":
    main()
