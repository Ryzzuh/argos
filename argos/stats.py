"""Where the seats actually were.

Summarises every general-admission seat the monitor has ever found free, by row
and by position within the row. Used to keep the write-up's figures honest: they
drift every time a sweep finds something new.
"""
from __future__ import annotations

import json
from collections import Counter

from . import store
from .config import DATA


def _row_extents(layout: dict) -> tuple[dict, list[str]]:
    """Column range per row, so 'middle third' means middle third of that row."""
    extents, labels = {}, []
    for area in layout["areas"]:
        for row in area["rows"]:
            seats = row.get("seats") or []
            if not seats:
                continue
            cols = [s["position"]["columnNumber"] for s in seats]
            extents[(area["number"], row["number"])] = (min(cols), max(cols))
            label = seats[0].get("rowLabel") or row.get("label")
            if label:
                labels.append(label)
    return extents, sorted(set(labels))


def main() -> None:
    caches = sorted(DATA.glob("seat-layout-*.json"))
    if not caches:
        raise SystemExit("no cached seat layout — run a sweep first")
    blob = json.loads(caches[0].read_text())
    extents, all_rows = _row_extents(blob.get("seatLayout", blob))

    with store.connect() as conn:
        seats = conn.execute(
            "SELECT DISTINCT showtime_id, seat_id, row_label FROM free_seats"
            " WHERE seat_type = 'Normal'").fetchall()
        sweeps = conn.execute(
            "SELECT COUNT(*) n, SUM(bookable_total = 0) empty FROM snapshots").fetchone()

    if not seats:
        print("no bookable seats found yet")
        return

    per_row, position = Counter(), Counter()
    for s in seats:
        area, row, col = (int(p) for p in s["seat_id"].split("_"))
        per_row[s["row_label"]] += 1
        lo, hi = extents.get((area, row), (col, col))
        frac = (col - lo) / max(hi - lo, 1)
        position["middle third" if 1 / 3 <= frac <= 2 / 3 else "outer thirds"] += 1

    total = len(seats)
    widest = max(per_row.values())
    print(f"{total} distinct bookable seats found across "
          f"{sweeps['n']} sweeps ({sweeps['empty']} of them empty)\n")
    print("by row (A is closest to the screen):")
    for row in all_rows:
        n = per_row.get(row, 0)
        bar = "█" * round(n / widest * 34) if n else "·"
        print(f"  {row}: {n:>3}  {bar}")

    never = [r for r in all_rows if not per_row.get(r)]
    if never:
        print(f"\nnever released a seat: {' '.join(never)}")

    print("\nposition within the row:")
    for key, n in position.most_common():
        print(f"  {key:<14} {n:>3}  ({n / total * 100:.0f}%)")


if __name__ == "__main__":
    main()
