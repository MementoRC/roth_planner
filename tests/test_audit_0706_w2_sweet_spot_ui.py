"""TDD tests for ui-primary-14: binary-search IRMAA-Safe Max in views/sweet_spot.py.

Finding: 'IRMAA-Safe Max' overstates safe conversion when SS is in the
partial-taxability zone. The naive subtraction `irmaa_tiers[0][0] - base.base_magi`
ignores that each $1 converted can raise MAGI by up to $1.85 when SS provisional
income is in the partial-taxability zone ($32K-$44K MFJ / $25K-$34K Single).

Fix: replace the naive formula with a binary search over
`all_in_at_conversion(...).magi <= irmaa_tier1_threshold`.
"""

from __future__ import annotations

import pytest

from engine.sweet_spot_compute import (
    BaseIncome,
    all_in_at_conversion,
    base_income_for_year,
    irmaa_safe_max,
)
from engine.tax import SS_TIER_1_MFJ, taxable_ss
from models.household import Household

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOW_THRESHOLD = 15_000.0  # synthetic low IRMAA threshold for partial-zone tests
_MFJ_TIER1_2026 = 218_000.0  # real 2026 MFJ IRMAA tier-1 (no indexing, cpi=0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_mfj_ss_partial(
    combined_ss: float = 40_000.0,
    opt: float = 0.0,
    total_ded: float = 30_000.0,
) -> BaseIncome:
    """BaseIncome with SS provisional income BELOW $32K at zero conversion.

    As conversions are added, provisional income enters the $32K-$44K zone where
    each $1 converted raises MAGI by $1.50 (not $1.00).
    """
    provisional_at_base = opt + 0.5 * combined_ss
    assert provisional_at_base < SS_TIER_1_MFJ, (
        f"Fixture broken: base provisional ({provisional_at_base}) must be < $32K"
    )
    tss = taxable_ss(combined_ss, opt, filing_status="MFJ")
    base_gross = opt + tss
    return BaseIncome(
        ya=66,
        sa=64,
        year=2026,
        cpi=0.0,
        opt=opt,
        combined_ss=combined_ss,
        base_gross=base_gross,
        base_magi=base_gross,
        total_ded=total_ded,
        ded_base=total_ded,
        ytd_magi=0.0,
        ytd_niit_magi=0.0,
    )


def _minimal_mfj_hh() -> Household:
    """Minimal MFJ household - no ACA, no options, no brokerage."""
    return Household(
        your_age=66,
        spouse_age=64,
        base_year=2026,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        your_ss_start_age=70,
        spouse_ss_start_age=70,
        filing_status="MFJ",
        cpi_assumption=0.0,
        ss_cola=0.0,
        your_aca_enrolled=False,
        spouse_aca_enrolled=False,
        brokerage_start=0.0,
        your_ira=2_000_000.0,
        spouse_ira=2_000_000.0,
    )


# ---------------------------------------------------------------------------
# Section 1: document the bug (naive formula overshoots)
# ---------------------------------------------------------------------------


class TestNaiveFormulaOvershoots:
    """Prove the naive `threshold - base_magi` formula gives a conversion that
    actually exceeds the IRMAA threshold when SS is in the partial-taxability zone.
    """

    def test_partial_zone_magi_exceeds_threshold_with_naive_amount(self) -> None:
        """Naive safe = $15K - $0 = $15K. At conv=$15K: provisional = $35K in zone.
        tss = 0.5*($35K - $32K) = $1,500 -> magi = $16,500 > $15K.
        """
        hh = _minimal_mfj_hh()
        base = _base_mfj_ss_partial(combined_ss=40_000.0, opt=0.0)

        assert base.base_magi == pytest.approx(0.0)

        naive_safe = _LOW_THRESHOLD - base.base_magi  # = $15,000
        result = all_in_at_conversion(hh, base, naive_safe, 0.0)

        assert result.magi > _LOW_THRESHOLD, (
            f"Expected magi={result.magi:.0f} > threshold={_LOW_THRESHOLD:.0f} "
            "to confirm naive formula overshoots in SS partial-taxability zone"
        )

    def test_partial_zone_magi_multiplier_is_150_percent(self) -> None:
        """In the $32K-$44K provisional zone each $1 converted raises MAGI by $1.50."""
        hh = _minimal_mfj_hh()
        base = _base_mfj_ss_partial(combined_ss=40_000.0, opt=0.0)

        r14 = all_in_at_conversion(hh, base, 14_000.0, 0.0)
        r15 = all_in_at_conversion(hh, base, 15_000.0, 0.0)

        delta_magi = r15.magi - r14.magi
        assert delta_magi == pytest.approx(1_500.0, abs=1.0), (
            f"Expected 1.5x MAGI multiplier in partial zone; got {delta_magi:.0f} per $1K"
        )


# ---------------------------------------------------------------------------
# Section 2: irmaa_safe_max binary-search helper (RED before fix)
# ---------------------------------------------------------------------------


class TestIrmaaSafeMax:
    """Tests for `irmaa_safe_max` in engine/sweet_spot_compute.py.

    RED before the function exists; GREEN after implementation.
    """

    def test_result_magi_at_or_below_threshold(self) -> None:
        """The conversion returned by irmaa_safe_max gives magi <= threshold."""
        hh = _minimal_mfj_hh()
        base = _base_mfj_ss_partial(combined_ss=40_000.0, opt=0.0)

        safe = irmaa_safe_max(hh, base, _LOW_THRESHOLD)

        result = all_in_at_conversion(hh, base, safe, 0.0)
        assert result.magi <= _LOW_THRESHOLD + 0.01, (
            f"irmaa_safe_max={safe:.0f} -> magi={result.magi:.0f} > threshold={_LOW_THRESHOLD:.0f}"
        )

    def test_result_strictly_less_than_naive_in_partial_zone(self) -> None:
        """Binary-search result must be strictly less than the naive subtraction."""
        hh = _minimal_mfj_hh()
        base = _base_mfj_ss_partial(combined_ss=40_000.0, opt=0.0)

        naive_safe = _LOW_THRESHOLD - base.base_magi
        safe = irmaa_safe_max(hh, base, _LOW_THRESHOLD)

        assert safe < naive_safe, (
            f"irmaa_safe_max={safe:.0f} should be < naive={naive_safe:.0f} in partial zone"
        )

    def test_result_is_14000_for_known_scenario(self) -> None:
        """magi(conv) = 1.5*conv - 6K in zone; threshold=$15K -> conv=$14K exactly."""
        hh = _minimal_mfj_hh()
        base = _base_mfj_ss_partial(combined_ss=40_000.0, opt=0.0)

        safe = irmaa_safe_max(hh, base, _LOW_THRESHOLD)

        assert safe == pytest.approx(14_000.0, abs=1_000.0), (
            f"Expected safe max ~$14K, got {safe:.0f}"
        )

    def test_returns_zero_when_base_magi_exceeds_threshold(self) -> None:
        """When base_magi already exceeds the threshold, return 0."""
        hh = _minimal_mfj_hh()
        base = BaseIncome(
            ya=66,
            sa=64,
            year=2026,
            cpi=0.0,
            opt=20_000.0,
            combined_ss=0.0,
            base_gross=20_000.0,
            base_magi=20_000.0,
            total_ded=30_000.0,
            ded_base=30_000.0,
            ytd_magi=0.0,
            ytd_niit_magi=0.0,
        )

        safe = irmaa_safe_max(hh, base, _LOW_THRESHOLD)
        assert safe == pytest.approx(0.0)

    def test_ss_fully_taxed_binary_search_matches_naive(self) -> None:
        """When SS fully taxed at base, tss is fixed; magi rises 1:1 -> binary ~= naive.

        opt=$200K, combined_ss=$20K -> provisional=$210K >> $44K -> tss=$17K fixed.
        base_magi = $217K. Naive safe = $218K - $217K = $1K. Binary search ~= $1K.
        """
        hh = _minimal_mfj_hh()
        opt = 200_000.0
        combined_ss = 20_000.0
        tss = taxable_ss(combined_ss, opt, filing_status="MFJ")
        base = BaseIncome(
            ya=66,
            sa=64,
            year=2026,
            cpi=0.0,
            opt=opt,
            combined_ss=combined_ss,
            base_gross=opt + tss,
            base_magi=opt + tss,
            total_ded=30_000.0,
            ded_base=30_000.0,
            ytd_magi=0.0,
            ytd_niit_magi=0.0,
        )

        threshold = _MFJ_TIER1_2026
        naive_safe = threshold - base.base_magi
        safe = irmaa_safe_max(hh, base, threshold)

        assert abs(safe - naive_safe) <= 1_000.0, (
            f"Fully-taxed SS: binary_search={safe:.0f} should ~= naive={naive_safe:.0f}"
        )
        result = all_in_at_conversion(hh, base, safe, 0.0)
        assert result.magi <= threshold + 0.01


# ---------------------------------------------------------------------------
# Section 3: compute_multi_year_summary uses binary search (RED before fix)
# ---------------------------------------------------------------------------


class TestSummaryIrmaaSafeCorrect:
    """compute_multi_year_summary must use binary-search irmaa_safe, not naive formula.

    Tests that irmaa_safe from the summary gives actual magi <= tier-1 threshold.
    """

    def _hh_claiming_ss_at_base_year(self) -> Household:
        """MFJ household claiming SS in 2026; provisional income near (but below) $32K."""
        return Household(
            your_age=62,
            spouse_age=60,
            base_year=2026,
            your_ss_fra=1_500.0,
            spouse_ss_fra=1_000.0,
            your_ss_start_age=62,
            spouse_ss_start_age=70,
            your_fra_age=67,
            spouse_fra_age=67,
            filing_status="MFJ",
            cpi_assumption=0.0,
            ss_cola=0.0,
            your_aca_enrolled=False,
            spouse_aca_enrolled=False,
            brokerage_start=0.0,
            your_ira=500_000.0,
            spouse_ira=500_000.0,
        )

    def test_year_summary_irmaa_safe_magi_at_or_below_tier1(self) -> None:
        """irmaa_safe from summary gives magi <= IRMAA tier-1 threshold."""
        from engine.irmaa import IRMAA_TIERS_MFJ, _index_irmaa_tiers
        from engine.sweet_spot_compute import compute_multi_year_summary

        hh = self._hh_claiming_ss_at_base_year()
        rows = compute_multi_year_summary(hh)
        row_2026 = next(r for r in rows if r.year == 2026)

        if row_2026.irmaa_safe is None or row_2026.irmaa_safe == 0.0:
            pytest.skip("base MAGI at/above tier 1 - no safe conversion to test")

        b = base_income_for_year(hh, 2026)
        irmaa_tiers = _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2028, 0.0)  # payment year
        tier1 = irmaa_tiers[0][0]

        result = all_in_at_conversion(hh, b, row_2026.irmaa_safe, 0.0)
        assert result.magi <= tier1 + 0.01, (
            f"irmaa_safe={row_2026.irmaa_safe:.0f} -> magi={result.magi:.0f} > tier1={tier1:.0f}"
        )
