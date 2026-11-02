"""Issues #95 and #104: the bracket-fill solvers (synthetic households only).

#95: add_bracket_fill_withdrawals sized every post-RMD year off ONE run of the
base plan, so the balances (and RMDs) it priced a later year on had not been
drawn down by the earlier years' fill withdrawals. Each later year's actual RMD
came in smaller than the one the room was sized against, and the year landed
under the bracket ceiling instead of on it.

#104: the Room This Year tiles (bracket_fill_conversions, i.e. the Fill
auto-fill plans) and the Sweet Spot Fill cards (forced_draw_fills ->
draw_aware_boundary) solved the base year with two different solvers and
disagreed by up to $0.58. The tiles must equal what Fill converts, so the cards
now take the base year from the same solver.
"""

from __future__ import annotations

import dataclasses

import pytest

from engine.scenario import run_scenario
from engine.scenario_autofill import (
    add_bracket_fill_withdrawals,
    base_year_bracket_fills,
    bracket_fill_conversions,
)
from engine.scenario_types import ConversionPlan
from engine.sweet_spot_compute import (
    base_income_for_year,
    compute_multi_year_summary,
    fills_for_year,
    forced_draw_fills,
    zero_conversion_gain_fractions,
    zero_conversion_sale_gains,
    zero_conversion_years,
)
from engine.tax import indexed_bracket_ceiling
from engine.this_year import draw_aware_bracket_rooms
from models.household import Household
from tests.test_issue_1_room_matches_fill import BASE_YEAR, _hh

# --------------------------------------------------------------------------
# #95: every uncapped fill year lands on the ceiling
# --------------------------------------------------------------------------


def _post_rmd_hh() -> Household:
    """In RMDs on a large IRA with no living costs: every year has room to fill."""
    return dataclasses.replace(
        Household(),
        your_age=75,
        spouse_age=75,
        your_ira=4_000_000.0,
        spouse_ira=200_000.0,
        your_ss_fra=2_500.0,
        spouse_ss_fra=1_500.0,
        grants=[],
        exercise_schedule=None,
        inherited_iras=[],
        cpi_assumption=0.025,
        brokerage_start=0.0,
        brokerage_start_basis=0.0,
        living_expenses=0.0,
        base_year=2026,
    )


def test_bracket_fill_lands_on_the_ceiling_in_every_uncapped_year() -> None:
    hh = _post_rmd_hh()
    plan = add_bracket_fill_withdrawals(hh, ConversionPlan(), target_bracket=0.22)
    res = run_scenario(hh, plan, "fill", end_age=95)
    checked = 0
    for yr in res.years:
        withdrawal = plan.extra_withdrawals.get(yr.year, 0.0)
        if not withdrawal:
            continue
        # Capped by the IRA balance: that year cannot reach the ceiling.
        if yr.your_ira_begin - yr.your_rmd - withdrawal < 1_000.0:
            continue
        ceiling = indexed_bracket_ceiling(
            2, year=yr.year, cpi=hh.cpi_assumption, filing_status=yr.filing_status
        )
        assert yr.taxable_income == pytest.approx(ceiling, abs=1.0), (yr.year, ceiling)
        checked += 1
    # Not vacuous: later years are where the base-run sizing undershot.
    assert checked >= 5


# --------------------------------------------------------------------------
# #104: the Sweet Spot cards equal the Room This Year tiles, exactly
# --------------------------------------------------------------------------


def _cards(hh: Household) -> tuple[tuple[float, float], bool]:
    """The base-year Fill cards exactly as views/sweet_spot.py derives them,
    plus whether the year was solved by the draw-aware path."""
    zero_years = zero_conversion_years(hh, ytd=None, net_inv_income=0.0)
    draws = {y: r.forced_your_ira_draw + r.forced_spouse_ira_draw for y, r in zero_years.items()}
    gains = zero_conversion_sale_gains(hh, zero_years)
    fills = forced_draw_fills(
        hh,
        ytd=None,
        net_inv_income=0.0,
        ira_draws=draws,
        sale_gains=gains,
        zero_years=zero_years,
    )
    zero = zero_years.get(hh.base_year)
    base = base_income_for_year(
        hh,
        hh.base_year,
        ira_draw=draws.get(hh.base_year, 0.0),
        sale_gain=gains.get(hh.base_year, 0.0),
        sale_gain_fraction=zero_conversion_gain_fractions(hh, zero_years).get(hh.base_year, 0.0),
        brokerage=zero.brokerage_balance if zero is not None else None,
    )
    return fills_for_year(hh, base, fills), hh.base_year in fills


_HOUSEHOLDS = {
    "forced-draw": lambda: _hh(),
    "fill-provoked-draw": lambda: _hh(brokerage_start=70_000.0),
    "no-draw": lambda: _hh(brokerage_start=2_000_000.0),
}


@pytest.mark.parametrize("name", list(_HOUSEHOLDS))
def test_sweet_spot_cards_equal_room_this_year_tiles(name: str) -> None:
    hh = _HOUSEHOLDS[name]()
    tiles = draw_aware_bracket_rooms(hh, None)
    cards, draw_aware = _cards(hh)
    assert cards == tiles
    assert tiles == bracket_fill_conversions(hh, None)[BASE_YEAR]
    if name != "no-draw":
        assert draw_aware  # the case #104 was about


@pytest.mark.parametrize("name", list(_HOUSEHOLDS))
def test_multi_year_table_base_row_equals_tiles(name: str) -> None:
    hh = _HOUSEHOLDS[name]()
    row = compute_multi_year_summary(hh)[0]
    assert row.year == BASE_YEAR
    assert (row.fill_12, row.fill_22) == draw_aware_bracket_rooms(hh, None)


def test_base_year_solver_matches_the_full_fill_plans() -> None:
    """The shared base-year solver is the first year of the Fill plans."""
    hh = _hh()
    assert base_year_bracket_fills(hh, None) == bracket_fill_conversions(hh, None)[BASE_YEAR]
