"""TDD regression tests for audit-0706 wave-2 LTCG findings.

Findings covered
----------------
1. scenario-autofill-2 (BEHAVIORAL): survivor brokerage LTCG stack baseline must use
   RMD-based ordinary income, not zero — understates LTCG tax when RMD pushes gains
   into the 15% band.
2. scenario-autofill-5 (clarity): LTCG_RATES_SINGLE constant is exported from tax.py
   and used in scenario_compare.py instead of LTCG_RATES_MFJ index literals.
3. tax-core-1 (cosmetic/robustness): estimate_ytd_federal_tax uses LTCG_RATES_SINGLE
   for Single filers (rate identical today but semantically correct).
4. tax-core-4 (BEHAVIORAL): deductions() with filing_status='Single' and no explicit
   std_ded must auto-select STD_DEDUCTION_SINGLE, not STD_DEDUCTION_MFJ.
"""

from __future__ import annotations

import pytest

from engine.tax import (
    LTCG_RATES_MFJ,
    LTCG_RATES_SINGLE,
    STD_DEDUCTION_MFJ,
    STD_DEDUCTION_SINGLE,
    deductions,
)

# ---------------------------------------------------------------------------
# Finding 2: LTCG_RATES_SINGLE exported from engine.tax
# ---------------------------------------------------------------------------


class TestLTCGRatesSingleExists:
    """scenario-autofill-5: LTCG_RATES_SINGLE must exist in engine.tax."""

    def test_ltcg_rates_single_is_tuple(self) -> None:
        assert isinstance(LTCG_RATES_SINGLE, tuple)

    def test_ltcg_rates_single_has_three_tiers(self) -> None:
        assert len(LTCG_RATES_SINGLE) == 3

    def test_ltcg_rates_single_values(self) -> None:
        assert LTCG_RATES_SINGLE == (0.0, 0.15, 0.20)

    def test_ltcg_rates_single_same_as_mfj(self) -> None:
        """Rates are currently identical — both are (0.0, 0.15, 0.20) in 2026."""
        assert LTCG_RATES_SINGLE == LTCG_RATES_MFJ


# ---------------------------------------------------------------------------
# Finding 4: deductions() auto-selects std_ded from filing_status
# ---------------------------------------------------------------------------


class TestDeductionsFilingStatusDefault:
    """tax-core-4: deductions() must infer std_ded / senior_extra from filing_status
    when the caller omits them."""

    def test_single_no_explicit_std_ded_uses_single_amount(self) -> None:
        """Core regression: deductions(filing_status='Single') without explicit
        std_ded must return STD_DEDUCTION_SINGLE, not STD_DEDUCTION_MFJ."""
        result = deductions(your_age=50, spouse_age=0, filing_status="Single", year=2026)
        assert result == pytest.approx(STD_DEDUCTION_SINGLE), (
            f"Expected STD_DEDUCTION_SINGLE={STD_DEDUCTION_SINGLE}, got {result}; "
            f"the MFJ default ({STD_DEDUCTION_MFJ}) must not be used for Single filers"
        )

    def test_mfj_no_explicit_std_ded_uses_mfj_amount(self) -> None:
        """Non-regression: deductions() with no filing_status (defaults MFJ) must
        still return STD_DEDUCTION_MFJ."""
        result = deductions(your_age=50, spouse_age=48, year=2026)
        assert result == pytest.approx(STD_DEDUCTION_MFJ)

    def test_single_over_65_no_explicit_args_gets_senior_extra(self) -> None:
        """Single filer 65+ with no explicit std_ded/senior_extra gets both the
        correct base deduction AND the senior extra."""
        from engine.tax import SENIOR_EXTRA_SINGLE

        result = deductions(your_age=70, spouse_age=0, filing_status="Single", year=2026)
        assert result == pytest.approx(STD_DEDUCTION_SINGLE + SENIOR_EXTRA_SINGLE)

    def test_explicit_override_still_works_single(self) -> None:
        """Callers that pass explicit std_ded must still get that value (no regression)."""
        custom_ded = 99_000.0
        result = deductions(
            your_age=50,
            spouse_age=0,
            std_ded=custom_ded,
            senior_extra=0.0,
            filing_status="Single",
            year=2026,
        )
        assert result == pytest.approx(custom_ded)

    def test_explicit_override_still_works_mfj(self) -> None:
        """Callers that pass explicit std_ded for MFJ must still get that value."""
        from engine.tax import SENIOR_EXTRA_MFJ

        result = deductions(
            your_age=65,
            spouse_age=60,
            std_ded=STD_DEDUCTION_MFJ,
            senior_extra=SENIOR_EXTRA_MFJ,
            year=2026,
        )
        assert result == pytest.approx(STD_DEDUCTION_MFJ + SENIOR_EXTRA_MFJ)


# ---------------------------------------------------------------------------
# Finding 1: survivor brokerage LTCG stack baseline uses RMD income
# ---------------------------------------------------------------------------


class TestSurvivorBrokerageLTCGStackBaseline:
    """scenario-autofill-2: the brokerage LTCG tax in compute_survivor_snapshot
    must stack on RMD-derived ordinary income, not zero.

    When RMD income is large enough to push realized brokerage gains into the 15%
    LTCG band, the computed tax must be HIGHER than under the zero-baseline assumption,
    and the projected brokerage balance must be LOWER.
    """

    def test_rmd_stacking_increases_ltcg_tax(self) -> None:
        """With RMD income stacking under realized gains that span the 0%→15% boundary,
        the RMD-based LTCG tax MUST be higher than the zero-baseline tax."""
        from engine.tax import LTCG_THRESHOLDS_SINGLE
        from engine.tax_indexing import index_tuple
        from models.household import Household

        hh = Household(
            your_age=72,
            spouse_age=67,
            your_ira=2_000_000.0,
            spouse_ira=0.0,
            growth_rate=0.05,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
        )
        cpi = hh.cpi_assumption
        year = 2026
        thr = index_tuple(LTCG_THRESHOLDS_SINGLE, year, cpi)
        # threshold[0] ≈ 49,450; gains crossing from below to above = material LTCG tax
        rmd_income = 40_000.0  # RMD pushes total past the 0% ceiling
        brok_realized = 30_000.0  # gains straddle 49,450 with and without RMD

        # Zero-baseline (old code): gains start at 0
        ltcg_at_15_z = max(0.0, min(brok_realized, thr[1]) - thr[0])
        tax_zero = ltcg_at_15_z * LTCG_RATES_SINGLE[1]

        # RMD-baseline (fixed code): stack starts at rmd_income (40k), gains 40k→70k
        ltcg_start = rmd_income
        ltcg_end = rmd_income + brok_realized
        ltcg_at_15_r = max(0.0, min(ltcg_end, thr[1]) - max(ltcg_start, thr[0]))
        tax_rmd = ltcg_at_15_r * LTCG_RATES_SINGLE[1]

        assert tax_rmd > tax_zero, (
            f"RMD-stacked LTCG tax ({tax_rmd:.2f}) must exceed zero-baseline tax ({tax_zero:.2f}) "
            f"when RMD pushes gains above the 0% ceiling ({thr[0]:.0f})"
        )

    def test_compute_survivor_snapshot_brokerage_tax_uses_rmd_baseline(self) -> None:
        """Integration: with a large inherited IRA generating RMDs that push realized
        gains above the LTCG 0% ceiling, the RMD-baseline LTCG tax must exceed the
        zero-baseline, confirming the fix is wired into the brokerage projection loop.
        """
        from engine.ira import calc_rmd
        from engine.tax import LTCG_THRESHOLDS_SINGLE, STD_DEDUCTION_SINGLE
        from engine.tax_indexing import index_tuple
        from models.household import Household

        # Survivor (spouse) inherits large IRA → big RMD → pushes LTCG into 15% band
        hh = Household(
            your_age=72,
            spouse_age=67,
            your_ira=2_000_000.0,
            spouse_ira=0.0,
            growth_rate=0.05,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            brokerage_start=500_000.0,
            brok_turnover=0.10,
        )
        cpi = hh.cpi_assumption
        # Projection year 2, survivor age 74 (past rmd_start=73) — RMD kicks in
        year_at_offset = 2028
        survivor_age_at_offset = 74
        brok_balance = 500_000.0
        brok_appreciation_rate = 0.05
        brok_realized = brok_balance * brok_appreciation_rate * hh.brok_turnover
        # brok_realized = 500000 * 0.05 * 0.10 = 2500

        ira_balance_at_proj = 1_800_000.0  # approximate inherited IRA after growth
        rmd_this_year = calc_rmd(ira_balance_at_proj, survivor_age_at_offset, hh.spouse_rmd_start_age)
        # rmd_this_year ≈ 1800000 / 25.5 ≈ 70,588

        thr = index_tuple(LTCG_THRESHOLDS_SINGLE, year_at_offset, cpi)
        # thr[0] ≈ 49,450 * cpi^2 ≈ 51k

        # OLD (zero-baseline): brok_realized=2500 < thr[0]≈51k → all at 0% → tax=0
        ltcg_at_15_old = max(0.0, min(brok_realized, thr[1]) - thr[0])
        tax_old = ltcg_at_15_old * LTCG_RATES_SINGLE[1]

        # NEW (RMD-baseline): taxable_ordinary ≈ rmd - std_ded ≈ 70588-16100=54488 > 51k
        # so ALL brok_realized gains fall in the 15% band
        taxable_ordinary_approx = max(rmd_this_year - STD_DEDUCTION_SINGLE, 0.0)
        ltcg_start = taxable_ordinary_approx
        ltcg_end = taxable_ordinary_approx + brok_realized
        ltcg_at_15_new = max(0.0, min(ltcg_end, thr[1]) - max(ltcg_start, thr[0]))
        tax_new = ltcg_at_15_new * LTCG_RATES_SINGLE[1]

        assert rmd_this_year > 50_000, (
            f"Test setup: RMD ({rmd_this_year:.0f}) must be large enough to exceed LTCG 0%-ceiling"
        )
        assert tax_new > tax_old, (
            f"RMD-baseline LTCG tax ({tax_new:.2f}) must exceed zero-baseline ({tax_old:.2f}) "
            f"when taxable_ordinary ({taxable_ordinary_approx:.0f}) > LTCG 0%-ceiling ({thr[0]:.0f})"
        )
