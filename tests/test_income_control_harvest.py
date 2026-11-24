"""Income Control slice 3 (#123, #130): realized gains, the 0% LTCG room wiring,
embedded gain and harvest wiring. Synthetic data only."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine import income_control as ic
from engine.income_control import build_income_control
from engine.room_lines import harvest_sizing, room_after, room_lines_for_year
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


def _yr(**kw: float | int | str) -> YearResult:
    base: dict[str, float | int | str] = {
        "year": 2026,
        "your_age": 61,
        "spouse_age": 58,
        "phase": "clean",
        "filing_status": "MFJ",
    }
    base.update(kw)
    return YearResult(**base)  # type: ignore[arg-type]


def test_embedded_gain_is_the_year_end_gain() -> None:
    # #123 C5 (owner decision): closing balance less closing basis, not opening figures.
    yr = _yr(
        brokerage_balance=180_000.0,
        realized_gains=10_000.0,
        brokerage_balance_end=200_000.0,
        brokerage_basis=130_000.0,
    )
    assert ic._embedded_gain(yr, _BASIS, None) == 70_000.0


def test_embedded_gain_later_rows_read_their_own_closing_figures() -> None:
    yr = _yr(year=2027, brokerage_balance_end=210_000.0, brokerage_basis=155_000.0)
    assert ic._embedded_gain(yr, 150_000.0, None) == 55_000.0


def test_embedded_gain_base_year_subtracts_the_ytd_long_term_gain() -> None:
    yr = _yr(brokerage_balance_end=200_000.0, brokerage_basis=_BASIS)
    ytd = _snapshot(ltcg_ytd=4_000.0)
    assert ic._embedded_gain(yr, _BASIS, ytd) == 76_000.0


def test_embedded_gain_ignores_a_net_capital_loss() -> None:
    yr = _yr(brokerage_balance_end=200_000.0, brokerage_basis=_BASIS)
    assert ic._embedded_gain(yr, _BASIS, _snapshot(ltcg_ytd=-8_000.0)) == 80_000.0


def test_embedded_gain_is_none_when_the_basis_is_unknown() -> None:
    yr = _yr(brokerage_balance=200_000.0)
    # the scenario resolves None to full basis; the harvest must not read it
    assert ic._embedded_gain(yr, None, None) is None


def test_embedded_gain_never_goes_negative() -> None:
    yr = _yr(brokerage_balance_end=100_000.0, brokerage_basis=_BASIS)
    assert ic._embedded_gain(yr, _BASIS, None) == 0.0


def test_harvest_rows_are_wired_with_the_previous_rows_closing_basis() -> None:
    hh = _brok_hh()
    rows = build_income_control(hh, ConversionPlan(), plan_label="x").years
    opening = hh.brokerage_start_basis
    for row in rows:
        yr = row.year_result
        embedded = ic._embedded_gain(yr, opening, None)
        expected = harvest_sizing(
            yr, room_lines_for_year(hh, yr), row.rooms, embedded, hh.cpi_assumption, None
        )
        assert row.harvest == expected
        opening = yr.brokerage_basis
    assert rows[0].harvest is not None
    assert rows[0].harvest.basis_known is True


def test_harvest_basis_carries_across_a_year_missing_from_the_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hh = _brok_hh()
    full = build_income_control(hh, ConversionPlan(), plan_label="x").years
    assert len(full) >= 4  # precondition: a middle year exists to drop
    gap_year = full[1].year
    real_run = ic.run_scenario

    def gapped(*args: object, **kw: object) -> SimpleNamespace:
        res = real_run(*args, **kw)  # type: ignore[arg-type]
        return SimpleNamespace(years=[y for y in res.years if y.year != gap_year])

    monkeypatch.setattr(ic, "run_scenario", gapped)
    rows = build_income_control(hh, ConversionPlan(), plan_label="x").years
    assert gap_year not in [r.year for r in rows]
    after = next(r for r in rows if r.year == gap_year + 1)
    assert after.harvest is not None
    assert after.harvest.basis_known is True
    prev_closing = next(r for r in rows if r.year == gap_year - 1).year_result.brokerage_basis
    expected = ic._embedded_gain(after.year_result, prev_closing, None)
    assert after.harvest == harvest_sizing(
        after.year_result,
        room_lines_for_year(hh, after.year_result),
        after.rooms,
        expected,
        hh.cpi_assumption,
        None,
    )


def test_harvest_base_row_uses_the_snapshot() -> None:
    hh = _brok_hh()
    ytd = _snapshot(qualified_dividends_ytd=2_000.0, ltcg_ytd=5_000.0)
    row = build_income_control(hh, ConversionPlan(), plan_label="x", ytd=ytd).years[0]
    yr = row.year_result
    embedded = ic._embedded_gain(yr, _BASIS, ytd)
    expected = harvest_sizing(
        yr, room_lines_for_year(hh, yr), row.rooms, embedded, hh.cpi_assumption, ytd
    )
    assert row.harvest == expected


def test_harvest_rows_with_an_unknown_basis_are_uncapped() -> None:
    rows = build_income_control(
        _brok_hh(brokerage_start_basis=None), ConversionPlan(), plan_label="x"
    ).years
    for row in rows:
        assert row.harvest is not None
        assert row.harvest.basis_known is False
        assert row.harvest.amount == row.harvest.room_uncapped


def test_harvest_defaults_to_none_on_hand_built_rows() -> None:
    from tests.test_income_control_view import _year

    assert _year(2026).harvest is None
