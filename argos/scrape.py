"""One full sweep: every session for the film, with real seat availability.

Deliberately computes sold-out from the seat data rather than the showtime's
own `isSoldOut` flag. That flag is wrong: 34 of 78 sessions claim they are not
sold out while having nothing free but wheelchair spaces.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import date, datetime, timedelta, timezone

from . import health, store
from .api import Ocapi
from .config import DATA, FILM_ID, TZ
from .health import SchemaDrift, SweepError
from .model import build_report, build_seat_index

# Above this share of failed showtime lookups, the sweep is not trustworthy.
MAX_FAILED_FRACTION = 0.2
# Below this share, skipped lookups are logged and not alerted on: a stray
# network blip is normal and does not compromise the sweep.
NOISY_FAILURE_SHARE = 0.1


def plan_dates(dates: list[str], within_days: int | None,
               today: date) -> tuple[list[str], str | None]:
    """The dates worth looking up, and the date the film first screens.

    Decided from the date list alone, which is one request, before a single
    showtime is fetched. A film whose first session is beyond the window then
    costs two requests per sweep (the film list and the date list) instead of one
    per date. Dates are ISO strings, so they compare correctly as text.
    """
    opens_on = min(dates) if dates else None
    if within_days is None:
        return list(dates), opens_on
    cutoff = (today + timedelta(days=within_days)).isoformat()
    return [d for d in dates if d <= cutoff], opens_on


async def _load_showtimes(api: Ocapi, dates: list[str], film_id: str,
                          *, listed: list[str] | None = None,
                          max_age_hours: float = 6.0,
                          force: bool = False) -> list[dict]:
    """Showtimes cost one request per date, so cache them per date.

    Per date rather than per request: a windowed run and a full run ask for
    different sets of dates, and a single cache keyed on the whole set would be
    invalidated by each in turn. A date seen for the first time is fetched on the
    spot, so a new screening *date* appears on the very next sweep. A new
    screening on a date already cached does not, which is why full sweeps pass
    force=True and re-read the schedule outright.
    """
    cache = DATA / f"showtimes-{film_id}.json"
    try:
        raw = json.loads(cache.read_text())
        # Keep only per-date entries. The format before per-date caching had a
        # list under "dates" and a float under "fetched_at", so none of it
        # survives this filter and it is simply refetched.
        entries = {d: e for d, e in raw.items()
                   if isinstance(e, dict) and "showtimes" in e and "fetched_at" in e}
    except Exception:
        entries = {}

    now = time.time()
    showtimes: list[dict] = []
    missing: list[str] = []
    for d in dates:
        entry = entries.get(d)
        fresh = entry and (now - entry["fetched_at"]) < max_age_hours * 3600
        if fresh and not force:
            showtimes.extend(entry["showtimes"])
        else:
            missing.append(d)

    if missing:
        groups = await asyncio.gather(*(api.showtimes_on(d, film_id) for d in missing))
        for d, group in zip(missing, groups):
            entries[d] = {"fetched_at": now, "showtimes": group}
            showtimes.extend(group)

    # Forget dates the API no longer lists, so the file cannot grow without end.
    keep = set(listed if listed is not None else dates)
    entries = {d: e for d, e in entries.items() if d in keep}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(entries))
    return showtimes


async def _load_layout(api: Ocapi, layout_id: str) -> dict:
    """Layouts change rarely and are ~80KB, so cache to disk."""
    cache = DATA / f"seat-layout-{layout_id}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            pass
    layout = await api.seat_layout(layout_id)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(layout))
    return layout


async def sweep(film_id: str = FILM_ID, *, verbose: bool = True,
                within_days: int | None = None,
                lead_days: int | None = None) -> dict:
    async with Ocapi() as api:
        films = await api.films()
        if not any(f.get("id") == film_id for f in films):
            titles = ", ".join(sorted(f["title"]["text"] for f in films)[:8])
            raise SchemaDrift(
                f"film {film_id} is no longer listed at IMAX Melbourne "
                f"({len(films)} films returned, e.g. {titles}). "
                "Either the season ended or the id changed."
            )
        title = next(f["title"]["text"] for f in films if f["id"] == film_id)

        dates = await api.screening_dates(film_id)
        if not dates:
            # A listed film with nothing on sale is a normal state, not a fault.
            # It only becomes suspicious if it had upcoming sessions we recorded
            # and they have vanished, which is either a pulled film or a broken
            # query, and that is worth being told about.
            if store.has_upcoming_sessions(film_id, datetime.now(TZ)):
                raise SchemaDrift(
                    f"{title} ({film_id}) had upcoming sessions and now lists "
                    "none. Either the film was pulled or the schedule query broke "
                    "- not something to report as 'no seats'.")
            if verbose:
                print(f"{title}: no sessions on sale yet")
            return [], _empty_summary(title, film_id, 0, reason="no sessions on sale",
                                      requests=api.requests)

        # Decide from the date list alone, before looking up any showtimes.
        today = datetime.now(TZ).date()
        in_scope, opens_on = plan_dates(dates, within_days, today)
        days_away = (date.fromisoformat(opens_on) - today).days

        # Dormant: the film has not started, and the caller does not want it
        # watched until its first session is close. Independent of the window, so
        # it also covers a film set to look at every date.
        dormant = lead_days is not None and days_away > lead_days
        if dormant or not in_scope:
            wakes_on = ((date.fromisoformat(opens_on) - timedelta(days=lead_days))
                        .isoformat() if dormant else None)
            if verbose:
                print(f"{title}: first session {opens_on} ({days_away} days away)"
                      + (f"; watching starts {wakes_on}" if dormant
                         else f", outside the {within_days}-day window"))
            return [], _empty_summary(title, film_id, len(dates), opens_on=opens_on,
                                      days_away=days_away, wakes_on=wakes_on,
                                      reason="not yet showing", requests=api.requests)

        # A narrow watchlist run trusts the cache to stay inside the rate-limit
        # budget; a full sweep always re-reads, so an added screening surfaces
        # within one full-sweep interval.
        showtimes = await _load_showtimes(api, in_scope, film_id, listed=dates,
                                          force=within_days is None)
        if not showtimes:
            raise SchemaDrift(
                f"{len(in_scope)} screening dates but zero showtimes for {film_id}")

        if within_days is not None:
            cutoff = datetime.now(TZ) + timedelta(days=within_days)
            showtimes = [s for s in showtimes
                         if datetime.fromisoformat(s["schedule"]["startsAt"]) <= cutoff]

        # Sessions that have already started cannot be booked.
        now = datetime.now(TZ)
        showtimes = [s for s in showtimes
                     if datetime.fromisoformat(s["schedule"]["startsAt"]) > now]

        layout_ids = {s.get("seatLayoutId") for s in showtimes if s.get("seatLayoutId")}
        indexes = {}
        for layout_id in layout_ids:
            indexes[layout_id] = build_seat_index(await _load_layout(api, layout_id))

        if verbose:
            print(f"{title}: {len(showtimes)} showtimes across {len(dates)} dates "
                  f"({', '.join(sorted(layout_ids))})")

        async def one(showtime):
            try:
                avail = await api.seat_availability(showtime["id"])
                return build_report(showtime, avail, indexes[showtime["seatLayoutId"]])
            except SchemaDrift:
                raise
            except Exception as exc:
                return exc

        results = await asyncio.gather(*(one(s) for s in showtimes))
        throttled, requests = api.throttled, api.requests

    return _finish(results, indexes, dates, title, film_id, throttled, requests)


def _empty_summary(title: str, film_id: str, dates: int, *, opens_on: str | None = None,
                   days_away: int | None = None, wakes_on: str | None = None,
                   reason: str = "", requests: int = 0) -> dict:
    """A sweep that legitimately had nothing in scope to check."""
    return {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "film_id": film_id, "title": title, "dates": dates,
        "showtimes": 0, "bookable_total": 0, "sessions_with_bookable": 0,
        "accessible_total": 0, "house_total": 0, "seat_total": 0,
        "breakdown": {}, "partial": False, "failed_share": 0.0,
        "throttled_requests": 0, "requests": requests, "warnings": [], "transient": [],
        "nothing_in_scope": True,
        "opens_on": opens_on, "days_away": days_away, "wakes_on": wakes_on,
        "reason": reason,
    }


def _finish(results, indexes, dates, title, film_id, throttled, requests=0):
    """Validate a sweep's results and roll them up. Shared by the local sweep
    and the cloud runner, which persist very different things."""
    reports = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    if results and failures and len(failures) / len(results) > MAX_FAILED_FRACTION:
        raise SweepError(
            f"{len(failures)}/{len(results)} showtime lookups failed — "
            f"refusing to report a partial picture. First: {failures[0]}"
        )

    # Two very different kinds of problem, deliberately kept apart. Drift means
    # the data no longer looks like what this code was written against and a
    # human should look. A skipped lookup is a network blip: the sweep is still
    # sound, and paging someone about it teaches them to ignore alerts.
    drift = sorted({w for idx in indexes.values() for w in idx.warnings}
                   | {w for r in reports for w in r.warnings})

    transient = []
    if failures:
        share = len(failures) / len(results)
        note = (f"{len(failures)} of {len(results)} showtime lookup(s) failed "
                f"and were skipped")
        transient.append(note)

    reports.sort(key=lambda r: r.starts_at)

    summary = {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "film_id": film_id,
        "title": title,
        "dates": len(dates),
        "showtimes": len(reports),
        "bookable_total": sum(r.bookable_count for r in reports),
        "sessions_with_bookable": sum(1 for r in reports if r.bookable_count),
        "accessible_total": sum(len(r.free_accessible) for r in reports),
        "house_total": sum(r.house for r in reports),
        "seat_total": next(iter(indexes.values())).total if indexes else 0,
        "breakdown": next(iter(indexes.values())).breakdown() if indexes else {},
        "partial": bool(failures),
        "failed_share": (len(failures) / len(results)) if results else 0.0,
        "throttled_requests": throttled,
        "requests": requests,
        "warnings": drift,
        "transient": transient,
    }
    return reports, summary


def persist(reports, summary):
    """Local mode: write the snapshot to SQLite and settle the alert state."""
    summary = dict(summary)
    if summary.get("nothing_in_scope"):
        # Nothing was checked, so there is nothing to snapshot or diff against.
        # A film that is simply not showing yet is a healthy outcome, so it also
        # clears any failure recorded while the question was still open.
        health.report_recovery(f"schema:{summary['film_id']}")
        health.report_recovery(f"sweep:{summary['film_id']}")
        health.write_heartbeat("ok", film_id=summary["film_id"], showtimes=0,
                               bookable=0)
        summary["snapshot_id"] = None
        return summary
    summary["snapshot_id"] = store.save_snapshot(
        reports, film_id=summary["film_id"], partial=summary["partial"])
    # Per film, plus the unsuffixed file so anything reading the old path still
    # sees the most recent sweep.
    blob = json.dumps(summary, indent=2)
    (DATA / f"last-sweep-{summary['film_id']}.json").write_text(blob)
    (DATA / "last-sweep.json").write_text(blob)

    film = summary["film_id"]
    if drift := summary["warnings"]:
        health.report_failure(
            f"schema:{film}",
            "Sweep completed but the data did not look the way it should:\n"
            + "\n".join(f"• {w}" for w in drift),
        )
    else:
        health.report_recovery(f"schema:{film}")

    # Only worth a human's attention once a meaningful slice of the schedule is
    # going unchecked; below that it is noise and goes to the log alone.
    for note in summary.get("transient", []):
        health.log_error(f"[transient] {note}")
    if summary.get("failed_share", 0) > NOISY_FAILURE_SHARE:
        health.report_failure(
            f"coverage:{film}",
            f"{summary['transient'][0]} — enough of the schedule went unchecked "
            "that a release could have been missed.",
        )
    else:
        health.report_recovery(f"coverage:{film}")
    health.report_recovery(f"sweep:{film}")
    health.report_recovery("token")

    health.write_heartbeat(
        "ok", snapshot_id=summary["snapshot_id"], film_id=film,
        showtimes=len(reports), bookable=summary["bookable_total"],
    )
    return summary


async def sweep_and_persist(film_id: str = FILM_ID, *, verbose: bool = True,
                            within_days: int | None = None,
                            lead_days: int | None = None) -> dict:
    reports, summary = await sweep(film_id, verbose=verbose, within_days=within_days,
                                   lead_days=lead_days)
    return persist(reports, summary)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep IMAX Melbourne seat availability")
    parser.add_argument("--once", action="store_true",
                        help="accepted for symmetry; a run is always one sweep")
    parser.add_argument("--film-id", default=FILM_ID)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--within-days", type=int, default=None,
                        help="only check sessions this many days out (watchlist mode)")
    args = parser.parse_args()

    with health.guard("sweep"):
        summary = asyncio.run(sweep_and_persist(
            args.film_id, verbose=not args.quiet, within_days=args.within_days))

    if not args.quiet:
        print(f"snapshot #{summary['snapshot_id']} at "
              f"{datetime.now(TZ):%a %d %b %H:%M}")
        print(f"  bookable seats free : {summary['bookable_total']} "
              f"across {summary['sessions_with_bookable']} session(s)")
        print(f"  accessible spaces   : {summary['accessible_total']}")
        print(f"  house-held seats    : {summary['house_total']}")
        print(f"  auditorium capacity : {summary['seat_total']} "
              f"({', '.join(f'{k}={v}' for k, v in summary['breakdown'].items())})")
        for warning in summary["warnings"]:
            print(f"  ! {warning}")


if __name__ == "__main__":
    main()
