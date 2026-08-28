"""Join seat availability against the seat layout, and notice when the shape
of the data changes.

The availability endpoint returns only `{seatId, status}`. Everything a human
needs — row letter, seat number, whether it is a real seat or a wheelchair
space — lives in the seat layout, keyed by the same id (e.g. "1_1_2").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import (ACCESSIBLE_TYPES, BOOKABLE_TYPES, EXPECTED_LAYOUT_ID,
                     EXPECTED_SEAT_COUNT, KNOWN_SEAT_TYPES, KNOWN_STATUSES, TZ)
from .health import SchemaDrift


@dataclass(frozen=True)
class Seat:
    seat_id: str
    row_label: str
    seat_label: str
    seat_type: str
    area: str
    row_number: int
    column_number: int

    @property
    def is_bookable(self) -> bool:
        """A seat an ordinary punter can actually buy."""
        return self.seat_type in BOOKABLE_TYPES

    @property
    def name(self) -> str:
        base = f"{self.row_label}{self.seat_label}"
        if self.seat_type in ACCESSIBLE_TYPES:
            return f"{base} ({self.seat_type.lower()})"
        if self.area != "Standard":
            return f"{base} ({self.area})"
        return base


@dataclass
class SeatIndex:
    layout_id: str
    seats: dict[str, Seat]
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.seats)

    def breakdown(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for seat in self.seats.values():
            counts[f"{seat.seat_type}/{seat.area}"] = counts.get(
                f"{seat.seat_type}/{seat.area}", 0) + 1
        return dict(sorted(counts.items()))


def build_seat_index(layout: dict) -> SeatIndex:
    seats: dict[str, Seat] = {}
    warnings: list[str] = []

    for area in layout.get("areas", []):
        area_name = (area.get("name") or {}).get("text") or f"Area {area.get('number')}"
        for row in area.get("rows", []):
            for seat in row.get("seats") or []:
                pos = seat.get("position") or {}
                seat_type = seat.get("type") or "Unknown"
                if seat_type not in KNOWN_SEAT_TYPES:
                    warnings.append(f"unrecognised seat type {seat_type!r}")
                seats[seat["id"]] = Seat(
                    seat_id=seat["id"],
                    row_label=seat.get("rowLabel") or row.get("label") or "?",
                    seat_label=seat.get("label") or "?",
                    seat_type=seat_type,
                    area=area_name,
                    row_number=pos.get("rowNumber", 0),
                    column_number=pos.get("columnNumber", 0),
                )

    if not seats:
        raise SchemaDrift(f"seat layout {layout.get('id')} contains no seats")

    layout_id = layout.get("id", "?")
    if layout_id != EXPECTED_LAYOUT_ID:
        warnings.append(f"layout is {layout_id}, expected {EXPECTED_LAYOUT_ID}")
    if len(seats) != EXPECTED_SEAT_COUNT:
        warnings.append(f"{len(seats)} seats, expected {EXPECTED_SEAT_COUNT}")

    return SeatIndex(layout_id=layout_id, seats=seats,
                     warnings=sorted(set(warnings)))


@dataclass
class ShowtimeReport:
    showtime_id: str
    business_date: str
    starts_at: datetime
    screen_id: str
    seat_layout_id: str
    is_sold_out_flag: bool
    free_bookable: list[Seat]
    free_accessible: list[Seat]
    house: int
    sold: int
    total: int
    warnings: list[str] = field(default_factory=list)

    @property
    def bookable_count(self) -> int:
        return len(self.free_bookable)

    @property
    def local_time(self) -> str:
        return self.starts_at.astimezone(TZ).strftime("%-I:%M%p").lower()

    @property
    def local_day(self) -> str:
        return self.starts_at.astimezone(TZ).strftime("%a %-d %b")

    def seat_names(self) -> str:
        return ", ".join(s.name for s in sorted(
            self.free_bookable, key=lambda s: (s.row_label, s.column_number)))


def build_report(showtime: dict, availability: list[dict],
                 index: SeatIndex) -> ShowtimeReport:
    """Combine one showtime's availability with the layout."""
    free_bookable: list[Seat] = []
    free_accessible: list[Seat] = []
    house = sold = 0
    warnings: list[str] = []
    unmatched = 0

    for entry in availability:
        status = entry.get("status")
        if status not in KNOWN_STATUSES:
            warnings.append(f"unrecognised seat status {status!r}")

        seat = index.seats.get(entry.get("seatId"))
        if seat is None:
            unmatched += 1
            continue

        if status == "Available":
            (free_bookable if seat.is_bookable else free_accessible).append(seat)
        elif status == "House":
            house += 1
        elif status == "Sold":
            sold += 1

    if availability and unmatched == len(availability):
        raise SchemaDrift(
            f"none of {len(availability)} seat ids for {showtime['id']} matched "
            f"layout {index.layout_id} — the join key has changed"
        )
    if unmatched:
        warnings.append(f"{unmatched} seat id(s) missing from the layout")

    schedule = showtime.get("schedule") or {}
    return ShowtimeReport(
        showtime_id=showtime["id"],
        business_date=schedule.get("businessDate", ""),
        starts_at=datetime.fromisoformat(schedule["startsAt"]),
        screen_id=showtime.get("screenId", ""),
        seat_layout_id=showtime.get("seatLayoutId", ""),
        is_sold_out_flag=bool(showtime.get("isSoldOut")),
        free_bookable=free_bookable,
        free_accessible=free_accessible,
        house=house,
        sold=sold,
        total=len(availability),
        warnings=sorted(set(warnings)),
    )
