"""Income Control slice 2: YTD actuals in the stack (#123). Synthetic data only."""

from __future__ import annotations

import pytest

from engine import income_control as ic
from engine.income_control import StackLine, build_income_control
from engine.scenario import run_scenario
from engine.scenario_compute import compute_conversions, compute_rmds
from engine.scenario_types import ConversionPlan, YearResult
from models.household import GrowthProfile
from models.ytd_income import YTDSnapshot
from tests.test_income_control import _hh, _schedule_hh
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
    yr = _yr(your_rmd=8_000.0, taxable_rmd=8_000.0, spouse_rmd=5_000.0, spouse_taxable_rmd=5_000.0)
    assert _line(ic._row(_hh(), yr), "rmd").remaining == 13_000.0


def test_dividend_lines_are_done_only_with_no_plan_and_no_above_plan_note() -> None:
    ytd = _ytd(qualified_dividends_ytd=1_200.0, ordinary_dividends_ytd=800.0)
    row = ic._row(_hh(), _yr(), None, ytd)  # base-year forecast dividends are 0 on the YearResult
    ordinary, qualified = _line(row, "dividends_ordinary"), _line(row, "dividends_qualified")
    assert (ordinary.done, ordinary.remaining, ordinary.plan) == (800.0, 0.0, None)
    assert (qualified.done, qualified.remaining, qualified.plan) == (1_200.0, 0.0, None)
    assert ordinary.magi_only is False
    assert qualified.magi_only is True
    assert ordinary.above_plan == qualified.above_plan == 0.0


def test_dividend_lines_carry_the_forecast_without_a_snapshot() -> None:
    yr = _yr(brokerage_ord_div=500.0, brokerage_qual_div=1_000.0)
    row = ic._row(_hh(), yr)
    assert (_line(row, "dividends_ordinary").done, _line(row, "dividends_ordinary").remaining) == (
        0.0,
        500.0,
    )
    assert _line(row, "dividends_qualified").remaining == 1_000.0


def test_capital_gain_parts_come_from_the_netted_snapshot_properties() -> None:
    ytd = _ytd(stcg_ytd=3_000.0, crypto_stcg_ytd=500.0, ltcg_ytd=10_000.0)
    row = ic._row(_hh(), _yr(), None, ytd)
    ordinary, long_term = _line(row, "capital_gain_ordinary"), _line(row, "capital_gain_long_term")
    assert ordinary.done == ytd.ordinary_capital_gain_ytd == 3_500.0
    assert long_term.done == ytd.preferential_capital_gain_ytd == 10_000.0
    assert (ordinary.magi_only, long_term.magi_only) == (False, True)
    assert ordinary.plan is None
    assert long_term.plan is None


def test_net_capital_loss_shows_the_capped_negative_ordinary_part() -> None:
    row = ic._row(_hh(), _yr(), None, _ytd(ltcg_ytd=-8_000.0))
    assert _line(row, "capital_gain_ordinary").done == -3_000.0
    assert _line(row, "capital_gain_long_term").done == 0.0


def test_without_a_snapshot_only_the_forecast_lines_exist() -> None:
    keys = {line.key for line in ic._row(_hh(), _yr()).stack_lines}
    assert keys == {
        "social_security",
        "rmd",
        "ira_draws",
        "extra_withdrawals",
        "roth_earnings",
        "dividends_ordinary",
        "dividends_qualified",
        "forced_exercise",
        "discretionary_exercise",
        "conversion",
    }


@pytest.mark.parametrize("qcd_planned", [5_000.0, 500_000.0])  # qcd < rmd, qcd > rmd
def test_clamped_rmd_matches_compute_rmds(qcd_planned: float) -> None:
    your_rmd, qcd, taxable, spouse_rmd, spouse_qcd, spouse_taxable = compute_rmds(
        400_000.0, 300_000.0, 75, 75, 73, 73, qcd_planned, qcd_planned, 1_000_000.0
    )
    assert your_rmd > 0
    assert ic._clamped_rmd(your_rmd, qcd) == taxable
    assert ic._clamped_rmd(spouse_rmd, spouse_qcd) == spouse_taxable


@pytest.mark.parametrize(
    ("your_age", "spouse_age"),
    [(61, 58), (_hh().your_rmd_start_age, _hh().spouse_rmd_start_age)],
)  # before and at the RMD start age
def test_planned_conversion_matches_compute_conversions(your_age: int, spouse_age: int) -> None:
    hh = _hh()
    plan = ConversionPlan(your_conversions={2026: 50_000.0}, spouse_conversions={2026: 10_000.0})
    yr = _yr(your_age=your_age, spouse_age=spouse_age)
    engine = compute_conversions(
        2026,
        your_age,
        spouse_age,
        50_000.0,
        10_000.0,
        None,
        hh.your_rmd_start_age,
        hh.spouse_rmd_start_age,
    )
    assert ic._planned_conversion(hh, yr, plan) == sum(engine)


def test_actual_only_income_lines_carry_the_snapshot_values() -> None:
    ytd = _ytd(
        wages_ytd=30_000.0,
        nec_income_ytd=2_000.0,
        interest_ytd=500.0,
        crypto_income_ytd=700.0,
        hsa_contribution_ytd=4_000.0,
        deductible_ira_contribution_ytd=1_000.0,
    )
    row = ic._row(_hh(), _yr(), None, ytd)
    for key, value in (
        ("wages", 30_000.0),
        ("nec", 2_000.0),
        ("interest", 500.0),
        ("crypto_income", 700.0),
    ):
        line = _line(row, key)
        assert (line.done, line.remaining, line.plan, line.magi_only) == (value, 0.0, None, False)
    adjustments = _line(row, "adjustments")
    assert (adjustments.done, adjustments.remaining, adjustments.plan) == (-5_000.0, 0.0, None)


def _opt_row(nqo: float) -> ic.IncomeControlYear:
    # _schedule_hh: g1 forced 60,000 in 2028; total 2028 option income 70,000.
    yr = _yr(year=2028, option_income=70_000.0)
    return ic._row(_schedule_hh(), yr, None, _ytd(nqo_exercise_ytd=nqo))


def test_options_below_the_forced_amount_fill_forced_first() -> None:
    row = _opt_row(50_000.0)
    forced, disc = _line(row, "forced_exercise"), _line(row, "discretionary_exercise")
    assert (forced.done, forced.remaining, forced.plan) == (50_000.0, 10_000.0, 60_000.0)
    assert (disc.done, disc.remaining, disc.plan) == (0.0, 10_000.0, 10_000.0)
    assert forced.full_year + disc.full_year == 70_000.0  # max(plan, done)


def test_options_past_the_forced_amount_spill_into_discretionary() -> None:
    row = _opt_row(65_000.0)
    forced, disc = _line(row, "forced_exercise"), _line(row, "discretionary_exercise")
    assert (forced.done, forced.remaining) == (60_000.0, 0.0)
    assert (disc.done, disc.remaining) == (5_000.0, 5_000.0)
    assert forced.above_plan == disc.above_plan == 0.0
    assert forced.full_year + disc.full_year == 70_000.0


def test_options_above_plan_show_the_excess_on_discretionary() -> None:
    row = _opt_row(90_000.0)
    forced, disc = _line(row, "forced_exercise"), _line(row, "discretionary_exercise")
    assert (forced.remaining, disc.done, disc.remaining) == (0.0, 30_000.0, 0.0)
    assert (forced.above_plan, disc.above_plan) == (0.0, 20_000.0)
    assert forced.full_year + disc.full_year == 90_000.0  # == max(70,000, 90,000)


def test_option_lines_without_a_snapshot_match_slice_one_values() -> None:
    row = ic._row(_schedule_hh(), _yr(year=2028, option_income=70_000.0))
    assert (row.forced_exercise, row.discretionary_exercise) == (60_000.0, 10_000.0)
    assert _line(row, "forced_exercise").remaining == 60_000.0
    assert _line(row, "discretionary_exercise").remaining == 10_000.0


def test_conversion_line_sums_both_people_and_sets_the_plan_from_the_plan_dict() -> None:
    hh = _hh()
    plan = ConversionPlan(your_conversions={2026: 100_000.0}, spouse_conversions={2026: 20_000.0})
    # engine: your 100,000 - 40,000 done = 60,000 remaining; spouse 20,000 - 20,000 = 0
    yr = _yr(your_conversion=60_000.0, spouse_conversion=0.0)
    ytd = _ytd(ira_conversions_ytd=40_000.0, spouse_ira_conversions_ytd=20_000.0)
    row = ic._row(hh, yr, plan, ytd)
    line = _line(row, "conversion")
    assert (line.done, line.remaining, line.plan) == (60_000.0, 60_000.0, 120_000.0)
    assert line.above_plan == 0.0
    assert row.planned_conversion == 60_000.0  # slice 1 field stays the remaining value


def test_conversion_above_plan_is_flagged() -> None:
    plan = ConversionPlan(your_conversions={2026: 100_000.0})
    row = ic._row(_hh(), _yr(your_conversion=0.0), plan, _ytd(ira_conversions_ytd=130_000.0))
    assert _line(row, "conversion").above_plan == 30_000.0


def test_conversion_below_plan_has_no_note() -> None:
    # remaining can fall short of plan for non-YTD reasons (balance clamp, cap scaling)
    plan = ConversionPlan(your_conversions={2026: 100_000.0})
    row = ic._row(_hh(), _yr(your_conversion=40_000.0), plan, _ytd(ira_conversions_ytd=10_000.0))
    assert _line(row, "conversion").full_year == 50_000.0
    assert _line(row, "conversion").above_plan == 0.0


def test_conversion_plan_is_zeroed_at_each_persons_rmd_start_age() -> None:
    hh = _hh()
    plan = ConversionPlan(your_conversions={2026: 50_000.0}, spouse_conversions={2026: 10_000.0})
    yr = _yr(
        your_age=hh.your_rmd_start_age,
        spouse_age=58,
        your_conversion=0.0,
        spouse_conversion=10_000.0,
    )
    line = _line(ic._row(hh, yr, plan, _ytd()), "conversion")
    assert line.plan == 10_000.0  # your 50,000 is zeroed at the RMD start age
    assert line.above_plan == 0.0


def test_conversion_line_without_a_snapshot_is_forecast_only() -> None:
    row = ic._row(_hh(), _yr(your_conversion=25_000.0, spouse_conversion=5_000.0))
    line = _line(row, "conversion")
    assert (line.done, line.remaining, line.plan) == (0.0, 30_000.0, 30_000.0)


def test_total_income_is_the_sum_of_line_full_years() -> None:
    plan = ConversionPlan(your_conversions={2026: 50_000.0})
    ytd = _ytd(
        wages_ytd=30_000.0,
        ordinary_dividends_ytd=800.0,
        ltcg_ytd=10_000.0,
        ira_conversions_ytd=20_000.0,
    )
    row = build_income_control(_hh(), plan, plan_label="x", ytd=ytd).years[0]
    assert row.total_income == pytest.approx(sum(line.full_year for line in row.stack_lines))
    assert row.total_income > row.fixed_income + row.planned_conversion  # actuals add on top


def test_magi_only_total_is_long_term_gains_plus_qualified_dividends() -> None:
    ytd = _ytd(ltcg_ytd=10_000.0, qualified_dividends_ytd=1_200.0, ordinary_dividends_ytd=800.0)
    row = ic._row(_hh(), _yr(), None, ytd)
    assert row.magi_only_total == ytd.preferential_capital_gain_ytd + ytd.qualified_dividends_ytd
    assert row.magi_only_total == 11_200.0


def test_legacy_interest_and_dividend_fields_agree_with_the_snapshot_lines() -> None:
    ytd = _ytd(
        interest_ytd=500.0, ordinary_dividends_ytd=800.0, qualified_dividends_ytd=1_200.0
    )
    row = ic._row(_hh(), _yr(), None, ytd)
    assert row.interest == _line(row, "interest").full_year == 500.0
    assert row.dividends_ordinary == _line(row, "dividends_ordinary").full_year == 800.0
    assert row.dividends_qualified == _line(row, "dividends_qualified").full_year == 1_200.0


def test_rows_without_a_snapshot_are_forecast_only_with_slice_one_values() -> None:
    hh = _schedule_hh()
    plan = ConversionPlan(your_conversions={2027: 40_000.0, 2028: 25_000.0})
    result = build_income_control(hh, plan, plan_label="x")
    for row in result.years:
        assert row.has_actuals is False
        assert all(line.done == 0.0 for line in row.stack_lines)
        keys = {line.key for line in row.stack_lines}
        assert keys.isdisjoint({"wages", "capital_gain_ordinary", "adjustments"})
        assert row.total_income == pytest.approx(
            row.fixed_income
            + row.forced_exercise
            + row.discretionary_exercise
            + row.planned_conversion
        )


def test_non_base_years_stay_forecast_only_even_with_a_snapshot() -> None:
    result = build_income_control(
        _schedule_hh(), ConversionPlan(), plan_label="x", ytd=_ytd(wages_ytd=30_000.0)
    )
    assert result.years[0].has_actuals is True
    assert _line(result.years[0], "wages").done == 30_000.0
    assert len(result.years) > 1
    for row in result.years[1:]:
        assert row.has_actuals is False
        assert all(line.done == 0.0 for line in row.stack_lines)


def test_hand_built_rows_without_lines_keep_the_slice_one_total() -> None:
    from tests.test_income_control_view import _year

    row = _year(2026)  # no stack_lines: legacy fields only
    assert row.total_income == row.fixed_income + 20_000.0
