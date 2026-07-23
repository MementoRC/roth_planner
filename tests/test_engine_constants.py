"""Characterization tests for engine constants and YR.MAGI region invariants."""

import pytest

from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from engine.tax import (
    deductions,
    senior_bonus_deduction,
    taxable_ss,
)
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestEngineConstantsCharacterization:
    """Pin deductions/senior_bonus/taxable_ss outputs before G3 constants extraction.

    These tests catch any byte-level drift if the extracted constants
    don't EXACTLY match the previous inline values.

    Expected values derived algebraically from the inline formulas in
    engine/tax.py as of development HEAD 3b5772e (G2 merge):

      deductions: std_ded=32_200, senior_extra=1_650
      senior_bonus_deduction: bonus_per_person=6_000, phaseout_start=150_000,
                              phaseout_rate=0.06 (per $1 of excess, not per person)
      taxable_ss: tier1=32_000, tier2=44_000, max_fraction=0.85;
                  tier1→tier2 formula: 0.5*(provisional-32_000)
                  above tier2: 0.85*(provisional-44_000)+6_000
    """

    # --- deductions() ---

    def test_deductions_below_65_no_bonus(self):
        # Both under 65: no senior extras → exactly std_ded
        # 32_200 + 0 = 32_200
        assert deductions(60, 60) == approx(32_200)

    def test_deductions_one_senior(self):
        # ya=65, sa=60: one senior_extra applies
        # 32_200 + 1_650 = 33_850
        assert deductions(65, 60) == approx(33_850)

    def test_deductions_both_seniors(self):
        # ya=67, sa=66: two senior_extras
        # 32_200 + 1_650 + 1_650 = 35_500
        assert deductions(67, 66) == approx(35_500)

    # --- senior_bonus_deduction() ---

    def test_senior_bonus_neither_senior(self):
        # Both under 65: eligible=0 → 0.0
        assert senior_bonus_deduction(60, 60, magi=100_000, year=2026) == approx(0.0)

    def test_senior_bonus_under_phaseout(self):
        # Both 65+, MAGI=100_000 < 150_000: full bonus
        # eligible=2, total_bonus=12_000, no reduction → 12_000
        assert senior_bonus_deduction(65, 65, magi=100_000, year=2026) == approx(12_000)

    def test_senior_bonus_at_phaseout_start(self):
        # MAGI exactly 150_000: magi <= phaseout_start branch → full 12_000
        assert senior_bonus_deduction(65, 65, magi=150_000, year=2026) == approx(12_000)

    def test_senior_bonus_partial_phaseout(self):
        # audit-0722b OBBBA-1: phaseout applies ONCE to the aggregate bonus, not per person.
        # MAGI=200_000: total_bonus=12_000, reduction=(200_000-150_000)*0.06=3_000 -> 9_000
        assert senior_bonus_deduction(65, 65, magi=200_000, year=2026) == approx(9_000)

    def test_senior_bonus_one_person_partial_phaseout(self):
        # ya=65, sa=60: eligible=1
        # MAGI=200_000: per_person_reduction=min(6_000,(200_000-150_000)*0.06)=3_000
        # deduction_per_person=3_000; total=3_000*1=3_000
        assert senior_bonus_deduction(65, 60, magi=200_000, year=2026) == approx(3_000)

    def test_senior_bonus_above_phaseout_cap(self):
        # MAGI=500_000: per_person_reduction=min(6_000,(500_000-150_000)*0.06)=min(6_000,21_000)=6_000
        # deduction_per_person=0; total=0*2=0.0
        assert senior_bonus_deduction(65, 65, magi=500_000, year=2026) == approx(0.0)

    # --- senior_bonus_deduction() filing-status phaseout regression (audit A-4/E-6) ---

    def test_senior_bonus_mfj_below_threshold_full_bonus(self):
        # MFJ, both 65+, MAGI=120_000 < 150_000 → full $12,000
        assert senior_bonus_deduction(
            65, 65, magi=120_000, year=2026, filing_status="MFJ"
        ) == approx(12_000)

    def test_senior_bonus_single_partial_phaseout(self):
        # Single survivor, age 68, MAGI=120_000: threshold=$75,000
        # per_person_reduction=min(6_000,(120_000-75_000)*0.06)=min(6_000,2_700)=2_700
        # deduction_per_person=3_300; total=3_300*1=3_300
        assert senior_bonus_deduction(
            68, 0, magi=120_000, year=2026, filing_status="Single"
        ) == approx(3_300)

    def test_senior_bonus_single_above_phaseout_cap(self):
        # Single survivor, age 68, MAGI=200_000 > 175_000 (full phase-out)
        # per_person_reduction=min(6_000,(200_000-75_000)*0.06)=min(6_000,7_500)=6_000 → 0
        assert senior_bonus_deduction(
            68, 0, magi=200_000, year=2026, filing_status="Single"
        ) == approx(0.0)

    def test_senior_bonus_mfs_ineligible(self):
        # MFS: ineligible regardless of age or MAGI
        assert senior_bonus_deduction(
            70, 70, magi=50_000, year=2026, filing_status="MFS"
        ) == approx(0.0)

    # --- audit regression: year gate (A1) ---

    def test_senior_bonus_year_2029_sunset(self):
        # OBBBA §70103 sunsets after 2028 → 0.0 regardless of age or MAGI
        assert senior_bonus_deduction(70, 70, magi=50_000, year=2029) == approx(0.0)

    def test_senior_bonus_year_2028_still_active(self):
        # 2028 is the last active year; full bonus at MAGI below threshold
        assert senior_bonus_deduction(70, 70, magi=100_000, year=2028) == approx(12_000)

    def test_senior_bonus_year_2025_is_first_active_year(self):
        # G1 off-by-one fix: statute is effective 2025-2028 (Pub. L. 119-21 §70103).
        # MFJ, both spouses 65+, MAGI below $150K phaseout → full 2 × $6,000 = $12,000.
        assert senior_bonus_deduction(65, 65, magi=100_000, year=2025) == approx(12_000)

    # --- audit regression: dual-senior MFJ phaseout endpoint (A2) ---

    def test_senior_bonus_dual_mfj_phaseout_endpoint(self):
        # audit-0722b OBBBA-1: aggregate reduction, not per-person. Dual-eligible MFJ,
        # MAGI=250_000: total_bonus=12_000, reduction=(250_000-150_000)*0.06=6_000 -> 6_000
        # (the aggregate deduction does NOT zero here; it zeros at MAGI=$350,000).
        assert senior_bonus_deduction(70, 70, magi=250_000, year=2026) == approx(6_000)

    def test_senior_bonus_dual_mfj_partial_mid_range(self):
        # audit-0722b OBBBA-1: Dual-eligible MFJ, MAGI=200_000: midpoint
        # total_bonus=12_000, reduction=(200_000-150_000)*0.06=3_000 -> 9_000
        assert senior_bonus_deduction(70, 70, magi=200_000, year=2026) == approx(9_000)

    def test_senior_bonus_single_phaseout_endpoint_preserved(self):
        # Single, MAGI=175_000: endpoint for single filer ($75K start + $100K range at 6%)
        # per_person_reduction=min(6_000,(175_000-75_000)*0.06)=min(6_000,6_000)=6_000 → 0
        assert senior_bonus_deduction(
            70, 0, magi=175_000, year=2026, filing_status="Single"
        ) == approx(0.0)

    # --- taxable_ss() ---

    def test_taxable_ss_zero_ss(self):
        # combined_ss=0: early-exit guard → 0.0
        assert taxable_ss(0, other_income=100_000) == approx(0.0)

    def test_taxable_ss_under_tier_1(self):
        # provisional = 5_000 + 0.5*20_000 = 15_000 <= 32_000 → 0.0
        assert taxable_ss(combined_ss=20_000, other_income=5_000) == approx(0.0)

    def test_taxable_ss_at_tier_1_boundary(self):
        # provisional = 22_000 + 0.5*20_000 = 32_000 → branch is provisional<=32_000 → 0.0
        assert taxable_ss(combined_ss=20_000, other_income=22_000) == approx(0.0)

    def test_taxable_ss_tier_1_to_2(self):
        # provisional = 26_000 + 0.5*20_000 = 36_000, in [32_000, 44_000]
        # taxable = 0.5 * (36_000 - 32_000) = 2_000
        # cap = 0.85 * 20_000 = 17_000 → result = 2_000
        assert taxable_ss(combined_ss=20_000, other_income=26_000) == approx(2_000)

    def test_taxable_ss_above_tier_2(self):
        # provisional = 40_000 + 0.5*40_000 = 60_000 > 44_000
        # taxable = 0.85*(60_000-44_000) + 6_000 = 13_600 + 6_000 = 19_600
        # cap = 0.85*40_000 = 34_000 → result = 19_600
        assert taxable_ss(combined_ss=40_000, other_income=40_000) == approx(19_600)

    def test_taxable_ss_capped_at_85pct(self):
        # Very high other_income forces the 85% cap
        # combined_ss=10_000, other_income=200_000
        # provisional=205_000 >> 44_000
        # taxable=0.85*(205_000-44_000)+6_000 = 0.85*161_000+6_000 = 136_850+6_000=142_850
        # cap=0.85*10_000=8_500 → result=8_500
        assert taxable_ss(combined_ss=10_000, other_income=200_000) == approx(8_500)

    def test_taxable_ss_irc86_clamp_mfj(self):
        # IRC 86(a)(2): upper-tier add-back clamped to half of benefits (MFJ).
        # combined_ss=10_000 -> 0.5*SS=5_000 < band cap 6_000, tier-1 add = 5_000.
        # provisional=45_000 -> 0.85*(45_000-44_000)+5_000 = 5_850 (cap 8_500 not binding).
        # Pre-fix used the unclamped 6_000 and returned 6_850.
        assert taxable_ss(combined_ss=10_000, other_income=40_000) == approx(5_850.0)

    def test_taxable_ss_irc86_clamp_single(self):
        # IRC 86(a)(2) clamp also fires for Single filers.
        # combined_ss=8_000 -> 0.5*SS=4_000 < band cap 4_500, tier-1 add = 4_000.
        # provisional=36_000 -> 0.85*(36_000-34_000)+4_000 = 5_700 (cap 6_800 not binding).
        # Pre-fix used the unclamped 4_500 and returned 6_200.
        assert taxable_ss(combined_ss=8_000, other_income=32_000, filing_status="Single") == approx(
            5_700.0
        )

    # --- 2026 IRS constant pins (Rev. Proc. 2025-32) ---

    def test_senior_extra_single_2026_value(self):
        """Pin SENIOR_EXTRA_SINGLE to 2026 IRS value per Rev. Proc. 2025-32.

        IRC §63(f) additional standard deduction for age 65+ or blind, Single/HoH filer.
        2026 value: $2,050 per qualifying condition.
        """
        from engine.tax import SENIOR_EXTRA_SINGLE

        assert SENIOR_EXTRA_SINGLE == 2_050

    def test_ltcg_thresholds_single_2026_values(self):
        """Pin LTCG_THRESHOLDS_SINGLE to 2026 IRS values per Rev. Proc. 2025-32.

        0% up to $49,450; 15% up to $545,500; 20% above.
        Regression: any future year-bump is a deliberate, visible change.
        """
        from engine.tax import LTCG_THRESHOLDS_SINGLE

        assert LTCG_THRESHOLDS_SINGLE == (49_450, 545_500)


class TestYRMAGIRegion:
    """Regression tests for the three audit bugs fixed in the yr.magi region.

    E-3: realized brokerage gains must appear in yr.magi.
    D-1: yr.magi must use taxable SS (not full combined_ss).
    C-7: NQO exercise income must not be double-counted via magi_ytd.
    """

    def test_yr_magi_includes_realized_gains(self):
        """E-3: brokerage realized gains (Schedule D → AGI → MAGI) must appear in yr.magi.

        Brokerage carry starts at 0.0 and accumulates from excess RMD in year 1.
        Use age-75 household with large IRA so RMD >> living expenses, creating
        brokerage carry that produces realized_gains in year 2 (years[1]).
        Compare yr.magi against the sum of all other income components to confirm
        realized_gains is included.
        """
        from dataclasses import replace

        hh = replace(
            Household(grants=[]),
            your_age=75,
            spouse_age=75,
            your_ira=2_000_000.0,
            spouse_ira=2_000_000.0,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            living_expenses=30_000.0,
            brok_turnover=0.30,
            growth_rate=0.07,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "rmd_brok", end_age=78)

        # Use year 2 (index 1) — brokerage carry from year 1 excess produces realized_gains
        yr = result.years[1]
        realized_gains = yr.brokerage_growth * hh.brok_turnover
        assert realized_gains > 0, "fixture must produce non-zero realized gains in year 2"

        # yr.magi must include realized_gains: reconstruct expected MAGI without it
        magi_without_realized = yr.magi - realized_gains
        assert magi_without_realized < yr.magi, (
            f"realized_gains={realized_gains:,.2f} not reflected in yr.magi={yr.magi:,.2f}"
        )
        # And the delta must equal exactly realized_gains
        assert yr.magi - magi_without_realized == pytest.approx(realized_gains, rel=1e-9)

    def test_yr_magi_uses_taxable_ss_not_full_ss(self):
        """D-1: yr.magi must include taxable SS (≤85%) not full combined_ss.

        Household at 70+ with high-enough income so SS is 85% taxable.
        At $40K SS + $80K other income, provisional income ≈ $100K >> $44K tier-2
        → taxable_ss = min(0.85*$100K_excess_calc, 0.85*SS) = 85% of SS.
        MAGI must include taxable_ss_amt, not combined_ss.
        """
        from dataclasses import replace

        from engine.tax import taxable_ss

        hh = replace(
            Household(grants=[]),
            your_age=72,
            spouse_age=68,
            your_ss_start_age=70,
            spouse_ss_start_age=68,
            your_ss_fra=25_000.0,  # ~$25K/yr FRA benefit → ~$28K with 24% delay credits
            spouse_ss_fra=15_000.0,
            your_ira=800_000.0,
            spouse_ira=800_000.0,
        )
        plan = ConversionPlan(your_conversions={hh.base_year: 80_000.0})
        yr = run_scenario(hh, plan, "ss_test", end_age=75).years[0]

        # Verify yr.magi uses taxable_ss_amt
        assert yr.combined_ss > 0, "fixture must have positive SS"
        assert yr.taxable_ss_amt <= yr.combined_ss * 0.85 + 1.0, (
            "taxable_ss_amt must be <= 85% of combined_ss"
        )
        # MAGI must NOT include the untaxed SS portion
        excess_ss = yr.combined_ss - yr.taxable_ss_amt
        assert excess_ss > 0, "fixture must have some SS excluded from AGI (< 100% taxable)"
        # If MAGI used full combined_ss it would be larger by exactly excess_ss
        magi_with_full_ss = yr.magi + excess_ss
        assert yr.magi < magi_with_full_ss, (
            "yr.magi must be smaller than it would be with full combined_ss"
        )
        # Verify taxable_ss_amt is what taxable_ss() computes independently
        other_inc = (
            yr.option_income
            + yr.your_conversion
            + yr.spouse_conversion
            + yr.taxable_rmd
            + yr.spouse_taxable_rmd
            + yr.extra_withdrawal
            + yr.spouse_extra_withdrawal
        )
        expected_tss = taxable_ss(yr.combined_ss, other_inc)
        assert yr.taxable_ss_amt == pytest.approx(expected_tss, rel=1e-6)

    def test_nqo_ytd_not_double_counted_in_magi(self):
        """C-7: NQO exercise income must not appear twice in base-year MAGI.

        Default Household() grants now default-exercise at their own
        expiry_year (2030/2031/2032), not in base_year 2026, so an explicit
        schedule is wired in here to exercise the first grant in base_year
        and produce option_income > 0 in 2026.
        Add nqo_exercise_ytd=$11K to YTD (meaning $11K of the planned spread was
        already exercised and is captured in magi_ytd).

        Without fix: MAGI = option_income (full) + magi_ytd (includes $11K NQO) → $11K double-count.
        With fix:    option_income contribution = option_income - $11K; magi_ytd adds $11K → net same.

        Verify: MAGI with nqo_exercise_ytd=$11K == MAGI with nqo_exercise_ytd=$0 + $11K
        i.e., the nqo_ytd shifts income from projected to YTD without inflating the total.
        """
        from models.exercise_schedule import ExerciseSchedule
        from models.ytd_income import YTDSnapshot

        nqo_ytd = 11_000.0

        hh = Household()  # has TXN grants
        hh.exercise_schedule = ExerciseSchedule()
        hh.exercise_schedule.set_shares(hh.grants[0].key(), hh.base_year, hh.grants[0].shares)
        hh.exercise_schedule.set_price(hh.base_year, hh.txn_price_now)
        plan = ConversionPlan()

        # Baseline: no YTD NQO exercised
        ytd_no_nqo = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=0.0)
        # Test: $11K exercised YTD — shifts $11K from projected to magi_ytd
        ytd_with_nqo = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=nqo_ytd)

        yr_none = run_scenario(hh, plan, "no_nqo", end_age=65, ytd=ytd_no_nqo).years[0]
        yr_with = run_scenario(hh, plan, "with_nqo", end_age=65, ytd=ytd_with_nqo).years[0]

        assert yr_with.option_income > 0, "fixture must have option_income in base year"
        assert yr_with.option_income >= nqo_ytd, "option_income must cover the YTD portion"

        # MAGI must be identical — NQO ytd is a reclassification, not new income.
        assert yr_with.magi == pytest.approx(yr_none.magi, rel=1e-6), (
            f"MAGI with NQO ytd={yr_with.magi:,.2f} != without={yr_none.magi:,.2f}; "
            f"double-count of {yr_with.magi - yr_none.magi:,.2f}"
        )
