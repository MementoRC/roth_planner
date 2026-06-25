"""Cross-cutting tests for single-filer foundation paths."""

import pytest

from engine.aca import aca_subsidy
from engine.aca_irmaa_compute import compute_year_by_year_timeline
from engine.headroom import compute_headroom
from engine.irmaa import irmaa_surcharge
from engine.niit import niit
from engine.tax import senior_bonus_deduction, taxable_ss
from models.household import Household
from models.ytd_income import YTDSnapshot


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestSingleFilerFoundations:
    """PR6a: verify single-filer constants and filing_status parameterization."""

    # --- taxable_ss ---

    def test_taxable_ss_default_mfj_unchanged(self):
        """Explicit filing_status='MFJ' produces identical result to omitting it."""

        assert taxable_ss(40_000, 20_000) == taxable_ss(40_000, 20_000, filing_status="MFJ")
        assert taxable_ss(100_000, 200_000) == taxable_ss(100_000, 200_000, filing_status="MFJ")

    def test_taxable_ss_single_uses_single_thresholds(self):
        """Single filer with provisional income between $25K and $34K hits the 50% tier.

        MFJ tier 1 starts at $32K — same provisional income ($27.5K) is below MFJ
        tier 1 (returns 0) but above Single tier 1 ($25K), so Single returns > 0.
        provisional = 2_500 + 0.5 * 50_000 = 27_500
        """
        from engine.tax import SS_TIER_1_SINGLE

        combined_ss = 50_000
        other = 2_500
        # provisional = 27_500 — above Single tier 1 ($25K), below MFJ tier 1 ($32K)
        mfj_result = taxable_ss(combined_ss, other, filing_status="MFJ")
        single_result = taxable_ss(combined_ss, other, filing_status="Single")
        assert mfj_result == 0.0
        assert single_result == approx(0.5 * (27_500 - SS_TIER_1_SINGLE))

    # --- niit ---

    def test_niit_default_mfj_unchanged(self):
        """Explicit filing_status='MFJ' produces identical result to omitting it."""

        assert niit(300_000, 50_000) == niit(300_000, 50_000, filing_status="MFJ")
        assert niit(200_000, 50_000) == niit(200_000, 50_000, filing_status="MFJ")

    def test_niit_single_uses_lower_threshold(self):
        """MAGI of $220K: below MFJ threshold ($250K) so MFJ → 0; above Single threshold
        ($200K) so Single → positive NIIT.

        excess = 220_000 - 200_000 = 20_000; NII = 30_000 → min(30K, 20K) × 3.8%
        """
        from engine.niit import NIIT_RATE

        magi = 220_000
        nii = 30_000
        mfj_result = niit(magi, nii, filing_status="MFJ")
        single_result = niit(magi, nii, filing_status="Single")
        assert mfj_result == 0.0
        assert single_result == approx(20_000 * NIIT_RATE)

    # --- irmaa_surcharge ---

    def test_irmaa_surcharge_default_mfj_unchanged(self):
        """Explicit filing_status='MFJ' produces identical result to omitting it."""

        assert irmaa_surcharge(220_000) == irmaa_surcharge(220_000, filing_status="MFJ")
        assert irmaa_surcharge(200_000) == irmaa_surcharge(200_000, filing_status="MFJ")

    def test_irmaa_surcharge_single_uses_single_tiers(self):
        """MAGI of $115K: below MFJ Tier 1 ($218K) so MFJ → 0; above Single Tier 1
        ($109K) so Single → positive surcharge (single person on Medicare).
        """

        magi = 115_000
        mfj_result = irmaa_surcharge(magi, num_people=1, filing_status="MFJ")
        single_result = irmaa_surcharge(magi, num_people=1, filing_status="Single")
        assert mfj_result == 0.0
        assert single_result > 0.0

    # --- aca_subsidy ---

    def test_aca_subsidy_default_mfj_unchanged(self):
        """Explicit filing_status='MFJ' produces identical result to omitting it."""

        assert aca_subsidy(40_000) == aca_subsidy(40_000, filing_status="MFJ")
        assert aca_subsidy(80_000) == aca_subsidy(80_000, filing_status="MFJ")

    def test_aca_subsidy_single_uses_fpl1(self):
        """Single filer: FPL_1 = $15,060 vs FPL_2 = $21,150.

        At MAGI = $40,000 (pre-ARP, enhanced_subsidies_active=False):
        - MFJ:    40_000 / 21_150 ≈ 1.89 → 150-200% FPL band → 6.4% cap
        - Single: 40_000 / 15_060 ≈ 2.66 → 250-300% FPL band → 9.6% cap
        Higher cap rate for Single → lower subsidy for Single filer.
        """

        magi = 40_000
        mfj_result = aca_subsidy(magi, filing_status="MFJ")
        single_result = aca_subsidy(magi, filing_status="Single")
        # Single filer is higher on the FPL scale → larger cap → less subsidy
        assert single_result < mfj_result

    # --- aca_premium_cap_rate ---

    def test_aca_premium_cap_rate_default_mfj_unchanged(self):
        """Explicit filing_status='MFJ' produces identical result to omitting it."""
        from engine.aca import aca_premium_cap_rate

        assert aca_premium_cap_rate(60_000) == aca_premium_cap_rate(60_000, filing_status="MFJ")

    def test_pre_arp_300_400_fpl_band_uses_9_96_pct(self):
        """Pre-ARP 300-400% FPL band rate is 9.96% per Rev. Proc. 2025-25 (IRB 2025-32).

        MFJ FPL_2 = $21,150. 300-400% band is $63,450 – $84,600.
        At MAGI = $70,000: 70_000 / 21_150 ≈ 3.31 → falls in 300-400% band.
        Updated from 9.78% (stale 2024 value) to 9.96% (2026 IRS value).
        """
        from engine.aca import ACA_PRE_ARP_SCHEDULE, FPL_2, aca_premium_cap_rate

        # Verify the schedule constant directly
        pre_arp_300_400_rate = next(rate for fpl, rate in ACA_PRE_ARP_SCHEDULE if fpl == 4.00)
        assert pre_arp_300_400_rate == pytest.approx(0.0996)

        # Verify via the public function at a MAGI squarely in the 300-400% band
        magi_in_band = 3.31 * FPL_2  # ~$70,002 — above 300% ($63,450), below 400% ($84,600)
        rate = aca_premium_cap_rate(magi_in_band, enhanced_subsidies_active=False)
        assert rate == pytest.approx(0.0996)


# ---------------------------------------------------------------------------
# D1 — senior_bonus_deduction must not count spouse for Single filers
# ---------------------------------------------------------------------------


class TestSeniorBonusSingleFilerNoSpouseCount:
    """D1: Single filer with a non-zero spouse_age must only count the filer."""

    def test_single_filer_only_counts_your_age(self):
        """Single, your_age=70, spouse_age=68 (below 65 is irrelevant here;
        the bug fires when spouse_age>=65 and filing_status='Single').

        Correct:  eligible=1 → $6,000 (MAGI below phaseout start $75K)
        Buggy:    eligible=2 → $12,000 (double-counts spouse)
        """
        result = senior_bonus_deduction(70, 68, magi=50_000, year=2026, filing_status="Single")
        assert result == approx(6_000)

    def test_single_filer_spouse_65_not_counted(self):
        """The critical case: spouse_age=65 (or older) must NOT be counted for Single.

        Correct:  eligible=1 → $6,000
        Buggy:    eligible=2 → $12,000 (would incorrectly count spouse)
        """
        result = senior_bonus_deduction(70, 65, magi=50_000, year=2026, filing_status="Single")
        assert result == approx(6_000)

    def test_mfj_both_65_still_counts_two(self):
        """MFJ control: both >=65 must still yield $12,000 (behavior unchanged)."""
        result = senior_bonus_deduction(70, 65, magi=50_000, year=2026, filing_status="MFJ")
        assert result == approx(12_000)


# ---------------------------------------------------------------------------
# D4 — compute_headroom IRMAA relevance uses only filer age for Single
# ---------------------------------------------------------------------------


class TestHeadroomIrmaaRelevanceSingle:
    """D4: irmaa_relevant and irmaa_first_relevant_year must not use spouse age
    when filing_status='Single'."""

    def test_single_filer_irmaa_not_relevant_because_of_young_spouse(self):
        """Single filer age 60 (ya+2=62 < 65): irmaa_relevant must be False.

        Buggy code checks (ya+2>=65 OR sa+2>=65); if spouse_age=65 that
        wrongly sets irmaa_relevant=True even for a Single filer.
        Correct: only ya+2>=65 matters for Single.
        """
        hh = Household(
            your_age=60,
            spouse_age=65,  # spouse age that would trigger bug
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            filing_status="MFJ",  # MFJ to confirm it triggers there
        )
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=50_000.0)
        hr_mfj = compute_headroom(hh, ytd, filing_status="MFJ")
        hr_single = compute_headroom(hh, ytd, filing_status="Single")
        # MFJ: spouse_age+2=67>=65 → irmaa_relevant=True
        assert hr_mfj.irmaa_relevant is True
        # Single: only ya+2=62<65 → irmaa_relevant=False (no surcharge at $50K)
        assert hr_single.irmaa_relevant is False

    def test_single_filer_irmaa_first_year_uses_only_filer_age(self):
        """Single filer age 55, spouse_age=63 (would trigger min with sa sooner).

        Buggy: years_until = min(65-2-55, 65-2-63) = min(8, 0) = 0 → immediate
        Correct: years_until = 65-2-55 = 8 → first_relevant_year = base_year + 8
        """
        hh = Household(
            your_age=55,
            spouse_age=63,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=50_000.0)
        hr = compute_headroom(hh, ytd, filing_status="Single")
        # Correct: 65-2-55=8 years until filer hits Medicare
        assert hr.irmaa_first_relevant_year == hh.base_year + 8


# ---------------------------------------------------------------------------
# D5 — compute_year_by_year_timeline respects filing_status=Single
# ---------------------------------------------------------------------------


class TestAcaIrmaaTimelineSingleFiler:
    """D5: Single filer must not see spouse age or spouse system entries in timeline."""

    def test_single_filer_spouse_age_is_none_in_timeline(self):
        """For a Single filer, TimelineRow.spouse_age must be None (not a real age)."""
        hh = Household(
            your_age=62,
            spouse_age=60,  # present in hh data but must be ignored for Single
            your_aca_enrolled=True,
            spouse_aca_enrolled=False,
            filing_status="Single",
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
        )
        rows = compute_year_by_year_timeline(hh, base_magi=50_000, years=5, cpi=hh.cpi_assumption)
        for row in rows:
            assert row.spouse_age is None, (
                f"year {row.year}: expected spouse_age=None for Single, got {row.spouse_age}"
            )

    def test_single_filer_system_string_has_no_spouse_entry(self):
        """Single filer: system string must not contain '(sp)' entries."""
        hh = Household(
            your_age=62,
            spouse_age=60,
            your_aca_enrolled=True,
            spouse_aca_enrolled=False,
            filing_status="Single",
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
        )
        rows = compute_year_by_year_timeline(hh, base_magi=50_000, years=5, cpi=hh.cpi_assumption)
        for row in rows:
            assert "(sp)" not in row.system, (
                f"year {row.year}: Single filer system string must not include spouse: {row.system!r}"
            )

    def test_single_filer_medicare_count_ignores_spouse_age(self):
        """Single filer age 63 with spouse_age=65: IRMAA tier must be None (not on
        Medicare yet) because only filer's age matters.

        Buggy: medicare_count = sum([63>=65, 65>=65]) = 1 → irmaa_tier is set
        Correct: medicare_count = 0 for single filer (63 < 65) → irmaa_tier=None
        """
        hh = Household(
            your_age=63,
            spouse_age=65,  # would trigger bug
            your_aca_enrolled=True,
            spouse_aca_enrolled=False,
            filing_status="Single",
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
        )
        rows = compute_year_by_year_timeline(hh, base_magi=50_000, years=1, cpi=hh.cpi_assumption)
        assert rows[0].irmaa_tier is None, (
            f"Single filer age 63: irmaa_tier should be None, got {rows[0].irmaa_tier}"
        )
