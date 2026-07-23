"""Regression test — audit-0722b HIGH.

Saving an unedited exercise schedule persists ``shares_by_grant_year`` but an
EMPTY ``price_by_year`` (the save-side drop-filter in
``views/option_exercise.py`` intentionally omits untouched "assumed" cells so
they don't freeze into fake overrides). Before the fix,
``ExerciseSchedule.income_for`` fell back to a flat 0.0 price for any year
missing from ``price_by_year``, so reloading such a household zeroed ALL
option income across the engine (scenario, sweet_spot, headroom, dashboard,
planner, rmd_squeeze) even though shares were still scheduled.

The fix: ``Household.option_income`` passes ``self.projected_txn_price`` as a
resolver into ``income_for`` so a missing price is re-projected from
``txn_price_now`` / ``txn_price_growth`` instead of defaulting to zero.
"""

import pytest

from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import GrowthProfile, Household


def approx(expected, tol=1e-2):
    return pytest.approx(expected, abs=tol)


def _household_with_saved_default_schedule() -> Household:
    grant = StockGrant(year=2020, strike=130.0, shares=650, expiry_year=2030, grant_id="g20")
    # Exact artifact of "Save schedule" on an untouched default: shares are
    # persisted, but price_by_year is empty because every cell matched the
    # projected assumption within tolerance and was dropped by the filter.
    schedule = ExerciseSchedule(
        shares_by_grant_year={grant.key(): {2030: 650}},
        price_by_year={},
    )
    return Household(
        grants=[grant],
        base_year=2026,
        txn_price_now=200.0,
        txn_price_growth=GrowthProfile(default_rate=0.07),
        exercise_schedule=schedule,
    )


class TestSavedDefaultScheduleStillPricesIncome:
    def test_option_income_reprojects_missing_price_instead_of_zero(self):
        hh = _household_with_saved_default_schedule()
        expected = (200.0 * 1.07**4 - 130.0) * 650
        assert expected == approx(85904.34, tol=1.0)
        assert hh.option_income(2030) == approx(expected)
        assert hh.option_income(2030) != approx(0.0)

    def test_engine_income_for_still_zero_when_no_resolver_given(self):
        # Bare-schedule callers with no resolver keep the old safe 0.0
        # fallback -- only Household.option_income supplies a resolver.
        grant = StockGrant(year=2020, strike=130.0, shares=650, expiry_year=2030, grant_id="g20")
        schedule = ExerciseSchedule(
            shares_by_grant_year={grant.key(): {2030: 650}}, price_by_year={}
        )
        assert schedule.income_for(2030, [grant]) == approx(0.0)

    def test_price_resolver_used_only_for_missing_years(self):
        # A year that DOES have an explicit stored price must use it, not the
        # resolver -- resolver is a gap-filler, not an override.
        grant = StockGrant(year=2020, strike=130.0, shares=650, expiry_year=2030, grant_id="g20")
        schedule = ExerciseSchedule(
            shares_by_grant_year={grant.key(): {2030: 650}}, price_by_year={2030: 300.0}
        )
        resolver_called_with = []

        def resolver(year: int) -> float:
            resolver_called_with.append(year)
            return 999.0

        income = schedule.income_for(2030, [grant], price_resolver=resolver)
        assert resolver_called_with == []
        assert income == approx((300.0 - 130.0) * 650)
