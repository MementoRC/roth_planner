"""Tests for AUDIT 2026-06-20 cluster #3: IRMAA tier CPI-indexing + frozen Tier-5.

Closes F15/F16/F17/F24/F29/F31/F34/F40/F47/F55.
"""

from __future__ import annotations

import pytest

from engine.aca_irmaa_compute import index_irmaa_tier_thresholds
from engine.irmaa import (
    BASE_YEAR,
    DEFAULT_CPI,
    IRMAA_TIERS_MFJ,
    IRMAA_TIERS_SINGLE,
    _index_irmaa_tiers,
    irmaa_tier,
)


class TestIrmaaTierIndexing:
    """Root Fix A: irmaa_tier() now accepts year/cpi and indexes thresholds forward."""

    def test_base_year_behavior_unchanged(self) -> None:
        """Calling with defaults (base year) matches old un-indexed behavior."""
        # MAGI just below Tier-1 MFJ threshold → tier 0
        assert irmaa_tier(200_000) == 0
        # MAGI above Tier-1 MFJ threshold ($218K) → tier 1
        assert irmaa_tier(220_000) == 1

    def test_future_year_lower_tier_for_magi_just_above_base_threshold(self) -> None:
        """Root Fix A (F15/F24/F29/F47): MAGI just above the 2026 Tier-1 threshold
        should fall BELOW the indexed Tier-1 in a future year.

        2026 MFJ Tier-1 = $218,000.  At cpi=0.025:
          2027: 218_000 * 1.025^1 = ~$223,450
          2028: 218_000 * 1.025^2 = ~$229,037

        So MAGI=220_000 is above the 2026 base (tier 1) but below the 2028
        indexed threshold (tier 0).
        """
        # Un-indexed base call: $220K > $218K → tier 1
        assert irmaa_tier(220_000, year=BASE_YEAR, cpi=DEFAULT_CPI) == 1

        # Indexed 2028 call: indexed threshold ≈ $229K → $220K is below it → tier 0
        assert irmaa_tier(220_000, year=2028, cpi=0.025) == 0

    def test_future_year_magi_above_indexed_threshold_still_correct_tier(self) -> None:
        """MAGI well above an indexed threshold is still classified correctly."""
        # $300K is above both 2026 and 2028 indexed Tier-1 ($229K) → should be tier 2 or higher
        assert irmaa_tier(300_000, year=2028, cpi=0.025) >= 2

    def test_single_filing_status_uses_single_tiers(self) -> None:
        """Single filing status uses Single tier table with indexing."""
        # 2026 Single Tier-1 = $109K
        # Just above base → tier 1 in base year
        assert irmaa_tier(112_000, filing_status="Single", year=BASE_YEAR) == 1
        # In 2028 with 2.5% cpi: indexed ≈ $109K * 1.025^2 ≈ $114.5K → $112K is below → tier 0
        assert irmaa_tier(112_000, filing_status="Single", year=2028, cpi=0.025) == 0

    def test_tier_5_mfi_not_affected_by_year(self) -> None:
        """MAGI above Tier-5 threshold gives tier 5 regardless of year (Tier 5 frozen)."""
        assert irmaa_tier(800_000, year=2028, cpi=0.025) == 5
        assert irmaa_tier(800_000, year=BASE_YEAR, cpi=0.025) == 5


class TestFrozenTier5NotIndexed:
    """Root Fix B (F16/F17/F34/F40/F55): Tier 5 is not CPI-inflated through
    2027 (BBA 2018 freeze). See TestIrmaaTopTierResumesIndexing2028 for the
    2028+ behavior, corrected by audit-0802 F2.
    """

    def test_index_irmaa_tiers_freezes_last_tier(self) -> None:
        """_index_irmaa_tiers preserves the frozen Tier-5 threshold exactly through 2027."""
        base_t5_threshold = IRMAA_TIERS_MFJ[-1][0]  # 750_000

        # At year=2027 (last frozen year) cpi=0.025, Tier 5 must remain at base value
        indexed = _index_irmaa_tiers(IRMAA_TIERS_MFJ, year=2027, cpi=0.025)
        assert indexed[-1][0] == base_t5_threshold, (
            f"Tier 5 was inflated: expected {base_t5_threshold}, got {indexed[-1][0]}"
        )

    def test_index_irmaa_tiers_inflates_lower_tiers(self) -> None:
        """Tiers 1-4 ARE indexed upward."""
        base_t1_threshold = IRMAA_TIERS_MFJ[0][0]  # 218_000

        indexed = _index_irmaa_tiers(IRMAA_TIERS_MFJ, year=2028, cpi=0.025)
        assert indexed[0][0] > base_t1_threshold, (
            f"Tier 1 was not inflated: expected > {base_t1_threshold}, got {indexed[0][0]}"
        )

    def test_index_irmaa_tier_thresholds_freezes_tier5(self) -> None:
        """F55: index_irmaa_tier_thresholds (used by views/aca_irmaa.py) also freezes Tier 5 through 2027."""
        base_t5_threshold = IRMAA_TIERS_MFJ[-1][0]

        indexed = index_irmaa_tier_thresholds(IRMAA_TIERS_MFJ, year=2027, cpi=0.025)
        assert indexed[-1][0] == base_t5_threshold, (
            f"index_irmaa_tier_thresholds inflated Tier 5: "
            f"expected {base_t5_threshold}, got {indexed[-1][0]}"
        )

    def test_index_irmaa_tiers_single_freezes_last_tier(self) -> None:
        """Single filing Tier-5 ($500K) is also frozen through 2027."""
        base_t5_single = IRMAA_TIERS_SINGLE[-1][0]  # 500_000
        indexed = _index_irmaa_tiers(IRMAA_TIERS_SINGLE, year=2027, cpi=0.03)
        assert indexed[-1][0] == base_t5_single

    def test_index_irmaa_tiers_empty_input(self) -> None:
        """Empty tier list returns empty list without error."""
        assert _index_irmaa_tiers([], year=2028, cpi=0.025) == []

    def test_index_irmaa_tiers_single_item(self) -> None:
        """Single-item tier list: the only tier is treated as frozen (last tier rule)."""
        single = [(100_000, 1000.0, 200.0)]
        indexed = _index_irmaa_tiers(single, year=2028, cpi=0.025)
        assert indexed[0][0] > 100_000, (
            "audit-0802 F2: the (last-tier) threshold resumes indexing in 2028"
        )


class TestIrmaaTopTierResumesIndexing2028:
    """audit-0802 F2: BBA 2018 (Pub. L. 115-123, §53109) / 42 U.S.C.
    §1395r(i)(5)(C) freezes the top IRMAA tier ($500K Single / $750K MFJ)
    only for years 2020-2027. Years 2028+ resume CPI indexing off an
    Aug-2026-effective base: top(year) = base_top * (1+cpi) ** (year - 2027).
    Corrects the prior "frozen forever" model asserted by
    TestFrozenTier5NotIndexed above (that class now only covers <=2027).
    """

    _MFJ_TOP = IRMAA_TIERS_MFJ[-1][0]  # 750_000

    def test_year_2027_still_frozen_at_base(self) -> None:
        """2027 is the last year of the statutory freeze — top stays at base."""
        indexed = _index_irmaa_tiers(IRMAA_TIERS_MFJ, year=2027, cpi=0.03)
        assert indexed[-1][0] == pytest.approx(self._MFJ_TOP)

    def test_year_2028_resumes_indexing(self) -> None:
        """2028 is the first post-freeze year: top = base * (1+cpi)^1."""
        cpi = 0.03
        indexed = _index_irmaa_tiers(IRMAA_TIERS_MFJ, year=2028, cpi=cpi)
        expected = self._MFJ_TOP * (1.0 + cpi) ** 1
        assert indexed[-1][0] == pytest.approx(expected)
        assert indexed[-1][0] > self._MFJ_TOP

    def test_year_2030_indexes_three_factors(self) -> None:
        """2030: top = base * (1+cpi)^3 (exponent counts from the 2027 anchor)."""
        cpi = 0.03
        indexed = _index_irmaa_tiers(IRMAA_TIERS_MFJ, year=2030, cpi=cpi)
        expected = self._MFJ_TOP * (1.0 + cpi) ** 3
        assert indexed[-1][0] == pytest.approx(expected)

    def test_year_2028_zero_cpi_no_change(self) -> None:
        """cpi=0.0 -> (1+0)^n == 1, so the top stays at base even post-freeze."""
        indexed = _index_irmaa_tiers(IRMAA_TIERS_MFJ, year=2028, cpi=0.0)
        assert indexed[-1][0] == pytest.approx(self._MFJ_TOP)

    def test_lower_tier_still_clamped_to_indexed_top_far_future(self) -> None:
        """A lower tier's indexed value must never exceed the (now-indexed) top."""
        cpi = 0.025
        indexed = _index_irmaa_tiers(IRMAA_TIERS_MFJ, year=2050, cpi=cpi)
        top_threshold = indexed[-1][0]
        assert top_threshold > self._MFJ_TOP  # sanity: it did index
        for threshold, _, _ in indexed[:-1]:
            assert threshold <= top_threshold
