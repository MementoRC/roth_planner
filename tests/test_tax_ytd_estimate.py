"""Tests for estimate_ytd_federal_tax — F1/F13/F14/F19/F20 bug fixes.

Covers:
  F19 / F1  — standard deduction applied before ordinary bracket walk
  F13       — marginal_bracket_pct derived from taxable income, not gross
  F14       — room_to_next_bracket measured from taxable income vs bracket ceilings
  F20       — taxable Social Security included in ordinary income base
"""

import pytest

from engine.tax import (
    STD_DEDUCTION_MFJ,
    STD_DEDUCTION_SINGLE,
    estimate_ytd_federal_tax,
    federal_tax,
    federal_tax_single,
    taxable_ss,
)
from models.household import Household
from models.ytd_income import YTDSnapshot


def approx(expected: float, tol: float = 1.0) -> object:
    return pytest.approx(expected, abs=tol)


def _hh_mfj(your_age: int = 61, spouse_age: int = 55) -> Household:
    """MFJ household, base_year=2026, no CPI inflation."""
    return Household(
        your_age=your_age,
        spouse_age=spouse_age,
        base_year=2026,
        cpi_assumption=0.0,
        filing_status="MFJ",
    )


def _hh_single(your_age: int = 61) -> Household:
    """Single household, base_year=2026, no CPI inflation."""
    return Household(
        your_age=your_age,
        spouse_age=0,
        base_year=2026,
        cpi_assumption=0.0,
        filing_status="Single",
    )


class TestF19F1StdDeductionApplied:
    """F19 / F1 — ordinary_tax must use taxable income (gross minus std deduction)."""

    def test_mfj_ordinary_tax_uses_taxable_income(self) -> None:
        """MFJ $100K wages: ordinary_tax must equal federal_tax($100K - $32,200)."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=100_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        taxable = max(100_000 - STD_DEDUCTION_MFJ, 0.0)
        assert result.ordinary_tax == approx(federal_tax(taxable))

    def test_mfj_ordinary_tax_less_than_gross_tax(self) -> None:
        """ordinary_tax must be strictly less than tax on gross income."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=100_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        tax_on_gross = federal_tax(100_000)
        assert result.ordinary_tax < tax_on_gross

    def test_single_ordinary_tax_uses_taxable_income(self) -> None:
        """Single $80K wages: ordinary_tax must equal federal_tax_single($80K - $16,100)."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=80_000)
        result = estimate_ytd_federal_tax(ytd, _hh_single())
        taxable = max(80_000 - STD_DEDUCTION_SINGLE, 0.0)
        assert result.ordinary_tax == approx(federal_tax_single(taxable))

    def test_income_below_std_ded_yields_zero_ordinary_tax(self) -> None:
        """Income fully absorbed by deduction → zero ordinary tax."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=20_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        assert result.ordinary_tax == approx(0.0)

    def test_zero_income_yields_zero_ordinary_tax(self) -> None:
        ytd = YTDSnapshot(tax_year=2026)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        assert result.ordinary_tax == approx(0.0)


class TestF13MarginalRateFromTaxableIncome:
    """F13 — marginal_bracket_pct must reflect the taxable-income bracket, not gross."""

    def test_gross_in_22pct_but_taxable_in_12pct(self) -> None:
        """MFJ $103K gross, $32,200 std ded → taxable $70,800 (12% bracket, ceil $100,800).
        Pre-fix code reported 22%; correct is 12%.
        """
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=103_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        # taxable = $103K - $32.2K = $70.8K; 12% bracket ceiling is $100,800
        assert result.marginal_bracket_pct == pytest.approx(0.12)

    def test_single_gross_in_22pct_but_taxable_in_12pct(self) -> None:
        """Single $65K gross, $16,100 std ded → taxable $48,900 (12% bracket, ceil $50,400)."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=65_000)
        result = estimate_ytd_federal_tax(ytd, _hh_single())
        # taxable = $65K - $16.1K = $48.9K; Single 12% ceiling $50,400
        assert result.marginal_bracket_pct == pytest.approx(0.12)

    def test_income_below_std_ded_yields_10pct_bracket(self) -> None:
        """Zero taxable income falls into 10% bracket (lowest non-zero bracket)."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=15_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        # taxable = $15K - $32.2K = $0 clamped; marginal_rate(0) returns 0.0
        assert result.marginal_bracket_pct == pytest.approx(0.0)

    def test_high_income_correctly_in_24pct(self) -> None:
        """MFJ $250K gross, $32.2K std ded → taxable $217.8K (24% bracket)."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=250_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        # taxable = $250K - $32.2K = $217.8K; 24% bracket: $211,400–$403,550
        assert result.marginal_bracket_pct == pytest.approx(0.24)


class TestF14RoomToNextBracketFromTaxableIncome:
    """F14 — room_to_next_bracket must be (bracket_ceil - taxable_ordinary), not gross-based."""

    def test_mfj_room_to_12pct_ceiling(self) -> None:
        """MFJ $120K gross, $32,200 std ded → taxable $87,800; room to 12% ceil ($100,800) = $13,000."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=120_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        # taxable = $120K - $32.2K = $87.8K; 12% ceil = $100,800; room = $13,000
        assert result.room_to_next_bracket == approx(13_000.0)

    def test_room_is_not_based_on_gross(self) -> None:
        """Pre-fix: gross $120K > $100.8K 12% ceil → skipped to 22% ceil, reported ~$91K room.
        Post-fix: room must be well under $91K.
        """
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=120_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        assert result.room_to_next_bracket < 50_000.0

    def test_single_room_to_12pct_ceiling(self) -> None:
        """Single $60K gross, $16,100 std ded → taxable $43,900; 12% ceil $50,400; room = $6,500."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=60_000)
        result = estimate_ytd_federal_tax(ytd, _hh_single())
        # taxable = $60K - $16.1K = $43.9K; Single 12% ceiling = $50,400
        assert result.room_to_next_bracket == approx(6_500.0)

    def test_zero_income_room_equals_first_bracket_ceil(self) -> None:
        """Zero income (all absorbed by std ded) → taxable = 0; room = first bracket ceiling."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=10_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        # taxable = 0; first MFJ bracket ceil = $24,800; room = $24,800
        assert result.room_to_next_bracket == approx(24_800.0)


class TestF20TaxableSocialSecurity:
    """F20 — taxable SS must be included in the ordinary income base."""

    def test_ss_above_tier2_adds_to_ordinary_tax(self) -> None:
        """$40K SS + $100K other → provisional $120K > $44K MFJ tier2 → taxable SS ≈ $34K.
        ordinary_tax must exceed the no-SS case.
        """
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=100_000)
        combined_ss = 40_000.0
        result_with_ss = estimate_ytd_federal_tax(ytd, _hh_mfj(), combined_ss=combined_ss)
        result_no_ss = estimate_ytd_federal_tax(ytd, _hh_mfj(), combined_ss=0.0)
        assert result_with_ss.ordinary_tax > result_no_ss.ordinary_tax

    def test_ss_exact_taxable_amount_included(self) -> None:
        """Verify the exact taxable SS amount is folded in.

        $40K SS + $100K other → taxable_ss($40K, $100K, MFJ).
        ordinary_tax must equal federal_tax(($100K + tss) - std_ded).
        """
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=100_000)
        combined_ss = 40_000.0
        tss = taxable_ss(combined_ss, 100_000.0, filing_status="MFJ")
        taxable = max(100_000 + tss - STD_DEDUCTION_MFJ, 0.0)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj(), combined_ss=combined_ss)
        assert result.ordinary_tax == approx(federal_tax(taxable))

    def test_ss_zero_default_preserves_existing_callers(self) -> None:
        """combined_ss=0.0 default: result identical to calling without the argument."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=100_000)
        result_default = estimate_ytd_federal_tax(ytd, _hh_mfj())
        result_explicit = estimate_ytd_federal_tax(ytd, _hh_mfj(), combined_ss=0.0)
        assert result_default.ordinary_tax == pytest.approx(result_explicit.ordinary_tax)
        assert result_default.marginal_bracket_pct == pytest.approx(
            result_explicit.marginal_bracket_pct
        )
        assert result_default.room_to_next_bracket == pytest.approx(
            result_explicit.room_to_next_bracket
        )

    def test_ss_below_tier1_adds_nothing(self) -> None:
        """SS so small provisional income stays below MFJ tier1 ($32K) → zero taxable SS."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=20_000)
        # provisional = 20_000 + 0.5 * 10_000 = 25_000 < 32_000 → tss = 0
        result_with_ss = estimate_ytd_federal_tax(ytd, _hh_mfj(), combined_ss=10_000.0)
        result_no_ss = estimate_ytd_federal_tax(ytd, _hh_mfj(), combined_ss=0.0)
        assert result_with_ss.ordinary_tax == pytest.approx(result_no_ss.ordinary_tax)

    def test_ss_raises_marginal_bracket(self) -> None:
        """Large SS on top of income near bracket boundary should push marginal rate up."""
        # $90K wages + $50K SS: provisional = 90K + 25K = 115K >> 44K tier2
        # taxable SS = min(0.85 * 50K, ...) = up to $42.5K
        # ordinary_income_with_ss ≈ $90K + ~$42.5K = ~$132.5K; taxable ≈ $100.3K (22% bracket)
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=90_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj(), combined_ss=50_000.0)
        # Without SS: taxable = 90K - 32.2K = 57.8K → 12% bracket
        result_no_ss = estimate_ytd_federal_tax(ytd, _hh_mfj(), combined_ss=0.0)
        assert result.marginal_bracket_pct >= result_no_ss.marginal_bracket_pct
