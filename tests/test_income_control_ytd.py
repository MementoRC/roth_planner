"""Income Control slice 2: YTD actuals in the stack (#123). Synthetic data only."""

from __future__ import annotations

import pytest

from engine import income_control as ic
from engine.income_control import StackLine
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan, YearResult
from models.household import GrowthProfile
from models.ytd_income import YTDSnapshot
from tests.test_income_control import _hh
from tests.test_roth_distribution_earnings import _household


def _roth_year(first_roth_year: int) -> YearResult:
    """The proven fixture from tests/test_roth_distribution_earnings.py
    (TestEarningsTaxEndToEnd._run): a Roth that doubles each year funds large
    living expenses, so the second projected year draws well over $100,000 of
    earnings; first_roth_year=2026 makes them non-qualified (ordinary income),
    2015 makes them qualified (not income)."""
    hh = _household(
        your_age=65,
        spouse_age=65,
        your_roth=1_000_000.0,
        spouse_roth=0.0,
        living_expenses=600_000.0,
        your_roth_growth=GrowthProfile(default_rate=1.0, yield_rate=0.0),
        your_first_roth_year=first_roth_year,
    )
    return run_scenario(hh, ConversionPlan(), end_age=67).years[1]


def test_roth_earnings_income_defaults_to_zero() -> None:
    yr = YearResult(year=2026, your_age=61, spouse_age=58, phase="clean")
    assert yr.roth_earnings_income == 0.0


def test_roth_earnings_income_is_the_non_qualified_earnings_in_combined_gross() -> None:
    yr = _roth_year(first_roth_year=2026)
    assert yr.forced_your_roth_earnings_draw > 100_000.0  # known from the proven fixture
    assert yr.roth_earnings_income > 0
    assert yr.roth_earnings_income == pytest.approx(
        yr.forced_your_roth_earnings_draw + yr.forced_spouse_roth_earnings_draw
    )
    assert yr.combined_gross >= yr.roth_earnings_income


def test_qualified_roth_earnings_are_not_exposed_as_income() -> None:
    yr = _roth_year(first_roth_year=2015)
    assert yr.forced_your_roth_earnings_draw > 0
    assert yr.roth_earnings_income == 0.0


def test_stack_line_full_year_is_done_plus_remaining() -> None:
    line = StackLine(
        "conversion", "Planned conversion", done=20_000.0, remaining=30_000.0, plan=50_000.0
    )
    assert line.full_year == 50_000.0
    assert line.above_plan == 0.0


def test_stack_line_above_plan_when_full_year_exceeds_plan() -> None:
    line = StackLine(
        "conversion", "Planned conversion", done=70_000.0, remaining=0.0, plan=50_000.0
    )
    assert line.above_plan == 20_000.0


def test_stack_line_never_reports_below_plan() -> None:
    line = StackLine(
        "conversion", "Planned conversion", done=0.0, remaining=10_000.0, plan=50_000.0
    )
    assert line.above_plan == 0.0


def test_stack_line_without_plan_has_no_above_plan_and_defaults() -> None:
    line = StackLine("wages", "Wages", done=30_000.0, remaining=0.0)
    assert line.plan is None
    assert line.magi_only is False
    assert line.above_plan == 0.0


def test_stack_line_ignores_float_noise_above_plan() -> None:
    line = StackLine("rmd", "RMD", done=0.1 + 0.2, remaining=0.0, plan=0.3)
    assert line.above_plan == 0.0


def test_stack_line_reports_excess_just_above_the_tolerance() -> None:
    line = StackLine("rmd", "RMD", done=0.31, remaining=0.0, plan=0.30)
    assert line.above_plan > 0.0


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


def _ytd(**kw: float) -> YTDSnapshot:
    return YTDSnapshot(tax_year=2026, snapshot_date="2026-06-30", **kw)


def _line(row: ic.IncomeControlYear, key: str) -> StackLine:
    return next(line for line in row.stack_lines if line.key == key)


def test_forecast_only_lines_without_a_snapshot() -> None:
    yr = _yr(
        combined_ss=24_000.0,
        forced_your_ira_draw=10_000.0,
        your_inherited_distribution=2_000.0,
        extra_withdrawal=7_000.0,
        spouse_extra_withdrawal=3_000.0,
        roth_earnings_income=4_000.0,
    )
    row = ic._row(_hh(), yr)
    assert row.has_actuals is False
    assert (_line(row, "social_security").done, _line(row, "social_security").remaining) == (
        0.0,
        24_000.0,
    )
    assert _line(row, "ira_draws").remaining == 12_000.0
    assert _line(row, "extra_withdrawals").remaining == 10_000.0
    assert _line(row, "roth_earnings").remaining == 4_000.0
    assert all(line.done == 0.0 for line in row.stack_lines)


def test_rmd_line_splits_done_and_remaining_against_the_unreduced_plan() -> None:
    # unreduced RMD 40,000; 30,000 already distributed -> engine reduced taxable_rmd to 10,000
    yr = _yr(your_rmd=40_000.0, taxable_rmd=10_000.0)
    row = ic._row(_hh(), yr, None, _ytd(ira_distributions_ytd=30_000.0))
    line = _line(row, "rmd")
    assert (line.done, line.remaining, line.plan) == (30_000.0, 10_000.0, 40_000.0)
    assert line.full_year == pytest.approx(40_000.0)
    assert line.above_plan == 0.0
    assert row.has_actuals is True


def test_rmd_line_above_plan_when_distributions_exceed_the_rmd() -> None:
    yr = _yr(your_rmd=40_000.0, taxable_rmd=0.0)
    line = _line(ic._row(_hh(), yr, None, _ytd(ira_distributions_ytd=55_000.0)), "rmd")
    assert (line.done, line.remaining, line.above_plan) == (55_000.0, 0.0, 15_000.0)


def test_rmd_plan_uses_the_clamped_per_person_form_including_qcd_above_rmd() -> None:
    # you: rmd 10,000, qcd 25,000 -> taxable 0 (qcd clamps to the rmd); spouse: 20,000 - 5,000 = 15,000
    yr = _yr(
        your_rmd=10_000.0,
        qcd=25_000.0,
        taxable_rmd=0.0,
        spouse_rmd=20_000.0,
        spouse_qcd=5_000.0,
        spouse_taxable_rmd=15_000.0,
    )
    line = _line(ic._row(_hh(), yr, None, _ytd()), "rmd")
    assert (line.done, line.remaining, line.plan) == (0.0, 15_000.0, 15_000.0)
    assert line.above_plan == 0.0


def test_rmd_line_remaining_sums_both_people() -> None:
    yr = _yr(
        your_rmd=8_000.0, taxable_rmd=8_000.0, spouse_rmd=5_000.0, spouse_taxable_rmd=5_000.0
    )
    assert _line(ic._row(_hh(), yr), "rmd").remaining == 13_000.0
