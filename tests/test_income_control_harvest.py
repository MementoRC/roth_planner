"""Income Control slice 3 (#123, #130): realized gains, the 0% LTCG room wiring,
embedded gain and harvest wiring. Synthetic data only."""

from __future__ import annotations

import pytest

from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan, YearResult
from models.household import GrowthProfile, Household
from models.ytd_income import YTDSnapshot
from tests.test_income_control import _hh

_GROWTH = GrowthProfile(default_rate=0.10, yield_rate=0.0)  # 10% appreciation, no dividends
_START, _BASIS, _TURNOVER = 200_000.0, 120_000.0, 0.5


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
    assert yr.realized_gains == pytest.approx(yr.brokerage_balance * 0.10 * _TURNOVER)


def test_realized_gains_is_the_turnover_gain_in_a_base_year_without_actuals() -> None:
    yr = _years(_brok_hh())[0]
    assert yr.realized_gains == pytest.approx(_START * 0.10 * _TURNOVER)


def test_realized_gains_is_zero_in_the_base_year_with_actuals_and_no_forced_draw() -> None:
    years = _years(_brok_hh(), _snapshot())
    assert years[0].forced_brokerage_draw == 0.0
    assert years[0].realized_gains == 0.0
    assert years[1].realized_gains > 0  # forecast turnover resumes after the base year


def test_realized_gains_adds_the_forced_draw_gain_to_the_turnover_gain() -> None:
    yr = _years(_brok_hh(living_expenses=150_000.0))[0]
    assert yr.forced_brokerage_draw > 0  # precondition: brokerage funds expenses first
    draw_gain = yr.forced_brokerage_draw * (1 - _BASIS / _START)
    assert yr.realized_gains == pytest.approx(_START * 0.10 * _TURNOVER + draw_gain)


def test_realized_gains_keeps_the_forced_draw_gain_in_the_base_year_with_actuals() -> None:
    yr = _years(_brok_hh(living_expenses=150_000.0), _snapshot())[0]
    assert yr.forced_brokerage_draw > 0
    assert yr.realized_gains == pytest.approx(yr.forced_brokerage_draw * (1 - _BASIS / _START))
