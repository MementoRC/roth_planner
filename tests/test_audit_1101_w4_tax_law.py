"""audit-1101 W4 F-03: excess-APTC repayment below 100% FPL.

Treas. Reg. §1.36B-2(b)(6): a taxpayer whose household income ends up below
100% of the poverty line is still an applicable taxpayer when advance credit
payments were made for the year (the Exchange estimated 100%-400% at
enrollment). The allowed PTC is computed on the actual income, so the
reconciliation only claws back the excess over that credit -- not the whole
advance. P.L. 119-21 repealed §36B(c)(1)(B) and the §36B(f)(2)(B) cap but left
this rule in place. Synthetic figures only.
"""

from __future__ import annotations

import pytest

from engine.aca import (
    _fpl,
    aca_allowed_ptc,
    aca_excess_aptc_repayment,
    aca_premium_cap_rate,
    aca_subsidy,
)
from engine.scenario_compute import compute_aca

BENCHMARK = 21_600.0
YEAR = 2026


def _lowest_band_rate(enhanced: bool) -> float:
    return aca_premium_cap_rate(_fpl("MFJ", year=YEAR), enhanced, "MFJ", year=YEAR)


@pytest.mark.parametrize("enhanced", [False, True])
def test_repayment_below_fpl_with_aptc_reconciles_against_allowed_credit(enhanced):
    magi = 0.8 * _fpl("MFJ", year=YEAR)
    advance = 18_000.0
    allowed = BENCHMARK - magi * _lowest_band_rate(enhanced)
    result = aca_excess_aptc_repayment(
        advance_aptc_annual=advance,
        actual_magi=magi,
        benchmark_premium_annual=BENCHMARK,
        enhanced_subsidies_active=enhanced,
        year=YEAR,
    )
    # Before the fix the whole $18,000 advance was clawed back.
    assert result == pytest.approx(advance - allowed, abs=0.01)
    assert result < advance


def test_allowed_ptc_below_fpl_without_aptc_is_zero():
    magi = 0.8 * _fpl("MFJ", year=YEAR)
    assert aca_allowed_ptc(magi, BENCHMARK, False, year=YEAR, advance_aptc_paid=False) == 0.0
    assert aca_subsidy(magi, BENCHMARK, False, year=YEAR) == 0.0


def test_allowed_ptc_at_or_above_fpl_matches_aca_subsidy():
    for ratio in (1.0, 1.5, 3.0):
        magi = ratio * _fpl("MFJ", year=YEAR)
        assert aca_allowed_ptc(
            magi, BENCHMARK, False, year=YEAR, advance_aptc_paid=True
        ) == pytest.approx(aca_subsidy(magi, BENCHMARK, False, year=YEAR))


def test_compute_aca_below_fpl_clawback_and_premium_use_the_same_allowed_credit():
    """In-year premium is benchmark - advance; the reconciliation (advance -
    allowed PTC) lands in aca_clawback. Total outflow = benchmark - allowed PTC."""
    magi = 0.8 * _fpl("MFJ", year=YEAR)
    advance = 8_000.0
    aca_magi, _loss, clawback, premium = compute_aca(
        magi=magi,
        combined_ss=0.0,
        taxable_ss_amt=0.0,
        your_conversion=0.0,
        spouse_conversion=0.0,
        ya=60,
        sa=60,
        your_aca_enrolled=True,
        spouse_aca_enrolled=True,
        aca_benchmark_premium_annual=BENCHMARK,
        aca_enhanced_subsidies_active=False,
        advance_aptc_annual=advance,
        current_filing_status="MFJ",
        year=YEAR,
        cpi=0.025,
    )
    effective_benchmark = premium + advance  # premium == benchmark - advance
    allowed = aca_allowed_ptc(
        aca_magi, effective_benchmark, False, year=YEAR, advance_aptc_paid=True
    )
    assert allowed > 0
    # Before the fix the whole advance was clawed back (clawback == advance).
    assert clawback == pytest.approx(advance - allowed, abs=0.01)
    assert premium + clawback == pytest.approx(effective_benchmark - allowed, abs=0.01)
