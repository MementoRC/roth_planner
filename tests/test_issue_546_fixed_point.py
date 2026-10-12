"""Issue #546: Room This Year's split must be a fixed point of Apply.

Applying the recommended split and re-running Find recommended a different
mix every time (demo household: 71,350 + 13,250 -> 56,400 + 28,200 ->
39,525 + 45,075). ``_base_projection`` ran the household's STORED exercise
schedule through the living-expense waterfall, so the option cash the applied
schedule put in a year cut that year's forced IRA draws -- lowering
base_ordinary and freeing room that the next candidate filled with yet more
options.
"""

from __future__ import annotations

from engine.exercise_optimizer import _base_projection, optimize_exercises
from engine.scenario_types import ConversionPlan
from engine.this_year import (
    ThisYearSplit,
    apply_split_conversions,
    apply_split_schedule,
    summarize_this_year,
)
from models.exercise_schedule import ExerciseSchedule
from models.household import GrowthProfile, Household


def _demo_household() -> Household:
    """The first-run demo household: app.get_household() over registry defaults."""
    from config.scalar_registry import scalar_default as d

    return Household(
        your_age=d("your_age"),
        spouse_age=d("spouse_age"),
        your_has_workplace_plan=d("your_has_workplace_plan"),
        spouse_has_workplace_plan=d("spouse_has_workplace_plan"),
        your_ira=d("your_ira"),
        spouse_ira=d("spouse_ira"),
        your_roth=d("your_roth"),
        spouse_roth=d("spouse_roth"),
        your_ss_fra=d("your_ss_fra"),
        spouse_ss_fra=d("spouse_ss_fra"),
        growth_rate=d("growth_rate") / 100,
        living_expenses=d("living_expenses"),
        stock_price_now=d("stock_price"),
        stock_price_growth=GrowthProfile(default_rate=float(d("stock_price_growth_rate")) / 100),
        your_aca_enrolled=d("your_aca"),
        spouse_aca_enrolled=d("spouse_aca"),
        aca_benchmark_premium_annual=d("aca_benchmark_premium_annual"),
        aca_enhanced_subsidies_active=d("aca_enhanced_subsidies_active"),
        advance_aptc_annual=float(d("advance_aptc_annual")),
        medicare_part_b_base_monthly=d("medicare_part_b_base_monthly"),
        your_ss_start_age=d("your_ss_start_age"),
        spouse_ss_start_age=d("spouse_ss_start_age"),
        your_rmd_start_age=d("your_rmd_start_age"),
        spouse_rmd_start_age=d("spouse_rmd_start_age"),
        your_defer_first_rmd=d("your_defer_first_rmd"),
        spouse_defer_first_rmd=d("spouse_defer_first_rmd"),
        spouse_is_sole_beneficiary=d("spouse_is_sole_beneficiary"),
        your_fra_age=d("your_fra_age"),
        spouse_fra_age=d("spouse_fra_age"),
        cpi_assumption=float(d("cpi_assumption")),
        filing_status=d("filing_status"),
    )


def _mix(split: ThisYearSplit) -> tuple[dict[str, float], dict[str, float]]:
    conversions = {c.person: c.year_total for c in split.conversions}
    options = {g.grant_key: g.year_total_income for g in split.exercises if g.year_total_income}
    return conversions, options


def _find(hh: Household, current: ConversionPlan) -> tuple[ThisYearSplit, object]:
    best = optimize_exercises(hh, current_plan=current, ytd=None).best
    return summarize_this_year(hh, None, best, current_plan=current), best


def _apply(hh: Household, current: ConversionPlan, split, best) -> ConversionPlan:
    """What views/room_this_year._apply_split writes, minus session state."""
    from views.planner import grid_normalized_conversions

    applied = apply_split_conversions(current, split)
    your, spouse = grid_normalized_conversions(
        hh, applied.your_conversions, applied.spouse_conversions
    )
    hh.exercise_schedule = apply_split_schedule(hh, best)
    return ConversionPlan(your_conversions=dict(your), spouse_conversions=dict(spouse))


def _assert_same_mix(a: ThisYearSplit, b: ThisYearSplit) -> None:
    conv_a, opt_a = _mix(a)
    conv_b, opt_b = _mix(b)
    assert conv_a.keys() == conv_b.keys()
    assert opt_a.keys() == opt_b.keys()
    for person in conv_a:
        assert abs(conv_a[person] - conv_b[person]) <= 1.0, (person, conv_a, conv_b)
    for key in opt_a:
        assert abs(opt_a[key] - opt_b[key]) <= 1.0, (key, opt_a, opt_b)


def test_find_after_apply_recommends_the_same_split() -> None:
    hh = _demo_household()
    current = ConversionPlan()
    first, best = _find(hh, current)
    current = _apply(hh, current, first, best)

    second, best = _find(hh, current)
    _assert_same_mix(first, second)

    # And it stays put on a further round.
    current = _apply(hh, current, second, best)
    third, _ = _find(hh, current)
    _assert_same_mix(first, third)


def test_base_projection_ignores_the_stored_schedule() -> None:
    hh = _demo_household()
    baseline = _base_projection(hh)

    stored = ExerciseSchedule()
    grant = hh.grants[0]
    stored.set_shares(grant.key(), hh.base_year, grant.shares // 2)
    stored.set_shares(grant.key(), grant.expiry_year, grant.shares - grant.shares // 2)
    stored.set_price(hh.base_year, hh.projected_stock_price(hh.base_year))
    stored.set_price(grant.expiry_year, hh.projected_stock_price(grant.expiry_year))
    hh.exercise_schedule = stored

    assert _base_projection(hh) == baseline
    # The caller's stored schedule is untouched.
    assert hh.exercise_schedule is stored
    assert stored.shares(grant.key(), hh.base_year) == grant.shares // 2
