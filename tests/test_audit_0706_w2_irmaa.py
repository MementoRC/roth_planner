"""Audit-0706 Wave-2 IRMAA regression tests.

Finding irmaa-0: irmaa_for_year must accept a medical_cpi keyword argument and
    forward it to irmaa_surcharge, so callers can override/freeze the medical
    inflation rate.

Finding irmaa-1: irmaa_next_threshold must return float('inf') when MAGI exceeds
    all tiers (no next tier exists), rather than the ambiguous 0.0 that also means
    "exactly at a boundary".  Callers can use math.isinf(room) to detect "Max tier".
"""

import pytest

from engine.irmaa import (
    MEDICAL_INFLATION,
    irmaa_for_year,
    irmaa_next_threshold,
    irmaa_surcharge,
)

# ---------------------------------------------------------------------------
# irmaa-0: medical_cpi override on irmaa_for_year
# ---------------------------------------------------------------------------


class TestIrmaaForYearMedicalCpiParam:
    """irmaa-0: irmaa_for_year must expose medical_cpi so callers can override it."""

    def test_default_medical_cpi_matches_constant(self) -> None:
        """Baseline: default call (no medical_cpi) equals explicit MEDICAL_INFLATION."""
        default, _ = irmaa_for_year(
            300_000, your_age_income_year=63, spouse_age_income_year=63, year=2030
        )
        explicit, _ = irmaa_for_year(
            300_000,
            your_age_income_year=63,
            spouse_age_income_year=63,
            year=2030,
            medical_cpi=MEDICAL_INFLATION,
        )
        assert default == pytest.approx(explicit, rel=1e-9)

    def test_frozen_medical_cpi_zero_differs_from_default(self) -> None:
        """medical_cpi=0.0 (frozen) must differ from the default in an out-year."""
        inflated, _ = irmaa_for_year(
            300_000,
            your_age_income_year=63,
            spouse_age_income_year=63,
            year=2032,
            medical_cpi=MEDICAL_INFLATION,
        )
        frozen, _ = irmaa_for_year(
            300_000,
            your_age_income_year=63,
            spouse_age_income_year=63,
            year=2032,
            medical_cpi=0.0,
        )
        # Both must be positive (both people will be on Medicare in 2034)
        assert inflated > 0
        assert frozen > 0
        # Inflated surcharge must be larger than frozen
        assert inflated > frozen

    def test_nondefault_medical_cpi_changes_surcharge(self) -> None:
        """Passing an arbitrary non-default medical_cpi must change the surcharge."""
        base, _ = irmaa_for_year(
            300_000,
            your_age_income_year=63,
            spouse_age_income_year=63,
            year=2030,
        )
        custom, _ = irmaa_for_year(
            300_000,
            your_age_income_year=63,
            spouse_age_income_year=63,
            year=2030,
            medical_cpi=0.10,  # 10% -- clearly different from 5.5%
        )
        assert base != pytest.approx(custom, rel=1e-3), (
            "irmaa_for_year with medical_cpi=0.10 must produce a different surcharge "
            "than the default MEDICAL_INFLATION=5.5%"
        )

    def test_medical_cpi_forwarded_to_irmaa_surcharge(self) -> None:
        """irmaa_for_year(medical_cpi=X) must equal irmaa_surcharge(..., medical_cpi=X)."""
        magi = 400_000
        ya, sa = 63, 63  # both 65 in medicare year (2030 income -> 2032 Medicare)
        year = 2030
        medical_cpi = 0.03

        via_for_year, _ = irmaa_for_year(
            magi,
            your_age_income_year=ya,
            spouse_age_income_year=sa,
            year=year,
            medical_cpi=medical_cpi,
        )
        # num_people=2 (both 65 in Medicare year), year unchanged (irmaa_for_year
        # doesn't forward-shift the year; the year param already targets the payment year)
        via_surcharge = irmaa_surcharge(
            magi,
            num_people=2,
            year=year,
            medical_cpi=medical_cpi,
        )
        assert via_for_year == pytest.approx(via_surcharge, rel=1e-9)


# ---------------------------------------------------------------------------
# irmaa-1: irmaa_next_threshold returns inf above the highest tier
# ---------------------------------------------------------------------------


class TestIrmaaNextThresholdAboveTopTier:
    """irmaa-1: above all tiers -> float('inf'); exactly-at-boundary -> 0.0."""

    def test_above_top_mfj_tier_returns_inf(self) -> None:
        """MAGI above $750K MFJ frozen tier must return float('inf')."""
        import math

        result = irmaa_next_threshold(800_000)
        assert math.isinf(result), f"Expected inf for MAGI above top MFJ tier, got {result}"
        assert result > 0

    def test_above_top_single_tier_returns_inf(self) -> None:
        """MAGI above $500K Single frozen tier must return float('inf')."""
        import math

        result = irmaa_next_threshold(600_000, filing_status="Single")
        assert math.isinf(result), f"Expected inf for MAGI above top Single tier, got {result}"

    def test_above_top_tier_in_out_year_returns_inf(self) -> None:
        """Above top tier in an out-year (2028) must also return inf."""
        import math

        result = irmaa_next_threshold(800_000, year=2028)
        assert math.isinf(result), f"Expected inf in out-year, got {result}"

    def test_at_boundary_returns_zero(self) -> None:
        """MAGI exactly at the first tier boundary must return 0.0 (not inf)."""
        # At the threshold, magi <= threshold is True -> threshold - magi == 0.0
        result = irmaa_next_threshold(218_000)
        assert result == pytest.approx(0.0, abs=1e-6)

    def test_below_top_tier_returns_positive_distance(self) -> None:
        """MAGI below the top tier must return a positive finite distance."""
        import math

        result = irmaa_next_threshold(400_000)
        assert result > 0
        assert math.isfinite(result)

    def test_inf_distinguishable_from_zero_at_boundary(self) -> None:
        """Confirm inf != 0.0 so callers can distinguish 'at boundary' from 'above top'."""
        import math

        at_boundary = irmaa_next_threshold(218_000)
        above_top = irmaa_next_threshold(800_000)
        assert at_boundary == pytest.approx(0.0)
        assert not math.isinf(at_boundary)
        assert math.isinf(above_top)

    def test_above_frozen_top_in_inversion_year_returns_inf(self) -> None:
        """Above the (still-frozen, year<=2027) top tier must return inf.

        audit-0802 F2: the top tier resumes CPI indexing for 2028+, so the
        original year=2042/cpi=0.04 scenario no longer inverts (both tier-4
        and the top tier grow together past that MAGI). Pinned to year=2027
        (last frozen year) with an extreme cpi=1.0 (100%; not economically
        realistic, chosen only to force the historical min()-clamp regression
        this test guards: unclamped tier-4 = 410_000*2 = 820_000 > frozen
        $750K top within a single year of compounding).
        """
        import math

        result_755 = irmaa_next_threshold(755_000, "MFJ", year=2027, cpi=1.0)
        result_760 = irmaa_next_threshold(760_000, "MFJ", year=2027, cpi=1.0)
        assert math.isinf(result_755), f"Expected inf for 755K above top, got {result_755}"
        assert math.isinf(result_760), f"Expected inf for 760K above top, got {result_760}"
