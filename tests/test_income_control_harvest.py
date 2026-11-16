"""Income Control slice 3 (#123, #130): realized gains, the 0% LTCG room wiring,
embedded gain and harvest wiring. Synthetic data only."""

from __future__ import annotations

import pytest

from engine.income_control import build_income_control
from engine.room_lines import room_after, room_lines_for_year
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan, YearResult
from models.household import GrowthProfile, Household
from models.ytd_income import YTDSnapshot
from tests.test_income_control import _hh

_GROWTH = GrowthProfile(default_rate=0.10, yield_rate=0.0)  # 10% appreciation, no dividends
_START, _BASIS, _TURNOVER = 200_000.0, 120_000.0, 0.5
# Exceeds the turnover gain (200,000 x 10% x 50% = 10,000), so it forces a brokerage draw.
_DRAW_EXPENSES = 150_000.0


def _brok_hh(**kw: object) -> Household:
    """Brokerage 200,000 at basis 120,000, 10% appreciation, 50% turnover. No
    expenses, no ACA, no income: nothing forces a brokerage draw."""
    base: dict[str, object] = {
        "brokerage_start": _START,
        "brokerage_start_basis": _BASIS,
        "brokerage_growth": _GROWTH,
        "brok_turnover": _TURNOVER,
        "living_expenses": 0.0,
        "your_aca_enrolled": False,
        "spouse_aca_enrolled": False,
    }
    base.update(kw)
    return _hh(**base)


def _snapshot(**kw: float) -> YTDSnapshot:
    return YTDSnapshot(tax_year=2026, snapshot_date="2026-06-30", **kw)


def _years(hh: Household, ytd: YTDSnapshot | None = None) -> list[YearResult]:
    return run_scenario(hh, ConversionPlan(), "x", end_age=64, ytd=ytd).years


def test_realized_gains_defaults_to_zero() -> None:
    assert YearResult(year=2026, your_age=61, spouse_age=58, phase="clean").realized_gains == 0.0


def test_realized_gains_is_the_turnover_gain_in_a_non_base_year() -> None:
    yr = _years(_brok_hh())[1]
    assert yr.forced_brokerage_draw == 0.0  # precondition: turnover only
    assert yr.realized_gains > 0
    expected = yr.brokerage_balance * _GROWTH.default_rate * _TURNOVER
    assert yr.realized_gains == pytest.approx(expected)


def test_realized_gains_is_the_turnover_gain_in_a_base_year_without_actuals() -> None:
    yr = _years(_brok_hh())[0]
    assert yr.realized_gains == pytest.approx(_START * _GROWTH.default_rate * _TURNOVER)


def test_realized_gains_is_zero_in_the_base_year_with_actuals_and_no_forced_draw() -> None:
    years = _years(_brok_hh(), _snapshot())
    assert years[0].forced_brokerage_draw == 0.0
    assert years[0].realized_gains == 0.0
    assert years[1].realized_gains > 0  # forecast turnover resumes after the base year


def test_realized_gains_adds_the_forced_draw_gain_to_the_turnover_gain() -> None:
    yr = _years(_brok_hh(living_expenses=_DRAW_EXPENSES))[0]
    assert yr.forced_brokerage_draw > 0  # precondition: brokerage funds expenses first
    draw_gain = yr.forced_brokerage_draw * (1 - _BASIS / _START)
    turnover_gain = _START * _GROWTH.default_rate * _TURNOVER
    assert yr.realized_gains == pytest.approx(turnover_gain + draw_gain)


def test_realized_gains_keeps_the_forced_draw_gain_in_the_base_year_with_actuals() -> None:
    yr = _years(_brok_hh(living_expenses=_DRAW_EXPENSES), _snapshot())[0]
    assert yr.forced_brokerage_draw > 0
    assert yr.realized_gains == pytest.approx(yr.forced_brokerage_draw * (1 - _BASIS / _START))


def test_ltcg_room_base_row_gets_the_snapshot_and_later_rows_do_not() -> None:
    hh = _brok_hh()
    ytd = _snapshot(qualified_dividends_ytd=2_000.0, ltcg_ytd=5_000.0)
    first, second = build_income_control(hh, ConversionPlan(), plan_label="x", ytd=ytd).years[:2]
    lines = room_lines_for_year(hh, first.year_result)
    assert first.rooms == room_after(first.year_result, lines, ytd)
    assert first.rooms["ltcg_zero"] == pytest.approx(
        room_after(first.year_result, lines)["ltcg_zero"] - 7_000.0
    )
    yr2 = second.year_result
    assert second.rooms == room_after(yr2, room_lines_for_year(hh, yr2))


def test_ltcg_room_drops_by_the_realized_gains_in_a_turnover_year() -> None:
    hh = _brok_hh()
    row = build_income_control(hh, ConversionPlan(), plan_label="x").years[1]
    yr = row.year_result
    unused = max(yr.total_deductions - yr.combined_gross, 0.0)
    measure = yr.taxable_income + yr.brokerage_qual_div + yr.realized_gains - unused
    assert yr.realized_gains > 0
    assert row.rooms["ltcg_zero"] == pytest.approx(room_lines_for_year(hh, yr).ltcg_zero - measure)
