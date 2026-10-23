"""films.toml parsing: the window, and typos that must not pass silently."""
import tempfile
import unittest
from pathlib import Path

from argos.films import load
from argos.health import ArgosError


def films(body: str):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "films.toml"
        path.write_text(body)
        return load(path, include_disabled=True)


def one(extra: str):
    return films(f'[[film]]\nid = "X"\n{extra}\n')[0]


class WithinDays(unittest.TestCase):
    def test_omitted_defaults_to_two_weeks(self):
        self.assertEqual(one("").within_days, 14)

    def test_a_number_of_days(self):
        self.assertEqual(one("within_days = 30").within_days, 30)

    def test_all_means_no_limit(self):
        self.assertIsNone(one('within_days = "all"').within_days)

    def test_zero_means_no_limit_not_nothing(self):
        # Treated literally, 0 would cut off at 'now' and filter every session.
        self.assertIsNone(one("within_days = 0").within_days)

    def test_nonsense_is_an_error_not_a_default(self):
        for bad in ('within_days = -5', 'within_days = "soon"', 'within_days = true',
                    'within_days = 2.5'):
            with self.assertRaises(ArgosError, msg=bad):
                one(bad)


class Typos(unittest.TestCase):
    def test_a_misspelt_film_key_is_an_error(self):
        with self.assertRaises(ArgosError):
            one("enable = false")

    def test_a_misspelt_top_level_table_is_an_error(self):
        with self.assertRaises(ArgosError):
            films('[default.seats]\nmin_row = "D"\n[[film]]\nid = "X"\n')


if __name__ == "__main__":
    unittest.main()
