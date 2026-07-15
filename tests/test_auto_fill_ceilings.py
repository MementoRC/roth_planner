"""Regression pins for auto-fill bracket-room behavior the exercise optimizer relies on.

The optimizer schedules option income into low-tax years and then fills the
remaining bracket room with Roth conversions. That only works because
``_auto_fill_core`` already counts ``hh.option_income(year)`` against each year's
room. Task 1 pins exactly that invariant.
"""

from __future__ import annotations

from engine.scenario_autofill import auto_fill_22
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household

_PRICE = 200.0  # in the money vs the $104 strike below


def _household_with_grant() -> tuple[Household, StockGrant]:
    grant = StockGrant(year=2019, strike=104.0, shares=2000, expiry_year=2035, grant_id="g19")
    return Household(grants=[grant]), grant


def test_option_income_consumes_bracket_room_in_auto_fill() -> None:
    """Option income landing in a year leaves less 22%-bracket room for Roth
    conversions that year."""
    hh_opt, grant = _household_with_grant()
    target_year = hh_opt.base_year + 1
    sched_opt = ExerciseSchedule()
    sched_opt.set_shares(grant.key(), target_year, grant.shares)
    sched_opt.set_price(target_year, _PRICE)
    hh_opt.exercise_schedule = sched_opt

    # Identical household, but its option income lands in a LATER year, so the
    # target year is option-income-free — the only difference from hh_opt.
    hh_none, grant2 = _household_with_grant()
    later_year = hh_none.base_year + 5
    sched_none = ExerciseSchedule()
    sched_none.set_shares(grant2.key(), later_year, grant2.shares)
    sched_none.set_price(later_year, _PRICE)
    hh_none.exercise_schedule = sched_none

    # Precondition: option income really does land only in hh_opt's target year.
    assert hh_opt.option_income(target_year) > 0
    assert hh_none.option_income(target_year) == 0

    plan_opt = auto_fill_22(hh_opt)
    plan_none = auto_fill_22(hh_none)

    assert plan_opt.your_conversions.get(target_year, 0.0) < plan_none.your_conversions.get(
        target_year, 0.0
    )
