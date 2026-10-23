"""Sweep timing: when a run history means alert, and that the numbers are kept honest."""
import argparse
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from argos import config, timing, watch
from argos.timing import assess, fmt

B = 600          # the budget: the watch interval
WARN = 0.7 * B   # 420s


class Assess(unittest.TestCase):
    def test_comfortably_inside_the_budget_is_healthy(self):
        self.assertEqual(assess("watch", [60, 70, 65]), ("recover", ""))

    def test_running_longer_than_the_budget_is_an_overrun(self):
        self.assertEqual(assess("watch", [60, 60, B + 1]), ("alert", "overran"))

    def test_exactly_the_budget_is_not_yet_an_overrun(self):
        # Equal means the next tick started as this one finished: nothing skipped.
        self.assertNotEqual(assess("watch", [60, 60, B])[1], "overran")

    def test_three_tight_watch_runs_in_a_row_is_a_pattern(self):
        self.assertEqual(assess("watch", [450, 460, 470]), ("alert", "tight"))

    def test_two_tight_runs_are_a_blip(self):
        self.assertEqual(assess("watch", [60, 450, 460]), (None, ""))

    def test_one_run_below_the_line_breaks_the_pattern(self):
        self.assertEqual(assess("watch", [450, 100, 470])[0], None)

    def test_the_warning_line_is_exclusive_and_recovery_inclusive(self):
        # 70% exactly is not tight, and is healthy.
        self.assertEqual(assess("watch", [WARN, WARN, WARN]), ("recover", ""))
        self.assertEqual(assess("watch", [WARN + 1] * 3), ("alert", "tight"))

    def test_between_the_lines_with_no_pattern_changes_nothing(self):
        self.assertEqual(assess("watch", [500]), (None, ""))

    def test_a_full_sweep_only_alerts_when_it_overruns_the_watch_budget(self):
        # Three tight full runs would be nine hours of history; it blocks the
        # watch loop only once it actually exceeds the interval.
        self.assertEqual(assess("full", [450, 460, 470]), (None, ""))
        self.assertEqual(assess("full", [60, B + 1]), ("alert", "overran"))

    def test_a_fixed_problem_is_announced_on_the_first_good_run(self):
        self.assertEqual(assess("watch", [700, 650, 90]), ("recover", ""))

    def test_no_history_says_nothing(self):
        self.assertEqual(assess("watch", []), (None, ""))


class History(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(timing, "TIMING_FILE", Path(self.tmp.name) / "timing.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    FILMS = [{"id": "a", "name": "A", "seconds": 40.0, "requests": 18, "throttled": 0},
             {"id": "b", "name": "B", "seconds": 150.0, "requests": 94, "throttled": 3}]

    def test_a_run_is_recorded_with_its_totals(self):
        entries = timing.record("watch", 190.04, self.FILMS)
        self.assertEqual(entries[-1]["seconds"], 190.0)
        self.assertEqual((entries[-1]["requests"], entries[-1]["throttled"]), (112, 3))

    def test_history_is_capped_and_newest_last(self):
        for n in range(timing.KEEP + 5):
            entries = timing.record("watch", float(n), [])
        self.assertEqual(len(entries), timing.KEEP)
        self.assertEqual(entries[-1]["seconds"], float(timing.KEEP + 4))

    def test_the_kinds_are_kept_apart(self):
        timing.record("watch", 30, [])
        timing.record("full", 240, [])
        loaded = timing.load()
        self.assertEqual([e["seconds"] for e in loaded["watch"]], [30])
        self.assertEqual([e["seconds"] for e in loaded["full"]], [240])

    def test_a_corrupt_file_is_treated_as_no_history_not_a_crash(self):
        timing.TIMING_FILE.write_text("{not json")
        self.assertEqual(timing.load(), {"watch": [], "full": []})
        self.assertEqual(len(timing.record("watch", 5, [])), 1)

    def test_the_check_in_line_reports_slack(self):
        timing.record("watch", 60, [])
        self.assertIn("watch 1m00s (10% of budget)", timing.summary_line())

    def test_the_check_in_line_is_empty_before_any_run(self):
        self.assertEqual(timing.summary_line(), "")


class Dispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(timing, "TIMING_FILE", Path(self.tmp.name) / "t.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.fail_, self.recover = mock.Mock(), mock.Mock()
        for target, fake in (("report_failure", self.fail_), ("report_recovery", self.recover)):
            p = mock.patch.object(timing.health, target, fake)
            p.start()
            self.addCleanup(p.stop)

    def test_an_overrun_alerts_under_a_per_kind_signature(self):
        timing.record_and_check("watch", B + 60, [])
        self.assertEqual(self.fail_.call_args.args[0], "timing:watch")
        self.recover.assert_not_called()

    def test_a_healthy_run_announces_recovery_not_an_alert(self):
        timing.record_and_check("watch", 30, [])
        self.recover.assert_called_once_with("timing:watch")
        self.fail_.assert_not_called()

    def test_the_middle_ground_does_neither(self):
        timing.record_and_check("watch", 500, [])
        self.fail_.assert_not_called()
        self.recover.assert_not_called()

    def test_the_alert_names_the_numbers_and_the_heaviest_film_first(self):
        films = [{"id": "a", "name": "Odyssey", "seconds": 40, "requests": 18, "throttled": 0},
                 {"id": "b", "name": "Dune", "seconds": 600, "requests": 94, "throttled": 5}]
        timing.record_and_check("watch", 700, films)
        text = self.fail_.call_args.args[1]
        self.assertIn("11m40s", text)
        self.assertIn("10m00s", text)
        self.assertIn("skipped", text)
        self.assertIn("112 requests in all, 5 of them throttled", text)
        self.assertLess(text.index("Dune"), text.index("Odyssey"))

    def test_a_full_sweep_overrun_says_it_blocked_the_watch_loop(self):
        timing.record_and_check("full", 900, [])
        self.assertIn("watch sweep skipped", self.fail_.call_args.args[1])


class Wiring(unittest.TestCase):
    def test_adhoc_runs_are_not_measured(self):
        ns = lambda **kw: argparse.Namespace(**{"dry_run": False, "film_id": None,
                                                "within_days": None, **kw})
        self.assertFalse(watch._adhoc(ns()))
        self.assertTrue(watch._adhoc(ns(dry_run=True)))
        self.assertTrue(watch._adhoc(ns(film_id="X")))
        self.assertTrue(watch._adhoc(ns(within_days=3)))

    def test_the_installer_runs_the_jobs_at_the_intervals_the_config_assumes(self):
        # The same two numbers live in a shell script and in config.py.
        script = (Path(__file__).resolve().parent.parent / "bin/install-agents.sh").read_text()
        found = dict((m[0], int(m[1])) for m in re.findall(r'"(\w+)\|(\d+)\|', script))
        self.assertEqual(found["watch"], config.WATCH_INTERVAL_SECONDS)
        self.assertEqual(found["full"], config.FULL_INTERVAL_SECONDS)


class Format(unittest.TestCase):
    def test_durations_read_naturally(self):
        self.assertEqual(fmt(45), "45s")
        self.assertEqual(fmt(192), "3m12s")
        self.assertEqual(fmt(600), "10m00s")
        self.assertEqual(fmt(3900), "1h05m")


if __name__ == "__main__":
    unittest.main()
