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
from datetime import datetime, timedelta, timezone

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


async def _load_showtimes(api: Ocapi, dates: list[str], film_id: str,
                          *, max_age_hours: float = 6.0,
                          force: bool = False) -> list[dict]:
    """Showtimes cost one request per date, so cache them between runs.

    The date list is a single request and acts as the cache key, so a new
    screening *date* is picked up on the very next sweep. A new screening on a
    date that already exists does not change that key, which is why full sweeps
    pass force=True and re-read the schedule outright.
    """
    cache = DATA / f"showtimes-{film_id}.json"
    if not force:
        try:
            blob = json.loads(cache.read_text())
            fresh = (time.time() - blob["fetched_at"]) < max_age_hours * 3600
            if fresh and blob["dates"] == dates:
                return blob["showtimes"]
        except Exception:
            pass

    groups = await asyncio.gather(*(api.showtimes_on(d, film_id) for d in dates))
    showtimes = [s for group in groups for s in group]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(
        {"fetched_at": time.time(), "dates": dates, "showtimes": showtimes}))
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
                within_days: int | None = None) -> dict:
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
            raise SchemaDrift(
                f"{title} ({film_id}) returned zero screening dates. "
                "That is either the end of the season or a broken query — "
                "not something to report as 'no seats'."
            )

        # A narrow watchlist run trusts the cache to stay inside the rate-limit
        # budget; a full sweep always re-reads, so an added screening surfaces
        # within one full-sweep interval.
        showtimes = await _load_showtimes(api, dates, film_id,
                                          force=within_days is None)
        if not showtimes:
            raise SchemaDrift(
                f"{len(dates)} screening dates but zero showtimes for {film_id}")

        # A watchlist run checks only the near sessions. The API allows ~41
        # requests per rolling minute, so a narrow sweep finishes inside one
        # window and can therefore run every few minutes.
        if within_days is not None:
            cutoff = datetime.now(TZ) + timedelta(days=within_days)
            showtimes = [s for s in showtimes
                         if datetime.fromisoformat(s["schedule"]["startsAt"]) <= cutoff]
            if not showtimes:
                raise SchemaDrift(
                    f"no sessions within {within_days} days — nothing to watch")

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
        throttled = api.throttled

    return _finish(results, indexes, dates, title, film_id, throttled)


def _finish(results, indexes, dates, title, film_id, throttled):
    """Validate a sweep's results and roll them up. Shared by the local sweep
    and the cloud runner, which persist very different things."""
    reports = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    if failures and len(failures) / len(results) > MAX_FAILED_FRACTION:
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
        "warnings": drift,
        "transient": transient,
    }
    return reports, summary


def persist(reports, summary):
    """Local mode: write the snapshot to SQLite and settle the alert state."""
    summary = dict(summary)
    summary["snapshot_id"] = store.save_snapshot(reports, partial=summary["partial"])
    (DATA / "last-sweep.json").write_text(json.dumps(summary, indent=2))

    if drift := summary["warnings"]:
        health.report_failure(
            "schema",
            "Sweep completed but the data did not look the way it should:\n"
            + "\n".join(f"• {w}" for w in drift),
        )
    else:
        health.report_recovery("schema")

    # Only worth a human's attention once a meaningful slice of the schedule is
    # going unchecked; below that it is noise and goes to the log alone.
    for note in summary.get("transient", []):
        health.log_error(f"[transient] {note}")
    if summary.get("failed_share", 0) > NOISY_FAILURE_SHARE:
        health.report_failure(
            "coverage",
            f"{summary['transient'][0]} — enough of the schedule went unchecked "
            "that a release could have been missed.",
        )
    else:
        health.report_recovery("coverage")
    health.report_recovery("sweep")
    health.report_recovery("token")

    health.write_heartbeat(
        "ok", snapshot_id=summary["snapshot_id"], showtimes=len(reports),
        bookable=summary["bookable_total"],
    )
    return summary


async def sweep_and_persist(film_id: str = FILM_ID, *, verbose: bool = True,
                            within_days: int | None = None) -> dict:
    reports, summary = await sweep(film_id, verbose=verbose, within_days=within_days)
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
