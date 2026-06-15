"""Cross-cutting tests for single-filer foundation paths."""

import pytest

from engine.aca import aca_subsidy
from engine.irmaa import irmaa_surcharge
from engine.niit import niit
from engine.tax import (
    taxable_ss,
)


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
