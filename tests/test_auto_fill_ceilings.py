"""Regression pins for auto-fill bracket-room behavior the exercise optimizer relies on.

The optimizer schedules option income into low-tax years and then fills the
remaining bracket room with Roth conversions. That only works because
``_auto_fill_core`` already counts ``hh.option_income(year)`` against each year's
room. Task 1 pins exactly that invariant.
"""

from __future__ import annotations

from engine.aca import is_pre_medicare_age
from engine.irmaa import IRMAA_TIERS_MFJ
from engine.scenario_autofill import auto_fill_22, auto_fill_24, auto_fill_aca
from engine.tax_indexing import index_value
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

    fix/aca-safe-medicare-age-gate: once BOTH spouses are 65+ the ACA
    400%-FPL cliff no longer applies (Medicare -- no subsidy left to
    protect), but auto_fill_aca does NOT become unbounded there. Its room
    falls back to the IRMAA tier-1 MAGI ceiling, which is still well below
    the 22% bracket ceiling, so the LIFETIME comparison continues to hold and
    is asserted unscoped below. (An earlier revision of this branch did let
    the post-65 room run unbounded and had to rescope this assertion to
    pre-Medicare years only; the tier-1 fallback removed that need. On this
    fixture the lifetime aca_safe total is ~$1.4M against the 22%-fill's
    ~$3.3M -- unbounded it was ~$39M.)

    The pre-Medicare subset is asserted SEPARATELY as the sharper claim: that
    is the window where the 400%-FPL cliff itself -- far tighter than IRMAA
    tier-1 -- is the binding constraint, so it pins ACA-specific behaviour
    rather than the shared fallback. The post-65 bound has its own test:
    test_auto_fill_aca_post_medicare_room_is_bounded_by_irmaa_tier1.
    """
    hh = Household(your_age=61, spouse_age=55, your_ira=10_000_000, spouse_ira=10_000_000)
    plan_22 = auto_fill_22(hh)
    plan_aca = auto_fill_aca(hh)

    # Wide enough to fully cover the pre-Medicare window for either spouse
    # (spouse_age=55 turns 65 ten years out); is_pre_medicare_age naturally
    # truncates the filtered list once both spouses are 65+, so the extra
    # range beyond that point is harmless.
    projection_years = range(hh.base_year, hh.base_year + 40)
    pre_medicare_years = [
        yr
        for yr in projection_years
        if is_pre_medicare_age(hh.your_age_in(yr)) or is_pre_medicare_age(hh.spouse_age_in(yr))
    ]
    total_22 = sum(
        plan_22.your_conversions.get(yr, 0.0) + plan_22.spouse_conversions.get(yr, 0.0)
        for yr in pre_medicare_years
    )
    total_aca_pre_medicare = sum(
        plan_aca.your_conversions.get(yr, 0.0) + plan_aca.spouse_conversions.get(yr, 0.0)
        for yr in pre_medicare_years
    )
    total_aca_full = sum(plan_aca.your_conversions.values()) + sum(
        plan_aca.spouse_conversions.values()
    )
    total_22_full = sum(plan_22.your_conversions.values()) + sum(
        plan_22.spouse_conversions.values()
    )
    # Sanity invariant: the full-projection total can never be smaller than the
    # pre-Medicare-only subset -- both sums are over non-negative amounts.
    assert total_aca_full >= total_aca_pre_medicare
    # Lifetime claim: holds across the WHOLE projection, post-65 included,
    # because the post-Medicare fallback ceiling (IRMAA tier-1) is itself below
    # the 22% bracket ceiling.
    assert total_aca_full < total_22_full, (
        f"ACA-capped lifetime total ({total_aca_full:.0f}) should be strictly less "
        f"than 22%-fill lifetime total ({total_22_full:.0f})"
    )
    # Sharper pre-Medicare claim: the window where the 400%-FPL cliff itself
    # (much tighter than IRMAA tier-1) is what binds.
    assert total_aca_pre_medicare < total_22, (
        f"ACA-capped pre-Medicare total ({total_aca_pre_medicare:.0f}) should be strictly "
        f"less than 22%-fill pre-Medicare total ({total_22:.0f})"
    )

    pre_rmd_years = range(hh.base_year, hh.base_year + (hh.your_rmd_start_age - hh.your_age))
    positive_somewhere = any(
        plan_aca.your_conversions.get(yr, 0.0) + plan_aca.spouse_conversions.get(yr, 0.0) > 0.0
        for yr in pre_rmd_years
    )
    assert positive_somewhere, (
        "auto_fill_aca must convert a positive amount in at least one pre-RMD year"
    )


def test_auto_fill_aca_post_medicare_room_is_bounded_by_irmaa_tier1() -> None:
    """Once BOTH spouses are 65+, auto_fill_aca's per-year room must fall back
    to the IRMAA tier-1 MAGI ceiling -- it must NOT become unbounded.

    The ACA 400%-FPL cliff genuinely stops applying at Medicare age, and
    engine/scenario.py's _strategy_magi_ceiling correctly lifts its CONSTRAINT
    entirely there. But this room fn is a heuristic that has to name a dollar
    figure: with no ceiling at all it returns float("inf") and the plan
    converts the entire remaining IRA balance every post-65 year (lifetime
    total ~$39M on this fixture, against ~$1.4M with the fallback) -- a
    degenerate plan rather than a strategy. Past 65 the household is on
    Medicare, so the IRMAA tier-1 surcharge cliff is the MAGI threshold that
    actually costs it money, and it is the correct successor bound.

    The oracle is `conversion <= indexed tier-1 threshold`, recomputed here
    from IRMAA_TIERS_MFJ rather than reusing the engine's helper. Room is
    `max(tier1 - base_magi, 0)` and base_magi is non-negative, so the room --
    and hence the conversion -- can never exceed the threshold itself. That is
    a true bound with no fitted tolerance, and it separates the two cases by
    orders of magnitude: bounded conversions run ~$185K against a ~$218K
    threshold, while the unbounded regression converts millions.

    This deliberately pins the PLAN, not run_scenario's realised yr.magi.
    Post-65 these are the SAME conversions auto_fill_irmaa_safe produces (the
    22%-bracket cap is not binding there, so both strategies reduce to the
    tier-1 room), and those already land up to ~$115K above tier-1 once
    run_scenario adds income the autofill's simplified projection does not
    model. That autofill-vs-scenario drift predates this change and is shared
    with irmaa_safe; asserting on yr.magi here would pin that pre-existing
    drift instead of this fallback.
    """
    hh = Household(your_age=61, spouse_age=55, your_ira=10_000_000, spouse_ira=10_000_000)
    plan = auto_fill_aca(hh)

    checked = 0
    for year in sorted(set(plan.your_conversions) | set(plan.spouse_conversions)):
        if is_pre_medicare_age(hh.your_age_in(year)) or is_pre_medicare_age(
            hh.spouse_age_in(year)
        ):
            continue  # ACA cliff still binds -- covered by the test above
        conv = plan.your_conversions.get(year, 0.0) + plan.spouse_conversions.get(year, 0.0)
        if conv <= 0.0:
            continue  # strategy offered nothing this year -- not its claim
        checked += 1
        # IRMAA's 2-year lookback: the tier-1 ceiling for income year `year` is
        # indexed to the PAYMENT year, year + 2 (matches _irmaa_tier1_magi_room).
        tier1 = index_value(IRMAA_TIERS_MFJ[0][0], year + 2, hh.cpi_assumption)
        assert conv <= tier1, (
            f"year {year} (age {hh.your_age_in(year)}/{hh.spouse_age_in(year)}): post-Medicare "
            f"aca_safe conversion {conv:.0f} exceeds the indexed IRMAA tier-1 ceiling "
            f"{tier1:.0f} -- the post-65 room fell back to something looser than tier-1 "
            "(an unbounded room converts the whole remaining balance here)"
        )
    assert checked, (
        "precondition failed: no post-Medicare year had a positive aca_safe conversion, so "
        "this test did not exercise the fallback at all -- the fixture must keep at least "
        "one spouse under their RMD-start age after both turn 65"
    )
