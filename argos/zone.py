"""Where you want to sit, and how many of you there are.

A zone narrows alerts to the seats you would actually take: rows from some
letter back, a band of seat numbers, and optionally a minimum number of free
seats side by side. It is applied when alerting, never when recording, so the
database keeps every seat and a zone can be changed or re-tested against history
without touching the data.

    [defaults.seats]
    min_row  = "D"          # this row or further back; A is nearest the screen
    cols     = [18, 28]     # inclusive band of seat numbers, 11 wide
    together = 2            # at least this many adjacent free seats

Two details of this auditorium shape the code:

* Rows are compared as letters. There is no row I, so counting rows by letter
  arithmetic goes wrong after H; comparing the letters themselves does not.
  The venue's own `rowNumber` runs back to front (M is 1, A is 12), which is why
  it is not used here at all.
* Rows A-L share one absolute column grid, centred on column 23, and there the
  printed seat number equals the column. Row M does not: its printed numbers
  drift from the grid by up to three. A zone is expressed in grid columns, so
  for row M it is approximate. Seats are also keyed by area, because row M is
  split into Standard and Premium blocks that reuse some column numbers.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from itertools import groupby
from typing import Iterable, Mapping

from .config import DATA
from .health import ArgosError

ALLOWED = {"enabled", "min_row", "cols", "together"}


class ZoneError(ArgosError):
    signature = "config"


def parse_seat_id(seat_id: str) -> tuple[int, int, int]:
    """'1_8_23' -> (area 1, rowNumber 8, column 23)."""
    area, row, col = (int(part) for part in seat_id.split("_"))
    return area, row, col


@dataclass(frozen=True)
class Zone:
    min_row: str | None = None
    cols: tuple[int, int] | None = None
    together: int = 1

    @property
    def restricts(self) -> bool:
        return self.min_row is not None or self.cols is not None or self.together > 1

    def in_zone(self, row_label: str, col: int) -> bool:
        if self.min_row is not None and row_label < self.min_row:
            return False
        if self.cols is not None and not (self.cols[0] <= col <= self.cols[1]):
            return False
        return True

    def qualifying(self, seats: Iterable[tuple]) -> set[str]:
        """The seat ids, out of these free seats, that meet the zone.

        `seats` yields (seat_id, row_label, ...). A seat qualifies if it is
        inside the band and, when `together` is more than one, belongs to a run
        of at least that many adjacent free seats that are all inside it. A pair
        straddling the edge of the band does not count: the zone is the zone.
        """
        inside = []
        for seat_id, row_label, *_ in seats:
            area, _, col = parse_seat_id(seat_id)
            if self.in_zone(row_label, col):
                inside.append((area, row_label, col, seat_id))
        if self.together <= 1:
            return {s[3] for s in inside}

        qualified: set[str] = set()
        inside.sort()
        for _, row_seats in groupby(inside, key=lambda s: (s[0], s[1])):
            run: list = []
            for seat in row_seats:
                if run and seat[2] == run[-1][2] + 1:
                    run.append(seat)
                    continue
                if len(run) >= self.together:
                    qualified.update(s[3] for s in run)
                run = [seat]
            if len(run) >= self.together:
                qualified.update(s[3] for s in run)
        return qualified

    def describe(self) -> str:
        parts = []
        if self.min_row:
            parts.append(f"row {self.min_row} or further back")
        if self.cols:
            parts.append(f"seats {self.cols[0]}-{self.cols[1]} "
                         f"({self.cols[1] - self.cols[0] + 1} wide)")
        if self.together > 1:
            parts.append(f"{self.together} together")
        return ", ".join(parts) or "anywhere"


def qualify(zone: Zone | None, seats: Iterable[tuple]) -> set[str]:
    """Qualifying seat ids, or every seat when there is no zone."""
    if zone is None:
        return {seat_id for seat_id, *_ in seats}
    return zone.qualifying(seats)


def newly_qualifying(previous: Mapping[str, list], current: Mapping[str, list],
                     zone: Zone | None, comparable: set[str]) -> dict[str, set[str]]:
    """Seats that qualify now and did not before, per showtime.

    The zone is applied to *both* snapshots, which is what makes it safe to
    change. Filtering only the current one would make every seat a widened zone
    newly admits look as though it had just been released.
    """
    fresh_by_showtime: dict[str, set[str]] = {}
    for showtime_id, seats in current.items():
        if showtime_id not in comparable:
            continue
        fresh = qualify(zone, seats) - qualify(zone, previous.get(showtime_id, []))
        if fresh:
            fresh_by_showtime[showtime_id] = fresh
    return fresh_by_showtime


def format_runs(seats: Iterable[tuple]) -> str:
    """'E22-E23, G4' from (seat_id, row_label, seat_label) rows.

    Adjacent seats are shown as a range so a pair reads as a pair.
    """
    keyed = sorted(
        ((*parse_seat_id(seat_id), row, label) for seat_id, row, label in seats),
        key=lambda s: (s[0], s[3], s[2]))
    out = []
    for _, group in groupby(keyed, key=lambda s: (s[0], s[3])):
        run: list = []
        for seat in group:
            if run and seat[2] == run[-1][2] + 1:
                run.append(seat)
                continue
            if run:
                out.append(run)
            run = [seat]
        if run:
            out.append(run)
    return ", ".join(
        f"{r[0][3]}{r[0][4]}" if len(r) == 1 else f"{r[0][3]}{r[0][4]}–{r[-1][3]}{r[-1][4]}"
        for r in out)


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

def parse(table: Mapping, where: str) -> Zone | None:
    """Build a Zone from a TOML table. None means 'no restriction'."""
    unknown = sorted(set(table) - ALLOWED)
    if unknown:
        # A misspelt key that is silently ignored is a zone that never applies.
        raise ZoneError(f"{where}: unknown setting(s) {unknown}; "
                        f"allowed are {sorted(ALLOWED)}")
    if not table.get("enabled", True):
        return None

    min_row = table.get("min_row")
    if min_row is not None:
        if not (isinstance(min_row, str) and len(min_row) == 1 and min_row.isalpha()):
            raise ZoneError(f'{where}: min_row must be a single row letter such as "D"')
        min_row = min_row.upper()

    cols = table.get("cols")
    if cols is not None:
        ints = (isinstance(cols, list) and len(cols) == 2 and
                all(isinstance(c, int) and not isinstance(c, bool) for c in cols))
        if not ints or cols[0] < 1 or cols[0] > cols[1]:
            raise ZoneError(f"{where}: cols must be [low, high] with 1 <= low <= high, "
                            f"for example [18, 28]")
        cols = (cols[0], cols[1])

    together = table.get("together", 1)
    if not isinstance(together, int) or isinstance(together, bool) or together < 1:
        raise ZoneError(f"{where}: together must be a whole number, 1 or more")
    if cols is not None and together > cols[1] - cols[0] + 1:
        raise ZoneError(f"{where}: together = {together} cannot fit in a band only "
                        f"{cols[1] - cols[0] + 1} seats wide")

    zone = Zone(min_row=min_row, cols=cols, together=together)
    return zone if zone.restricts else None


def resolve(defaults: Mapping, film: Mapping | None, where: str) -> Zone | None:
    """A film's zone: the global default, overridden field by field."""
    merged = {**defaults, **(film or {})}
    # A film that supplies its own seats table is opting in, even if the
    # default was switched off.
    if film:
        merged["enabled"] = film.get("enabled", True)
    return parse(merged, where)


# --------------------------------------------------------------------------
# checking a zone against the real auditorium
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Geometry:
    """Every seat in the auditorium: (seat_id, row_label, seat_type)."""
    seats: tuple[tuple[str, str, str], ...]

    @property
    def rows(self) -> set[str]:
        return {row for _, row, _ in self.seats}

    @property
    def cols(self) -> tuple[int, int]:
        cols = [parse_seat_id(seat_id)[2] for seat_id, _, _ in self.seats]
        return min(cols), max(cols)

    def normal(self) -> list[tuple[str, str]]:
        return [(seat_id, row) for seat_id, row, kind in self.seats if kind == "Normal"]


def load_geometry() -> Geometry | None:
    """From the cached seat layout, which exists once a sweep has run."""
    for path in sorted(DATA.glob("seat-layout-*.json")):
        try:
            blob = json.loads(path.read_text())
            layout = blob.get("seatLayout", blob)
            return Geometry(tuple(
                (seat["id"], seat["rowLabel"], seat["type"])
                for area in layout["areas"] for row in area["rows"]
                for seat in (row.get("seats") or [])))
        except Exception:
            continue
    return None


def validate(zone: Zone, geometry: Geometry) -> str | None:
    """Why this zone can never match anything here, or None if it can.

    A zone that matches no seat is the quietest possible failure: the monitor
    runs, finds nothing, and never alerts. So it is checked against the room.
    """
    if zone.min_row and zone.min_row not in geometry.rows:
        return (f'min_row "{zone.min_row}" is not a row in this auditorium '
                f"(rows are {', '.join(sorted(geometry.rows))})")
    lo, hi = geometry.cols
    if zone.cols and (zone.cols[1] < lo or zone.cols[0] > hi):
        return f"cols {list(zone.cols)} lie outside the seat grid, which spans {lo}-{hi}"
    if not zone.qualifying(geometry.normal()):
        return ("no seat in the auditorium could ever satisfy this zone, "
                "even with every seat free")
    return None


def coverage(zone: Zone, geometry: Geometry) -> tuple[int, int]:
    """(seats that could qualify if all were free, bookable seats in total)."""
    normal = geometry.normal()
    return len(zone.qualifying(normal)), len(normal)


def effective(zone: Zone | None, film_id: str, film_name: str,
              *, alert: bool = True) -> Zone | None:
    """The zone to apply right now, failing open.

    An invalid zone is reported and then ignored, so a typo costs you noise
    rather than silence. Going quiet is the one thing this monitor must not do.
    """
    from . import health

    if zone is None:
        return None
    geometry = load_geometry()
    problem = validate(zone, geometry) if geometry else None
    if problem:
        if alert:
            health.report_failure(
                f"config:{film_id}",
                f"{film_name}: {problem}.\n\nAlerting on every bookable seat until "
                "this is fixed, rather than on none.")
        return None
    if alert:
        health.report_recovery(f"config:{film_id}")
    return zone


# --------------------------------------------------------------------------
# what a zone would have done
# --------------------------------------------------------------------------

def simulate(film_id: str, zone: Zone | None) -> dict:
    """Replay this film's recorded history through the alert logic."""
    from . import store

    snaps = list(reversed(store.latest_snapshots(100_000, film_id)))
    previous: dict = {}
    previous_id = None
    events, seats_alerted, examples = 0, 0, []
    for snap in snaps:
        current = store.bookable_details_in(snap["id"])
        if previous_id is not None:
            comparable = {r["showtime_id"] for r in store.snapshot_rows(previous_id)}
            fresh = newly_qualifying(previous, current, zone, comparable)
            if fresh:
                events += 1
                seats_alerted += sum(len(v) for v in fresh.values())
                if len(examples) < 5:
                    sid, ids = next(iter(fresh.items()))
                    labels = [s for s in current[sid] if s[0] in ids]
                    examples.append((snap["taken_at"][:16], format_runs(labels)))
        previous, previous_id = current, snap["id"]
    return {"sweeps": len(snaps), "alerts": events, "seats": seats_alerted,
            "examples": examples}


def main() -> None:
    from .films import load

    parser = argparse.ArgumentParser(description="Check each film's seat zone")
    parser.add_argument("--no-history", action="store_true",
                        help="skip replaying recorded sweeps")
    args = parser.parse_args()

    geometry = load_geometry()
    for film in load():
        print(f"\n{film.name}  ({film.id})")
        if film.zone is None:
            print("  no zone: every bookable seat alerts")
            continue
        print(f"  zone: {film.zone.describe()}")
        if geometry:
            if problem := validate(film.zone, geometry):
                print(f"  ! INVALID: {problem}")
                continue
            reachable, total = coverage(film.zone, geometry)
            print(f"  covers {reachable} of {total} bookable seats")
        else:
            print("  (no cached seat layout yet - run a sweep to validate)")
        if args.no_history:
            continue
        before, after = simulate(film.id, None), simulate(film.id, film.zone)
        print(f"  history ({after['sweeps']} sweeps): {before['alerts']} alerts "
              f"unfiltered, {after['alerts']} with this zone")
        for taken, seats in after["examples"]:
            print(f"    {taken}  {seats}")


if __name__ == "__main__":
    main()
