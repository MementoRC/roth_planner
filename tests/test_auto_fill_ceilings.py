"""Regression pins for auto-fill bracket-room behavior the exercise optimizer relies on.

The optimizer schedules option income into low-tax years and then fills the
remaining bracket room with Roth conversions. That only works because
``_auto_fill_core`` already counts ``hh.option_income(year)`` against each year's
room. Task 1 pins exactly that invariant.
"""

from __future__ import annotations

from engine.scenario_autofill import auto_fill_22, auto_fill_24, auto_fill_aca
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


def test_auto_fill_24_converts_more_than_22() -> None:
    """auto_fill_24 must convert at least as much as auto_fill_22 in every
    pre-RMD year, and strictly more in total: the 24% bracket ceiling is
    higher than the 22% ceiling, so there is more room to fill each year.

    Mirrors test_22pct_fill_more_aggressive (tests/test_scenario_core.py), but
    with a MUCH larger IRA ($10M each, vs that test's $1.7M) so that neither
    fill ever exhausts the IRA balance within the pre-RMD window. With the
    $1.7M fixture the more-aggressive 24%-fill drains the IRA to $0 a few
    years before the less-aggressive 22%-fill does, which makes the 24%-fill
    convert LESS than the 22%-fill in those later years (both are 0, but 22%
    still has balance) -- an IRA-cap artifact, not a bracket-room difference.
    """
    hh = Household(your_age=61, spouse_age=55, your_ira=10_000_000, spouse_ira=10_000_000)
    plan_22 = auto_fill_22(hh)
    plan_24 = auto_fill_24(hh)

    for year in range(hh.base_year, hh.base_year + (hh.your_rmd_start_age - hh.your_age)):
        conv_22 = plan_22.your_conversions.get(year, 0.0) + plan_22.spouse_conversions.get(
            year, 0.0
        )
        conv_24 = plan_24.your_conversions.get(year, 0.0) + plan_24.spouse_conversions.get(
            year, 0.0
        )
        assert conv_24 >= conv_22 - 1.0, (
            f"year {year}: 24%-fill conversion ({conv_24:.0f}) should be >= "
            f"22%-fill conversion ({conv_22:.0f})"
        )

    total_22 = sum(plan_22.your_conversions.values()) + sum(plan_22.spouse_conversions.values())
    total_24 = sum(plan_24.your_conversions.values()) + sum(plan_24.spouse_conversions.values())
    assert total_24 > total_22, (
        f"24%-fill total ({total_24:.0f}) should exceed 22%-fill total ({total_22:.0f})"
    )


def test_auto_fill_aca_converts_less_than_22() -> None:
    """auto_fill_aca caps conversions at the ACA 400%-FPL MAGI cliff (~$80K
    MFJ), far below the 22% bracket ceiling, so it must convert strictly less
    in total than auto_fill_22 while still converting something in at least
    one pre-RMD year.

    Mirrors test_irmaa_safe_stays_under_threshold (tests/test_scenario_core.py)
    in spirit, but (like the 24%-fill test above) uses a large IRA ($10M each)
    rather than the default Household()'s $500K/$500K. With the small default
    IRA both fills fully drain the account within the window, and the slower
    ACA-capped drain leaves money compounding in the IRA longer, producing a
    LARGER nominal total than the fast-draining 22%-fill -- an IRA-cap/growth
    artifact that inverts the comparison this test is meant to make.
    """
    hh = Household(your_age=61, spouse_age=55, your_ira=10_000_000, spouse_ira=10_000_000)
    plan_22 = auto_fill_22(hh)
    plan_aca = auto_fill_aca(hh)

    total_22 = sum(plan_22.your_conversions.values()) + sum(plan_22.spouse_conversions.values())
    total_aca = sum(plan_aca.your_conversions.values()) + sum(plan_aca.spouse_conversions.values())
    assert total_aca < total_22, (
        f"ACA-capped total ({total_aca:.0f}) should be strictly less than "
        f"22%-fill total ({total_22:.0f})"
    )

    pre_rmd_years = range(hh.base_year, hh.base_year + (hh.your_rmd_start_age - hh.your_age))
    positive_somewhere = any(
        plan_aca.your_conversions.get(yr, 0.0) + plan_aca.spouse_conversions.get(yr, 0.0) > 0.0
        for yr in pre_rmd_years
    )
    assert positive_somewhere, (
        "auto_fill_aca must convert a positive amount in at least one pre-RMD year"
    )
