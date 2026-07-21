"""Regression test for audit-0720 finding M1.

deductions() calls index_value() for the base std_ded/senior_extra WITHOUT
round50=True, returning an unrounded CPI-scaled float instead of the IRC
Section 1(f)(6) nearest-$50 amount. Every other indexed quantity in
engine/tax.py passes round50=True (brackets, LTCG breakpoints, the inline
std-deduction in estimate_ytd_federal_tax) — this one silently didn't.
"""

from __future__ import annotations

from engine.tax import deductions


class TestM1DeductionsRound50:
    def test_forward_year_deduction_rounds_to_nearest_50(self) -> None:
        result = deductions(60, 60, year=2027)
        assert result == 33_000.0, (
            f"deductions(60, 60, year=2027) = {result}, expected 33000.0 "
            "(IRC Sec 1(f)(6) nearest-$50 rounding)"
        )
