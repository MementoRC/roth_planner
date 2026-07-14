"""Regression test for audit 2026-07-13 (R1+R2), engine/aca.py effective_benchmark_premium.

Bug: the Single branch blended couple_benchmark via an age-factor ratio using a
spouse_age (even a default/placeholder spouse_age=0), instead of returning the
FULL individual benchmark premium as the function's own docstring promises
("a Single filer has one household adult, so an enrolled Single filer gets the
full individual benchmark rather than a halved couple rate"). For a genuine
single household, ``couple_benchmark`` IS the individual's benchmark premium
(there is no second adult to blend against); the age-ratio blend understated
the benchmark by ~31% ($12,000 -> ~$8,305), overstating out-of-pocket premium
and understating the subsidy.
"""

import pytest

from engine.aca import aca_age_factor, effective_benchmark_premium


class TestSingleFilerFullBenchmark:
    """Single filer enrolled must get the FULL benchmark premium, unblended."""

    def test_single_enrolled_returns_full_benchmark(self) -> None:
        benchmark = 12_000.0
        result = effective_benchmark_premium(
            benchmark,
            your_age=61,
            your_on_aca=True,
            spouse_age=0,
            spouse_on_aca=False,
            filing_status="Single",
        )
        assert result == pytest.approx(benchmark), (
            f"Single filer enrolled must get full benchmark {benchmark}, got {result}"
        )

    def test_single_enrolled_full_benchmark_regardless_of_spouse_age(self) -> None:
        """spouse_age is irrelevant noise for a genuine Single filer (no 2nd adult)."""
        benchmark = 12_000.0
        result_a = effective_benchmark_premium(
            benchmark, your_age=61, your_on_aca=True,
            spouse_age=0, spouse_on_aca=False, filing_status="Single",
        )
        result_b = effective_benchmark_premium(
            benchmark, your_age=61, your_on_aca=True,
            spouse_age=55, spouse_on_aca=False, filing_status="Single",
        )
        assert result_a == pytest.approx(benchmark)
        assert result_b == pytest.approx(benchmark)

    def test_single_not_enrolled_is_zero(self) -> None:
        result = effective_benchmark_premium(
            12_000.0, your_age=61, your_on_aca=False,
            spouse_age=55, spouse_on_aca=False, filing_status="Single",
        )
        assert result == 0.0


class TestMfjBranchUnchanged:
    """MFJ branch must retain age-rated blending for partial enrollment (unchanged)."""

    COUPLE = 21_600.0

    def test_mfj_both_enrolled_is_full_couple_rate(self) -> None:
        result = effective_benchmark_premium(
            self.COUPLE, your_age=61, your_on_aca=True,
            spouse_age=55, spouse_on_aca=True, filing_status="MFJ",
        )
        assert result == pytest.approx(self.COUPLE)

    def test_mfj_one_enrolled_uses_age_rated_share(self) -> None:
        f_you = aca_age_factor(61)
        f_sp = aca_age_factor(55)
        expected = self.COUPLE * f_you / (f_you + f_sp)
        result = effective_benchmark_premium(
            self.COUPLE, your_age=61, your_on_aca=True,
            spouse_age=55, spouse_on_aca=False, filing_status="MFJ",
        )
        assert result == pytest.approx(expected, rel=1e-6)

    def test_mfj_none_enrolled_is_zero(self) -> None:
        result = effective_benchmark_premium(
            self.COUPLE, your_age=61, your_on_aca=False,
            spouse_age=55, spouse_on_aca=False, filing_status="MFJ",
        )
        assert result == 0.0
