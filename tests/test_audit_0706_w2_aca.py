"""Regression tests for audit-0706 Wave 2 — engine/aca.py findings.

aca-1: Enhanced schedule uses <= at 150% FPL, misclassifying exactly 150% into 0% band.
aca-2: effective_benchmark_premium returns full couple-rate for Single survivor (overstated).
aca-3: aca_premium_cap_rate returns non-zero for MAGI < 100% FPL (pre-ARP, no lower guard).
aca-4: aca_applies has no minimum-age lower bound (age=0 incorrectly returns True).
"""

import pytest

from engine.aca import (
    FPL_1,
    FPL_2,
    aca_age_factor,
    aca_applies,
    aca_premium_cap_rate,
    effective_benchmark_premium,
)

# ---------------------------------------------------------------------------
# aca-1: Enhanced schedule — exactly 150% FPL must fall in 2% band, not 0% band
# ---------------------------------------------------------------------------


class TestAca1EnhancedFPLBoundary:
    """ACA-1: Enhanced schedule <= lookup misclassifies exactly 150% FPL into 0% band."""

    def test_enhanced_exactly_150pct_fpl_enters_two_pct_band(self) -> None:
        """Bug: enhanced schedule returns 0.0 at exactly 150% FPL (wrong band).

        The enhanced schedule entry (1.50, 0.00) means BELOW 150% FPL -> 0%.
        Exactly 150% FPL should fall into the NEXT band (150-200% -> 2% cap).
        With <= the boundary is greedily assigned to 0%, which is wrong.
        """
        magi_at_150 = 1.50 * FPL_2  # MFJ
        rate = aca_premium_cap_rate(
            magi_at_150, enhanced_subsidies_active=True, filing_status="MFJ"
        )
        # After fix: exactly 150% FPL should return 0.02 (2% band), NOT 0.00
        assert rate == pytest.approx(0.02), (
            f"At exactly 150% FPL enhanced schedule should return 0.02 (2% band), got {rate}"
        )

    def test_enhanced_below_150pct_fpl_is_zero_band(self) -> None:
        """Below 150% FPL (exclusive) -> 0% band is correct (should pass before and after fix)."""
        magi_below = 1.499 * FPL_2
        rate = aca_premium_cap_rate(
            magi_below, enhanced_subsidies_active=True, filing_status="MFJ"
        )
        assert rate == pytest.approx(0.00)

    def test_enhanced_above_150pct_fpl_is_two_pct_band(self) -> None:
        """Just above 150% FPL -> 2% band (should pass both before and after fix)."""
        magi_above = 1.501 * FPL_2
        rate = aca_premium_cap_rate(
            magi_above, enhanced_subsidies_active=True, filing_status="MFJ"
        )
        assert rate == pytest.approx(0.02)

    def test_enhanced_single_exactly_150pct_fpl(self) -> None:
        """Single filer: exactly 150% FPL_1 enhanced -> 2% band."""
        magi_at_150 = 1.50 * FPL_1
        rate = aca_premium_cap_rate(
            magi_at_150, enhanced_subsidies_active=True, filing_status="Single"
        )
        assert rate == pytest.approx(0.02), (
            f"Single: at exactly 150% FPL enhanced should return 0.02, got {rate}"
        )


# ---------------------------------------------------------------------------
# aca-2: Single survivor benchmark — age-ratio share, not full couple rate
# ---------------------------------------------------------------------------


class TestAca2SingleSurvivorBenchmark:
    """ACA-2: effective_benchmark_premium returns full couple-rate for Single survivor.

    A Single survivor uses a couple_benchmark (two-person rate). Their correct
    individual rate is their age-weighted share: couple_benchmark * factor(your_age)
    / (factor(your_age) + factor(spouse_age)).
    """

    COUPLE = 21_600.0

    def test_single_enrolled_returns_age_ratio_share(self) -> None:
        """Single enrolled at age 62, spouse_age 58 -- must return age-ratio share.

        factor(62)=2.873, factor(58)=2.548
        share = 21600 * 2.873 / (2.873 + 2.548)
        """
        your_age = 62
        spouse_age = 58
        f_you = aca_age_factor(your_age)   # 2.873
        f_sp = aca_age_factor(spouse_age)  # 2.548
        expected = self.COUPLE * f_you / (f_you + f_sp)

        result = effective_benchmark_premium(
            self.COUPLE,
            your_age=your_age,
            your_on_aca=True,
            spouse_age=spouse_age,
            spouse_on_aca=False,
            filing_status="Single",
        )
        assert result == pytest.approx(expected, rel=1e-6), (
            f"Single enrolled: expected age-ratio share {expected:.2f}, got {result:.2f}"
        )
        # Must be less than full couple rate (single person's share < two-person rate)
        assert result < self.COUPLE

    def test_single_enrolled_returns_less_than_full_couple_rate(self) -> None:
        """Single survivor's age-ratio share must be strictly less than the couple rate."""
        result = effective_benchmark_premium(
            self.COUPLE,
            your_age=61,
            your_on_aca=True,
            spouse_age=55,
            spouse_on_aca=False,
            filing_status="Single",
        )
        # After fix: Single gets age-ratio share, NOT the full couple_benchmark
        assert result < self.COUPLE, (
            f"Single filer should get age-ratio share < couple rate, got {result} == {self.COUPLE}"
        )

    def test_single_not_enrolled_is_zero(self) -> None:
        """Single filer not enrolled -> 0.0 regardless (unchanged)."""
        result = effective_benchmark_premium(
            self.COUPLE,
            your_age=61,
            your_on_aca=False,
            spouse_age=55,
            spouse_on_aca=False,
            filing_status="Single",
        )
        assert result == 0.0

    def test_mfj_both_enrolled_unchanged(self) -> None:
        """MFJ both enrolled -> still full couple rate (unchanged by fix)."""
        result = effective_benchmark_premium(
            self.COUPLE,
            your_age=61,
            your_on_aca=True,
            spouse_age=55,
            spouse_on_aca=True,
            filing_status="MFJ",
        )
        assert result == pytest.approx(self.COUPLE)

    def test_mfj_one_enrolled_age_ratio_unchanged(self) -> None:
        """MFJ one enrolled -> age-ratio share (unchanged by fix)."""
        f_you = aca_age_factor(61)  # 2.810
        f_sp = aca_age_factor(55)   # 2.230
        expected = self.COUPLE * f_you / (f_you + f_sp)
        result = effective_benchmark_premium(
            self.COUPLE,
            your_age=61,
            your_on_aca=True,
            spouse_age=55,
            spouse_on_aca=False,
            filing_status="MFJ",
        )
        assert result == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# aca-3: aca_premium_cap_rate lower bound -- below 100% FPL must return 0.0
# ---------------------------------------------------------------------------


class TestAca3CapRateLowerBound:
    """ACA-3: aca_premium_cap_rate returns non-zero for MAGI < 100% FPL (pre-ARP).

    The aca_subsidy() function has a 100% FPL floor, but the public
    aca_premium_cap_rate() does not -- it returns non-zero rates for sub-100% MAGI,
    inconsistent with PTC-ineligibility (IRC section 36B(c)(1)(A)).
    """

    def test_pre_arp_below_100pct_fpl_cap_rate_is_zero(self) -> None:
        """Pre-ARP: below 100% FPL must return 0.0 cap rate (PTC-ineligible)."""
        magi_below = 0.99 * FPL_2  # just below 100% FPL
        rate = aca_premium_cap_rate(
            magi_below, enhanced_subsidies_active=False, filing_status="MFJ"
        )
        assert rate == 0.0, (
            f"Pre-ARP: MAGI below 100% FPL must yield cap_rate=0.0, got {rate}"
        )

    def test_pre_arp_zero_magi_cap_rate_is_zero(self) -> None:
        """Pre-ARP: MAGI=0 must return 0.0 (well below 100% FPL)."""
        rate = aca_premium_cap_rate(0.0, enhanced_subsidies_active=False, filing_status="MFJ")
        assert rate == 0.0, f"Pre-ARP: MAGI=0 must yield cap_rate=0.0, got {rate}"

    def test_pre_arp_at_100pct_fpl_cap_rate_nonzero(self) -> None:
        """Pre-ARP: at exactly 100% FPL the first band (2.10%) applies (unchanged)."""
        rate = aca_premium_cap_rate(FPL_2, enhanced_subsidies_active=False, filing_status="MFJ")
        assert rate == pytest.approx(0.0210), (
            f"Pre-ARP: at 100% FPL should return 2.10%, got {rate}"
        )

    def test_pre_arp_below_100pct_single_cap_rate_is_zero(self) -> None:
        """Single filer: below 100% FPL_1 must return 0.0."""
        magi_below = 0.5 * FPL_1
        rate = aca_premium_cap_rate(
            magi_below, enhanced_subsidies_active=False, filing_status="Single"
        )
        assert rate == 0.0, (
            f"Single: MAGI below 100% FPL must yield cap_rate=0.0, got {rate}"
        )

    def test_enhanced_below_100pct_fpl_not_affected(self) -> None:
        """Enhanced schedule: below 150% FPL returns 0% cap -- fix is only for pre-ARP."""
        # Enhanced at 50% FPL: below 150% threshold -> 0% cap (first enhanced band)
        magi_below = 0.5 * FPL_2
        rate = aca_premium_cap_rate(
            magi_below, enhanced_subsidies_active=True, filing_status="MFJ"
        )
        # Enhanced has no 100% floor, just the 150% threshold -> returns 0.00
        assert rate == pytest.approx(0.00)


# ---------------------------------------------------------------------------
# aca-4: aca_applies minimum-age guard -- age=0 must return False
# ---------------------------------------------------------------------------


class TestAca4AppliiesMinimumAge:
    """ACA-4: aca_applies has no minimum-age lower bound.

    age=0, -1, etc. should return False (not enrolled in ACA marketplace).
    The pre-Medicare window is 0 < age < 65.
    """

    def test_age_zero_returns_false(self) -> None:
        """age=0 with enrolled=True must return False."""
        assert aca_applies(0, enrolled=True) is False, (
            "age=0 should not qualify for ACA marketplace"
        )

    def test_negative_age_returns_false(self) -> None:
        """Negative age must return False."""
        assert aca_applies(-1, enrolled=True) is False, (
            "Negative age should not qualify for ACA marketplace"
        )

    def test_valid_age_enrolled_returns_true(self) -> None:
        """Valid pre-Medicare age (61) enrolled -> True (unchanged)."""
        assert aca_applies(61, enrolled=True) is True

    def test_age_65_returns_false(self) -> None:
        """age=65 -> False (Medicare age, unchanged)."""
        assert aca_applies(65, enrolled=True) is False

    def test_not_enrolled_returns_false(self) -> None:
        """not enrolled -> False regardless of age (unchanged)."""
        assert aca_applies(61, enrolled=False) is False
