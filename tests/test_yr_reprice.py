"""engine.yr_reprice (#123 slice 3): the audit-0823 AF-2 re-pricing, extracted.
Synthetic data only."""

from __future__ import annotations

import pytest

from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan, YearResult
from engine.tax import senior_bonus_deduction, taxable_ss
from engine.yr_reprice import Repriced, reprice
from tests.test_audit_0823_af1_af2_conversion_sizing import _post_rmd_household

_SS_UPPER_TIER_RATE = 0.85  # IRC §86 upper tier


def _old_closure(yr: YearResult, draw: float, cpi: float) -> float:
    """Verbatim copy of the pre-extraction `_taxable_income_at` closure in
    scenario_autofill.add_bracket_fill_withdrawals: the no-behaviour-change oracle."""
    tss_at = taxable_ss(
        yr.combined_ss,
        yr.ss_provisional_base + draw,
        filing_status=yr.filing_status,
    )
    gross_at = yr.combined_gross - yr.taxable_ss_amt + tss_at + draw
    magi_at = yr.magi_phaseout_basis - yr.taxable_ss_amt + tss_at + draw
    ded_at = yr.deductions_before_senior_bonus + senior_bonus_deduction(
        yr.senior_bonus_ya_eff,
        yr.senior_bonus_sa_eff,
        magi_at,
        year=yr.year,
        cpi=cpi,
        filing_status=yr.filing_status,
    )
    return gross_at - ded_at


def _band_year() -> tuple[YearResult, float]:
    """Ages 76, Social Security in the IRC 86 partial band, senior bonus eligible."""
    hh = _post_rmd_household()
    return run_scenario(hh, ConversionPlan(), "x", end_age=78).years[0], hh.cpi_assumption


def _hand_year(**kw: float | int | str) -> YearResult:
    base: dict[str, float | int | str] = {
        "year": 2030,
        "your_age": 70,
        "spouse_age": 70,
        "phase": "clean",
        "filing_status": "MFJ",
    }
    base.update(kw)
    return YearResult(**base)  # type: ignore[arg-type]


def test_reprice_band_year_fixture_is_in_the_partial_band_and_bonus_eligible() -> None:
    yr, _cpi = _band_year()
    assert 0.0 < yr.taxable_ss_amt < _SS_UPPER_TIER_RATE * yr.combined_ss
    assert yr.senior_bonus_ya_eff >= 65
    assert yr.senior_bonus_sa_eff >= 65


@pytest.mark.parametrize("draw", [0.0, 5_000.0, 20_000.0, 60_000.0, 150_000.0])
def test_reprice_with_no_other_agi_equals_the_old_fill_closure(draw: float) -> None:
    yr, cpi = _band_year()
    assert reprice(yr, draw, 0.0, cpi).taxable_income == max(_old_closure(yr, draw, cpi), 0.0)


def test_reprice_at_zero_reproduces_the_years_own_figures() -> None:
    hh = _post_rmd_household()
    for yr in run_scenario(hh, ConversionPlan(), "x", end_age=80).years:
        rp = reprice(yr, 0.0, 0.0, hh.cpi_assumption)
        assert rp.taxable_ss == pytest.approx(yr.taxable_ss_amt)
        assert rp.total_deductions == pytest.approx(yr.total_deductions)
        assert rp.taxable_income == pytest.approx(yr.taxable_income)
        assert rp.magi_phaseout_basis == pytest.approx(yr.magi_phaseout_basis)


def test_reprice_other_agi_moves_provisional_income_and_magi_but_not_gross() -> None:
    # SS 40,000; other income 20,000 -> provisional 40,000, taxable SS 4,000.
    yr = _hand_year(
        combined_ss=40_000.0,
        taxable_ss_amt=4_000.0,
        ss_provisional_base=20_000.0,
        combined_gross=24_000.0,
        total_deductions=10_000.0,
        deductions_before_senior_bonus=10_000.0,
        magi_phaseout_basis=24_000.0,
    )
    ordinary = reprice(yr, 4_000.0, 0.0, 0.0)
    other = reprice(yr, 0.0, 4_000.0, 0.0)
    assert ordinary.taxable_ss == other.taxable_ss == 6_000.0  # provisional 44,000
    assert ordinary.magi_phaseout_basis == other.magi_phaseout_basis == 30_000.0
    assert ordinary.taxable_income == 20_000.0  # 24,000 - 4,000 + 6,000 + 4,000 - 10,000
    assert other.taxable_income == 16_000.0  # preferential: not in gross


def test_reprice_prices_the_senior_bonus_phase_out_at_the_new_magi() -> None:
    # MFJ 2027, both 66: MAGI 148,000 + 12,000 = 160,000, each bonus loses 0.06 x 10,000.
    yr = _hand_year(
        year=2027,
        your_age=66,
        spouse_age=66,
        senior_bonus_ya_eff=66,
        senior_bonus_sa_eff=66,
        combined_gross=148_000.0,
        deductions_before_senior_bonus=60_000.0,
        magi_phaseout_basis=148_000.0,
    )
    rp = reprice(yr, 0.0, 12_000.0, 0.0)
    assert isinstance(rp, Repriced)
    assert rp.taxable_ss == 0.0
    assert rp.magi_phaseout_basis == 160_000.0
    assert rp.total_deductions == pytest.approx(60_000.0 + 2 * (6_000.0 - 600.0))
    assert rp.taxable_income == pytest.approx(148_000.0 - 70_800.0)


def test_reprice_floors_taxable_income_at_zero() -> None:
    yr = _hand_year(combined_gross=10_000.0, deductions_before_senior_bonus=30_000.0)
    assert reprice(yr, 0.0, 0.0, 0.0).taxable_income == 0.0
