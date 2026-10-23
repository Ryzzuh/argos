"""Deciding what to look up from the date list alone, before any showtime request.

The point of all of it: a film that has not started must cost two requests (the
film list and the date list), not one per date and one per session.
"""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from argos import scrape
from argos.config import TZ
from argos.health import SchemaDrift
from argos.scrape import plan_dates

TODAY = datetime.now(TZ).date()


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


class PlanDates(unittest.TestCase):
    DATES = [iso(54), iso(55), iso(60)]

    def test_nothing_in_a_short_window_still_reports_when_it_opens(self):
        in_scope, opens_on = plan_dates(self.DATES, 14, TODAY)
        self.assertEqual(in_scope, [])
        self.assertEqual(opens_on, iso(54))

    def test_unlimited_keeps_every_date(self):
        self.assertEqual(plan_dates(self.DATES, None, TODAY)[0], self.DATES)

    def test_the_cutoff_day_is_inclusive(self):
        self.assertEqual(plan_dates([iso(14), iso(15)], 14, TODAY)[0], [iso(14)])

    def test_no_dates_at_all(self):
        self.assertEqual(plan_dates([], 14, TODAY), ([], None))


LAYOUT = {"id": "L", "areas": [{"number": 1, "name": {"text": "Standard"}, "rows": [
    {"number": 1, "label": "A", "seats": [
        {"id": "1_1_1", "position": {"areaNumber": 1, "columnNumber": 1, "rowNumber": 1},
         "rowLabel": "A", "label": "1", "type": "Normal"}]}]}]}


class FakeApi:
    """Complete enough to run a whole sweep, and counting what it was asked."""
    throttled = 0

    def __init__(self, dates, per_date=2):
        self._dates, self._per = dates, per_date
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def films(self):
        self.calls.append("films")
        return [{"id": "F1", "title": {"text": "Test Film"}}]

    async def screening_dates(self, film_id):
        self.calls.append("dates")
        return list(self._dates)

    async def showtimes_on(self, d, film_id):
        self.calls.append(f"showtimes:{d}")
        return [{"id": f"{d}-{n}", "seatLayoutId": "L", "screenId": "S1",
                 "isSoldOut": False,
                 "schedule": {"startsAt": f"{d}T18:00:00+11:00", "businessDate": d}}
                for n in range(self._per)]

    async def seat_layout(self, layout_id):
        self.calls.append("layout")
        return LAYOUT

    async def seat_availability(self, showtime_id):
        self.calls.append("availability")
        return [{"seatId": "1_1_1", "status": "Sold"}]


class LoadShowtimes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(scrape, "DATA", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    async def test_a_second_run_within_the_hour_asks_for_nothing(self):
        api = FakeApi([iso(1), iso(2)])
        await scrape._load_showtimes(api, [iso(1), iso(2)], "F1")
        api.calls.clear()
        got = await scrape._load_showtimes(api, [iso(1), iso(2)], "F1")
        self.assertEqual(api.calls, [])
        self.assertEqual(len(got), 4)

    async def test_a_windowed_run_and_a_full_run_do_not_evict_each_other(self):
        # The regression a single cache keyed on the whole date set would have.
        api = FakeApi([iso(1), iso(2), iso(3)])
        await scrape._load_showtimes(api, [iso(1)], "F1", listed=[iso(1), iso(2), iso(3)])
        await scrape._load_showtimes(api, [iso(1), iso(2), iso(3)], "F1")
        api.calls.clear()
        await scrape._load_showtimes(api, [iso(1)], "F1", listed=[iso(1), iso(2), iso(3)])
        self.assertEqual(api.calls, [], "the windowed run refetched what the full run had")

    async def test_only_the_missing_dates_are_fetched(self):
        api = FakeApi([iso(1), iso(2)])
        await scrape._load_showtimes(api, [iso(1)], "F1")
        api.calls.clear()
        await scrape._load_showtimes(api, [iso(1), iso(2)], "F1")
        self.assertEqual(api.calls, [f"showtimes:{iso(2)}"])

    async def test_force_refetches_everything_asked_for(self):
        api = FakeApi([iso(1), iso(2)])
        await scrape._load_showtimes(api, [iso(1), iso(2)], "F1")
        api.calls.clear()
        await scrape._load_showtimes(api, [iso(1), iso(2)], "F1", force=True)
        self.assertEqual(len(api.calls), 2)

    async def test_the_old_cache_format_is_discarded_not_crashed_on(self):
        (Path(self.tmp.name) / "showtimes-F1.json").write_text(json.dumps(
            {"fetched_at": 1.0, "dates": [iso(1)], "showtimes": [{"id": "x"}]}))
        api = FakeApi([iso(1)])
        got = await scrape._load_showtimes(api, [iso(1)], "F1")
        self.assertEqual(len(got), 2)

    async def test_dates_the_api_no_longer_lists_are_forgotten(self):
        api = FakeApi([iso(1), iso(2)])
        await scrape._load_showtimes(api, [iso(1), iso(2)], "F1")
        await scrape._load_showtimes(api, [iso(1)], "F1", listed=[iso(1)])
        cached = json.loads((Path(self.tmp.name) / "showtimes-F1.json").read_text())
        self.assertEqual(list(cached), [iso(1)])


class SweepEarlyExit(unittest.IsolatedAsyncioTestCase):
    """Run the real sweep() against a fake API and count what it asked for."""

    def setUp(self):
        # Caches go to a scratch directory, never the real data/.
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(scrape, "DATA", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    async def run_sweep(self, dates, *, within_days=14, lead_days=None, upcoming=False):
        api = FakeApi(dates)
        with mock.patch.object(scrape, "Ocapi", lambda: api), \
             mock.patch.object(scrape.store, "has_upcoming_sessions",
                               lambda *a: upcoming):
            result = await scrape.sweep("F1", verbose=False, within_days=within_days,
                                        lead_days=lead_days)
        return api, result

    async def test_a_film_that_has_not_started_costs_two_requests(self):
        api, (reports, summary) = await self.run_sweep([iso(54 + n) for n in range(18)])
        self.assertEqual(api.calls, ["films", "dates"])
        self.assertTrue(summary["nothing_in_scope"])
        self.assertEqual((summary["opens_on"], summary["days_away"]), (iso(54), 54))

    async def test_unlimited_window_is_still_dormant_until_the_lead(self):
        # The configuration that prompted this: every date, but not yet showing.
        api, (_, summary) = await self.run_sweep(
            [iso(54 + n) for n in range(18)], within_days=None, lead_days=14)
        self.assertEqual(api.calls, ["films", "dates"])
        self.assertEqual(summary["wakes_on"], iso(40))

    async def test_it_wakes_once_inside_the_lead(self):
        dates = [iso(10), iso(11)]
        api, (reports, summary) = await self.run_sweep(
            dates, within_days=None, lead_days=14)
        self.assertNotIn("nothing_in_scope", summary)
        self.assertEqual([c for c in api.calls if c.startswith("showtimes")],
                         [f"showtimes:{d}" for d in dates])

    async def test_the_boundary_day_is_awake_and_the_day_before_is_not(self):
        # lead 14: a first session exactly 14 days out is in; 15 days out is not.
        _, (_, on_boundary) = await self.run_sweep(
            [iso(14), iso(15)], within_days=None, lead_days=14)
        _, (_, just_outside) = await self.run_sweep(
            [iso(15), iso(16)], within_days=None, lead_days=14)
        self.assertNotIn("nothing_in_scope", on_boundary)
        self.assertTrue(just_outside["nothing_in_scope"])
        self.assertEqual(just_outside["wakes_on"], iso(1))

    async def test_a_lead_of_zero_waits_for_the_first_day(self):
        _, (_, early) = await self.run_sweep([iso(1)], within_days=None, lead_days=0)
        self.assertTrue(early["nothing_in_scope"])

    async def test_a_film_already_showing_is_never_dormant(self):
        # Dates start in the past: days_away is negative, so no lead can hold it.
        dates = [iso(-1), iso(2)]
        _, (_, summary) = await self.run_sweep(dates, within_days=None, lead_days=0)
        self.assertNotIn("nothing_in_scope", summary)

    async def test_without_a_lead_the_window_alone_decides(self):
        api, (_, summary) = await self.run_sweep([iso(54)], within_days=None, lead_days=None)
        self.assertNotIn("nothing_in_scope", summary)
        self.assertTrue(any(c.startswith("showtimes") for c in api.calls))

    async def test_listed_with_no_dates_is_normal_when_nothing_was_ever_recorded(self):
        _, (_, summary) = await self.run_sweep([], upcoming=False)
        self.assertEqual(summary["reason"], "no sessions on sale")

    async def test_listed_with_no_dates_after_having_sessions_is_drift(self):
        with self.assertRaises(SchemaDrift):
            await self.run_sweep([], upcoming=True)


if __name__ == "__main__":
    unittest.main()
