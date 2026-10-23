"""Zone logic: the off-by-ones and silent failures it is easy to get wrong.

Run with:  uv run python -m unittest discover -s tests -v
"""
import unittest

from argos.zone import (Geometry, Zone, ZoneError, format_runs, newly_qualifying,
                        parse, qualify, resolve, validate)


def seat(row, col, area=1, row_number=1):
    """A free seat as the store returns it: (seat_id, row_label, seat_label)."""
    return (f"{area}_{row_number}_{col}", row, str(col))


ZONE = Zone(min_row="D", cols=(18, 28), together=1)
PAIR = Zone(min_row="D", cols=(18, 28), together=2)


class InZone(unittest.TestCase):
    def test_row_bound_is_inclusive_and_counts_letters(self):
        self.assertFalse(ZONE.in_zone("C", 23))
        self.assertTrue(ZONE.in_zone("D", 23))
        self.assertTrue(ZONE.in_zone("M", 23))

    def test_missing_row_i_does_not_shift_the_bound(self):
        # Letter arithmetic would put J at 10 where it is the 9th row.
        z = Zone(min_row="H")
        self.assertTrue(z.in_zone("J", 23))
        self.assertFalse(z.in_zone("G", 23))

    def test_column_band_is_inclusive_at_both_ends(self):
        self.assertFalse(ZONE.in_zone("E", 17))
        self.assertTrue(ZONE.in_zone("E", 18))
        self.assertTrue(ZONE.in_zone("E", 28))
        self.assertFalse(ZONE.in_zone("E", 29))

    def test_the_seat_actually_booked_is_inside(self):
        self.assertIn("1_8_23", ZONE.qualifying([seat("E", 23, row_number=8)]))


class Together(unittest.TestCase):
    def test_a_lone_seat_does_not_qualify_a_pair_zone(self):
        self.assertEqual(PAIR.qualifying([seat("E", 23)]), set())

    def test_adjacent_pair_qualifies_both_seats(self):
        got = PAIR.qualifying([seat("E", 22), seat("E", 23)])
        self.assertEqual(got, {"1_1_22", "1_1_23"})

    def test_a_gap_breaks_adjacency(self):
        self.assertEqual(PAIR.qualifying([seat("E", 22), seat("E", 24)]), set())

    def test_longer_run_qualifies_every_seat_in_it(self):
        run = [seat("E", c) for c in (21, 22, 23)]
        self.assertEqual(len(PAIR.qualifying(run)), 3)

    def test_pair_straddling_the_band_edge_does_not_count(self):
        # 28 is in the band, 29 is not: only one seat is inside.
        self.assertEqual(PAIR.qualifying([seat("E", 28), seat("E", 29)]), set())

    def test_neighbouring_rows_are_not_adjacent(self):
        self.assertEqual(PAIR.qualifying([seat("E", 23), seat("F", 24)]), set())

    def test_areas_sharing_a_column_number_are_not_neighbours(self):
        # Row M is split into two areas that reuse column numbers.
        seats = [seat("M", 22, area=1), seat("M", 23, area=2)]
        self.assertEqual(PAIR.qualifying(seats), set())

    def test_together_without_a_band_works_anywhere(self):
        z = Zone(together=2)
        self.assertEqual(len(z.qualifying([seat("A", 1), seat("A", 2)])), 2)


class Diffing(unittest.TestCase):
    def test_a_pair_forming_alerts_on_both_seats(self):
        # 22 was free but unqualified; 23 freeing completes the pair.
        before = {"s1": [seat("E", 22)]}
        now = {"s1": [seat("E", 22), seat("E", 23)]}
        got = newly_qualifying(before, now, PAIR, {"s1"})
        self.assertEqual(got, {"s1": {"1_1_22", "1_1_23"}})

    def test_unchanged_seats_never_alert_whatever_the_zone(self):
        snap = {"s1": [seat("E", 23), seat("G", 4)]}
        for zone in (None, ZONE, PAIR, Zone(cols=(1, 45))):
            self.assertEqual(newly_qualifying(snap, snap, zone, {"s1"}), {})

    def test_widening_a_zone_does_not_flood_alerts(self):
        # Same seats in both snapshots; only the zone changes between runs.
        snap = {"s1": [seat("E", 14), seat("E", 23)]}
        narrow, wide = Zone(cols=(18, 28)), Zone(cols=(10, 32))
        self.assertEqual(newly_qualifying(snap, snap, narrow, {"s1"}), {})
        self.assertEqual(newly_qualifying(snap, snap, wide, {"s1"}), {})

    def test_seats_outside_the_zone_are_ignored(self):
        got = newly_qualifying({"s1": []}, {"s1": [seat("G", 4)]}, ZONE, {"s1"})
        self.assertEqual(got, {})

    def test_a_showtime_not_checked_last_time_is_skipped(self):
        got = newly_qualifying({}, {"s9": [seat("E", 23)]}, ZONE, comparable=set())
        self.assertEqual(got, {})

    def test_no_zone_alerts_on_any_new_seat(self):
        got = newly_qualifying({"s1": []}, {"s1": [seat("G", 4)]}, None, {"s1"})
        self.assertEqual(got, {"s1": {"1_1_4"}})


class Formatting(unittest.TestCase):
    def test_adjacent_seats_read_as_a_range(self):
        self.assertEqual(format_runs([seat("E", 22), seat("E", 23)]), "E22–E23")

    def test_separate_seats_stay_separate(self):
        self.assertEqual(format_runs([seat("E", 22), seat("G", 4)]), "E22, G4")


class Parsing(unittest.TestCase):
    def test_empty_or_disabled_means_no_zone(self):
        self.assertIsNone(parse({}, "x"))
        self.assertIsNone(parse({"enabled": False, "min_row": "D"}, "x"))
        self.assertIsNone(parse({"enabled": True}, "x"))

    def test_the_requested_zone(self):
        z = parse({"min_row": "d", "cols": [18, 28], "together": 2}, "x")
        self.assertEqual(z, Zone("D", (18, 28), 2))

    def test_a_misspelt_key_is_an_error_not_a_silent_no_op(self):
        with self.assertRaises(ZoneError):
            parse({"column": [18, 28]}, "x")

    def test_bad_values_are_rejected(self):
        for bad in ({"cols": [28, 18]}, {"cols": [18]}, {"cols": "18-28"},
                    {"min_row": "DD"}, {"min_row": 4}, {"together": 0},
                    {"together": True}, {"cols": [0, 5]}):
            with self.assertRaises(ZoneError, msg=str(bad)):
                parse(bad, "x")

    def test_together_wider_than_the_band_can_never_match(self):
        with self.assertRaises(ZoneError):
            parse({"cols": [18, 19], "together": 3}, "x")

    def test_a_film_overrides_the_default_field_by_field(self):
        d = {"min_row": "D", "cols": [18, 28], "together": 2}
        z = resolve(d, {"cols": [14, 32]}, "x")
        self.assertEqual(z, Zone("D", (14, 32), 2))

    def test_a_film_can_switch_the_zone_off(self):
        d = {"min_row": "D", "cols": [18, 28]}
        self.assertIsNone(resolve(d, {"enabled": False}, "x"))

    def test_a_film_with_its_own_table_opts_in_over_a_disabled_default(self):
        d = {"enabled": False, "cols": [18, 28]}
        self.assertIsNotNone(resolve(d, {"cols": [10, 30]}, "x"))

    def test_no_film_table_inherits_the_default(self):
        self.assertEqual(resolve({"min_row": "D"}, None, "x"), Zone("D"))


class AgainstTheRoom(unittest.TestCase):
    ROOM = Geometry(tuple(
        (f"1_{n}_{c}", row, "Normal")
        for n, row in enumerate("DEFG", 1) for c in range(1, 46)))

    def test_a_good_zone_validates(self):
        self.assertIsNone(validate(ZONE, self.ROOM))

    def test_an_unknown_row_is_caught(self):
        self.assertIn("not a row", validate(Zone(min_row="Z"), self.ROOM))

    def test_a_band_off_the_grid_is_caught(self):
        self.assertIn("outside", validate(Zone(cols=(60, 70)), self.ROOM))

    def test_a_zone_nothing_can_satisfy_is_caught(self):
        # Row labels exist and the band is on the grid, but no seat is both.
        room = Geometry((("1_1_5", "D", "Normal"), ("1_2_40", "E", "Normal")))
        self.assertIn("could ever", validate(Zone(min_row="E", cols=(1, 10)), room))


if __name__ == "__main__":
    unittest.main()
