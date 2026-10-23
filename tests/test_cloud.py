"""The stateless runner's diff honours the zone, and reads old saved state."""
import unittest

from argos.cloud import diff
from argos.zone import Zone


def info(*placed, with_rows=True):
    """placed: (row, col). Builds one showtime's saved state."""
    ids = [f"1_1_{col}" for _, col in placed]
    state = {"starts_at": "2026-10-25T18:30:00+11:00", "total": 459,
             "bookable": sorted(ids),
             "labels": {f"1_1_{c}": f"{r}{c}" for r, c in placed}}
    if with_rows:
        state["rows"] = {f"1_1_{c}": r for r, c in placed}
    return state


def snap(**showtimes):
    return {"showtimes": showtimes}


PAIR = Zone(min_row="D", cols=(18, 28), together=2)


class CloudDiff(unittest.TestCase):
    def test_a_pair_completing_in_the_zone_is_reported(self):
        prev = snap(s1=info(("E", 22)))
        cur = snap(s1=info(("E", 22), ("E", 23)))
        freed, _ = diff(prev, cur, PAIR)
        self.assertEqual(sorted(freed["s1"]), ["1_1_22", "1_1_23"])

    def test_seats_outside_the_zone_are_silent(self):
        freed, _ = diff(snap(s1=info()), snap(s1=info(("G", 4), ("G", 5))), PAIR)
        self.assertEqual(freed, {})

    def test_no_zone_reports_every_new_seat(self):
        freed, _ = diff(snap(s1=info()), snap(s1=info(("G", 4))), None)
        self.assertEqual(freed, {"s1": ["1_1_4"]})

    def test_a_new_session_needs_seats_in_the_zone(self):
        prev = snap(s1=info())
        outside = snap(s1=info(), s2=info(("G", 4), ("G", 5)))
        inside = snap(s1=info(), s2=info(("E", 22), ("E", 23)))
        self.assertEqual(diff(prev, outside, PAIR)[1], [])
        self.assertEqual(len(diff(prev, inside, PAIR)[1]), 1)

    def test_state_saved_before_rows_were_recorded_still_works(self):
        # The row is recovered from the leading letter of the label.
        old = info(("E", 22), with_rows=False)
        new = info(("E", 22), ("E", 23), with_rows=False)
        freed, _ = diff(snap(s1=old), snap(s1=new), PAIR)
        self.assertEqual(sorted(freed["s1"]), ["1_1_22", "1_1_23"])

    def test_changing_the_zone_between_runs_does_not_flood(self):
        same = snap(s1=info(("E", 14), ("E", 23)))
        for zone in (Zone(cols=(18, 28)), Zone(cols=(10, 32)), None):
            self.assertEqual(diff(same, same, zone), ({}, []))


if __name__ == "__main__":
    unittest.main()
