"""Issue #1: the displayed 12% / 22% room must match what "Fill X%" converts.

#598's Fill caps TOTAL taxable ordinary income -- conversion plus the
waterfall's forced IRA draw -- at the bracket top. The displayed rooms used to
ignore the draw, so in a forced-draw year they overstated the Fill. Every
surface now reports the draw-aware room: Room This Year (tiles and the
post-split rooms), Sweet Spot (cards and multi-year table), and the
Planner / Dashboard room columns (YearResult.room_12 / room_22, which are the
room left AFTER the planned conversion and so are ~0 once Fill is applied).

Synthetic households only.
"""

from __future__ import annotations

import pytest

from engine.exercise_optimizer import OptimizedPlan
from engine.headroom import compute_headroom
from engine.scenario import run_scenario
from engine.scenario_autofill import auto_fill_12, auto_fill_22, bracket_fill_conversions
from engine.scenario_types import ConversionPlan, YearResult
from engine.sweet_spot_compute import (
    base_income_for_year,
    compute_multi_year_summary,
    fill_conversions_for_year,
    fills_for_year,
    forced_draw_fills,
    zero_conversion_ira_draws,
)
from engine.tax import indexed_bracket_ceiling
from engine.this_year import draw_aware_bracket_rooms, summarize_this_year
from models.exercise_schedule import ExerciseSchedule
from models.household import Household
from models.ytd_income import YTDSnapshot

BASE_YEAR = 2026
TOL = 100.0  # matches the #598 autofill tolerance


def _hh(**kwargs: float) -> Household:
    fields: dict[str, object] = {
        "brokerage_start": 0.0,
        "living_expenses": 60_000.0,
        "your_ira": 900_000.0,
        "spouse_ira": 700_000.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
    }
    fields.update(kwargs)
    return Household(
        your_age=61,
        spouse_age=55,
        base_year=BASE_YEAR,
        cpi_assumption=0.0,
        grants=[],
        **fields,  # type: ignore[arg-type]
    )


def _forced_draw_hh() -> Household:
    """No brokerage and $60k of spending: the waterfall draws the IRA."""
    return _hh()


def _no_draw_hh() -> Household:
    """A big brokerage funds spending and tax: no IRA draw is forced."""
    return _hh(brokerage_start=2_000_000.0)


def _base(hh: Household, plan: ConversionPlan) -> YearResult:
    return run_scenario(hh, plan, "t", end_age=95).years[0]


def _draw(yr: YearResult) -> float:
    return yr.forced_your_ira_draw + yr.forced_spouse_ira_draw


def _fill_totals(hh: Household) -> tuple[float, float]:
    p12, p22 = auto_fill_12(hh, None), auto_fill_22(hh, None)

    def _total(p: ConversionPlan) -> float:
        return p.your_conversions.get(BASE_YEAR, 0.0) + p.spouse_conversions.get(BASE_YEAR, 0.0)

    return _total(p12), _total(p22)


def _plan(total: float, label: str) -> OptimizedPlan:
    sched = ExerciseSchedule()
    sched.set_price(BASE_YEAR, 200.0)
    conv = ConversionPlan(your_conversions={BASE_YEAR: total}, spouse_conversions={BASE_YEAR: 0.0})
    return OptimizedPlan(label, sched, conv, lifetime_all_in=0.0)


# --------------------------------------------------------------------------
# (a) forced-draw year: every displayed room == the matching Fill conversion
# --------------------------------------------------------------------------


def test_precondition_forced_draw_year_has_a_draw() -> None:
    hh = _forced_draw_hh()
    f12, f22 = _fill_totals(hh)
    assert 0 < f12 < f22
    assert _draw(_base(hh, auto_fill_12(hh, None))) > 0.0


def test_fill_helper_matches_auto_fill_cells() -> None:
    hh = _forced_draw_hh()
    f12, f22 = _fill_totals(hh)
    got12, got22 = bracket_fill_conversions(hh, None)[BASE_YEAR]
    assert got12 == pytest.approx(f12, abs=0.01)
    assert got22 == pytest.approx(f22, abs=0.01)


@pytest.mark.parametrize(("label", "level"), [("top-of-12", 1), ("top-of-22", 2)])
def test_room_this_year_room_after_is_zero_after_fill(label: str, level: int) -> None:
    """The split carries the Fill conversion: the room left, counting the
    forced draw, is ~0 (it was the draw's size before the fix)."""
    hh = _forced_draw_hh()
    totals = _fill_totals(hh)
    split = summarize_this_year(
        hh, None, _plan(totals[level - 1], label), current_plan=ConversionPlan()
    )
    assert _draw(_base(hh, _plan(totals[level - 1], label).conversions)) > 0.0
    room = split.room_after.to_12pct if level == 1 else split.room_after.to_22pct
    assert room == pytest.approx(0.0, abs=TOL)


def test_room_this_year_tiles_equal_fill_conversions() -> None:
    hh = _forced_draw_hh()
    headroom = compute_headroom(hh, YTDSnapshot(tax_year=BASE_YEAR), "MFJ")
    f12, f22 = _fill_totals(hh)
    # The draw-blind tiles overstate the Fill by (about) the draw.
    assert headroom.room_to_12pct > f12 + 10_000.0
    r12, r22 = draw_aware_bracket_rooms(hh, None, headroom)
    assert r12 == pytest.approx(f12, abs=TOL)
    assert r22 == pytest.approx(f22, abs=TOL)


def test_sweet_spot_fills_equal_auto_fill_conversions() -> None:
    hh = _forced_draw_hh()
    f12, f22 = _fill_totals(hh)
    row = compute_multi_year_summary(hh)[0]
    assert row.year == BASE_YEAR
    assert row.fill_12 == pytest.approx(f12, abs=TOL)
    assert row.fill_22 == pytest.approx(f22, abs=TOL)

    draws = zero_conversion_ira_draws(hh)
    assert draws[BASE_YEAR] > 0.0
    fills = forced_draw_fills(hh, ytd=None, net_inv_income=0.0, ira_draws=draws)
    base = base_income_for_year(hh, BASE_YEAR, ira_draw=draws[BASE_YEAR])
    card12, card22 = fills_for_year(hh, base, fills)
    assert card12 == pytest.approx(f12, abs=TOL)
    assert card22 == pytest.approx(f22, abs=TOL)


@pytest.mark.parametrize(("fill", "level"), [(auto_fill_12, 1), (auto_fill_22, 2)])
def test_planner_and_dashboard_room_is_zero_after_fill(fill, level: int) -> None:  # type: ignore[no-untyped-def]
    """YearResult.room_12 / room_22 (Planner + Dashboard columns) are the room
    left after the planned conversion, forced draw included: ~0 under Fill."""
    hh = _forced_draw_hh()
    yr = _base(hh, fill(hh, None))
    assert _draw(yr) > 0.0
    room = yr.room_12 if level == 1 else yr.room_22
    assert room == pytest.approx(0.0, abs=TOL)
    ceiling = indexed_bracket_ceiling(level, year=BASE_YEAR, cpi=0.0, filing_status="MFJ")
    assert yr.taxable_income == pytest.approx(ceiling, abs=TOL)


# --------------------------------------------------------------------------
# (b) no-draw year: unchanged
# --------------------------------------------------------------------------


def test_no_draw_year_tiles_unchanged() -> None:
    hh = _no_draw_hh()
    assert _draw(_base(hh, auto_fill_12(hh, None))) == 0.0
    headroom = compute_headroom(hh, YTDSnapshot(tax_year=BASE_YEAR), "MFJ")
    assert draw_aware_bracket_rooms(hh, None, headroom) == (
        headroom.room_to_12pct,
        headroom.room_to_22pct,
    )


def test_no_draw_year_sweet_spot_unchanged() -> None:
    hh = _no_draw_hh()
    row = compute_multi_year_summary(hh)[0]
    base = base_income_for_year(hh, BASE_YEAR)
    assert (row.fill_12, row.fill_22) == fill_conversions_for_year(hh, base)
    assert forced_draw_fills(hh, ytd=None, net_inv_income=0.0, ira_draws={BASE_YEAR: 0.0}) == {}


def test_no_draw_year_room_after_is_the_plain_scenario_room() -> None:
    hh = _no_draw_hh()
    plan = _plan(40_000.0, "top-of-22")
    split = summarize_this_year(hh, None, plan, current_plan=ConversionPlan())
    yr = _base(hh, plan.conversions)
    assert _draw(yr) == 0.0
    expected = indexed_bracket_ceiling(2, year=BASE_YEAR, cpi=0.0, filing_status="MFJ") - (
        yr.combined_gross - yr.total_deductions
    )
    assert split.room_after.to_22pct == pytest.approx(max(expected, 0.0))


# --------------------------------------------------------------------------
# (c) run_scenario's conversion cap stays draw-blind
# --------------------------------------------------------------------------


def test_manual_plan_conversion_is_not_reduced_by_the_draw() -> None:
    hh = _forced_draw_hh()
    yr = _base(hh, ConversionPlan(your_conversions={BASE_YEAR: 150_000.0}))
    ceiling = indexed_bracket_ceiling(2, year=BASE_YEAR, cpi=0.0, filing_status="MFJ")
    assert _draw(yr) > 0.0
    # The user's plan is honoured in full ...
    assert yr.your_conversion == pytest.approx(150_000.0)
    # ... even though conversion + draw together pass the 22% top.
    assert yr.taxable_income > ceiling
