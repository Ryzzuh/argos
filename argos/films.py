"""The watch list.

Which films to monitor lives in films.toml rather than in code, so changing it
does not mean editing a module and reloading three launchd agents. Read-only:
you edit the file, nothing writes it.
"""
from __future__ import annotations

import argparse
import asyncio
import tomllib
from dataclasses import dataclass

from .config import FILM_ID, FILMS_FILE
from . import zone as zone_mod
from .health import ArgosError
from .zone import Zone

# A full sweep is roughly this many requests per film, against a limiter that
# allows 41 per rolling minute. Used to warn when a watch list cannot finish
# inside its own schedule.
REQUESTS_PER_FILM = 80
WINDOW_LIMIT = 35


@dataclass(frozen=True)
class Film:
    id: str
    name: str
    within_days: int | None = 14
    lead_days: int | None = None   # None: watch from the moment sessions exist
    enabled: bool = True
    zone: Zone | None = None     # None: every bookable seat alerts

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"


FILM_KEYS = {"id", "name", "enabled", "within_days", "lead_days", "seats"}


def _reject_unknown(table: dict, allowed: set, where: str) -> None:
    """A misspelt key that is silently ignored is a setting that never applies:
    `enable = false` would leave a film running and tell nobody."""
    if unknown := sorted(set(table) - allowed):
        raise ArgosError(f"{where}: unknown setting(s) {unknown}; "
                         f"allowed are {sorted(allowed)}")


def _window(value, where: str) -> int | None:
    """within_days as the sweep wants it: a number of days, or None for no limit.

    TOML has no null, so "no limit" is spelled `"all"` or `0`. Anything else that
    is not a positive whole number is an error rather than a silent default: a
    window that quietly filters out every session looks exactly like a sold-out
    season.
    """
    if value in ("all", 0):
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise ArgosError(f'{where}: within_days must be a number of days, or "all" '
                     f"(or 0) for no limit - got {value!r}")


def _lead(value, where: str) -> int | None:
    """lead_days: begin watching this many days before the first session.

    Absent means watch from the moment sessions exist. Zero means wait until the
    day of the first session. Anything else that is not a whole number of days is
    an error, since a lead that silently never wakes looks like a quiet film.
    """
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise ArgosError(f"{where}: lead_days must be a whole number of days, 0 or more "
                     f"- got {value!r}")


def load(path=None, *, include_disabled: bool = False) -> list[Film]:
    """The watch list.

    Disabled films are parked, not deleted: they stay in the file with their
    settings intact and are simply skipped. Pass include_disabled to see them,
    which is what the listing does.
    """
    path = path or FILMS_FILE
    if not path.exists():
        return [Film(id=FILM_ID, name=FILM_ID)]

    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ArgosError(f"{path.name} is not valid TOML: {exc}") from exc

    _reject_unknown(raw, {"film", "defaults"}, path.name)
    defaults = raw.get("defaults", {})
    _reject_unknown(defaults, {"seats"}, f"[defaults] in {path.name}")
    default_seats = defaults.get("seats", {})

    films = []
    for entry in raw.get("film", []):
        if not entry.get("id"):
            raise ArgosError(f"{path.name} has a [[film]] entry with no id")
        label = f"film {entry['id']}"
        _reject_unknown(entry, FILM_KEYS, label)
        films.append(Film(
            id=entry["id"],
            name=entry.get("name") or entry["id"],
            within_days=_window(entry.get("within_days", 14), label),
            lead_days=_lead(entry.get("lead_days"), label),
            enabled=bool(entry.get("enabled", True)),
            zone=zone_mod.resolve(default_seats, entry.get("seats"),
                                  f"{label} seats"),
        ))

    if not films:
        raise ArgosError(
            f"{path.name} lists no films. Add a [[film]] entry, or delete the "
            "file to fall back to IMAX_FILM_ID."
        )
    return films if include_disabled else [f for f in films if f.enabled]


def cadence_warning(films: list[Film], interval_minutes: int = 10) -> str | None:
    """Whether this many films can be swept inside the watch interval."""
    windows = (len(films) * REQUESTS_PER_FILM) / WINDOW_LIMIT
    if windows > interval_minutes:
        return (f"{len(films)} films need roughly {windows:.0f} minutes per full "
                f"sweep against the API's rate limit, which does not fit a "
                f"{interval_minutes}-minute schedule. Reduce the list, or widen "
                f"the interval in bin/install-agents.sh.")
    return None


async def _list_available() -> None:
    from .api import Ocapi
    async with Ocapi() as api:
        films = await api.films()
    watched = {f.id for f in load()}   # enabled only
    print(f"{len(films)} films at this site (* = currently watched):\n")
    for f in sorted(films, key=lambda x: x["title"]["text"]):
        mark = "*" if f["id"] in watched else " "
        print(f" {mark} {f['id']}  {f['title']['text']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Show the watch list")
    parser.add_argument("--available", action="store_true",
                        help="list every film at the site, to find an id")
    args = parser.parse_args()

    if args.available:
        asyncio.run(_list_available())
        return

    films = load(include_disabled=True)
    active = [f for f in films if f.enabled]
    print(f"{len(active)} of {len(films)} film(s) enabled in {FILMS_FILE.name}:")
    for f in films:
        scope = f"next {f.within_days} days" if f.within_days else "all dates"
        if f.lead_days is not None:
            scope += f", starts {f.lead_days} days before first session"
        mark = "on " if f.enabled else "off"
        print(f"  [{mark}] {f.id}  {f.name}  [{scope}]")
        print(f"          seats: {f.zone.describe() if f.zone else 'any bookable seat'}")
    if not active:
        print("\n! Every film is disabled — nothing will be monitored.")
    if warning := cadence_warning(active):
        print(f"\n! {warning}")


if __name__ == "__main__":
    main()
