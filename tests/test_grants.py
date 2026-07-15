"""Tests for models.grants — StockGrant."""

import pytest

from config.defaults import DEFAULTS
from engine.scenario_compute import compute_phase
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestGrants:
    def test_grant_spreads(self):
        hh = Household()
        price = DEFAULTS["stock_price_now"]
        for i, g in enumerate(hh.grants):
            expected = DEFAULTS["grants"][i]
            assert g.spread(price) == approx(expected.spread(price))

    def test_total_spread(self):
        hh = Household()
        price = DEFAULTS["stock_price_now"]
        total = sum(g.spread(price) for g in hh.grants)
        expected = sum(g.spread(price) for g in DEFAULTS["grants"])
        assert total == approx(expected, tol=10)

    def test_option_income_by_year(self):
        hh = Household()
        assert hh.option_income(2026) == 0
        assert hh.option_income(hh.grants[0].expiry_year) == approx(
            hh.grants[0].spread(hh.txn_price_now)
        )
        assert hh.option_income(hh.grants[1].expiry_year) == approx(
            hh.grants[1].spread(hh.txn_price_now)
        )
        assert hh.option_income(hh.grants[2].expiry_year) == approx(
            hh.grants[2].spread(hh.txn_price_now)
        )
        assert hh.option_income(hh.grants[2].expiry_year + 1) == 0


class TestComputePhaseOptionsWindow:
    """Characterization (2026-07-14 design): compute_phase's 'options' label is
    NOT a hardcoded 2026-2028 window anymore -- it fires for ANY year with
    scheduled option income > 0 (capped by grant.expiry_year via
    ExerciseSchedule.income_for). Supersedes the old fixed-window
    TestComputePhaseNqoWindow characterization.
    """

    def test_options_phase_active_for_any_year_with_scheduled_exercise(self):
        # An explicit schedule entry outside the old 2026-2028 window (e.g. 2031,
        # within the grant's expiry) must still label that year 'options'.
        from models.exercise_schedule import ExerciseSchedule
        from models.grants import StockGrant

        hh = Household(
            grants=[StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2031)],
            base_year=2026,
        )
        hh.exercise_schedule = ExerciseSchedule()
        hh.exercise_schedule.set_shares(hh.grants[0].key(), 2031, 1000)
        hh.exercise_schedule.set_price(2031, 200.0)
        ya = hh.your_age + (2031 - hh.base_year)
        sa = hh.spouse_age + (2031 - hh.base_year)
        phase = compute_phase(ya, sa, 2031, hh)
        assert phase == "options", f"Expected 'options' in 2031 (scheduled exercise), got {phase!r}"

    def test_no_options_phase_in_year_without_scheduled_exercise(self):
        # Same household/schedule as above, but a year with nothing scheduled
        # must NOT be labeled 'options'.
        from models.exercise_schedule import ExerciseSchedule
        from models.grants import StockGrant

        hh = Household(
            grants=[StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2031)],
            base_year=2026,
        )
        hh.exercise_schedule = ExerciseSchedule()
        hh.exercise_schedule.set_shares(hh.grants[0].key(), 2031, 1000)
        hh.exercise_schedule.set_price(2031, 200.0)
        phase = compute_phase(hh.your_age, hh.spouse_age, hh.base_year, hh)
        assert phase != "options", (
            f"Expected no 'options' phase in {hh.base_year} (nothing scheduled), got {phase!r}"
        )
