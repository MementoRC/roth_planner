"""Regression test for deep-review 2026-06-18 PR-G5 (conversion-tax baseline deduction)."""

import pytest

from engine.scenario_compute import compute_federal_tax
from engine.tax import (
    SENIOR_EXTRA_MFJ,
    STD_DEDUCTION_MFJ,
    deductions,
    senior_bonus_deduction,
)


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestConversionTaxBaselineDeduction:
    def test_baseline_uses_no_conversion_obbba_deduction(self):
        """scenario-math-3: the incremental conversion tax must compute its no-conversion
        baseline with the OBBBA bonus evaluated at the NO-conversion MAGI.

        MFJ, both 66, year 2026 (cpi 0). A $60k conversion pushes MAGI 150k -> 210k.
        Baseline OBBBA bonus should be the full $12k (at 150k), not the phased-out $4.8k
        (at 210k). Passing the phased-out deduction as the baseline (the old bug) makes
        the conversion look ~$1,584 cheaper than it is.
        """
        year, cpi = 2026, 0.0
        ya = sa = 66
        std_senior = deductions(ya, sa, STD_DEDUCTION_MFJ, SENIOR_EXTRA_MFJ, year=year, cpi=cpi)
        full_ded = std_senior + senior_bonus_deduction(ya, sa, 150_000, year=year, cpi=cpi)
        phased_ded = std_senior + senior_bonus_deduction(ya, sa, 210_000, year=year, cpi=cpi)
        # audit-0809 C19: phaseout reduces each person's $6,000 independently, floored
        # per person, then summed (12k -> 4.8k at 210k MAGI), per IRS Schedule 1-A
        # Part V lines 31-37 -- not the aggregate 12k - 3.6k formula audit-0722b used.
        assert full_ded - phased_ded == approx(7_200.0)

        combined_gross = 210_000.0
        taxable_with = max(combined_gross - phased_ded, 0.0)

        _, _, conv_tax_correct, _ = compute_federal_tax(
            taxable_with, combined_gross, 60_000.0, 0.0, full_ded, "MFJ", year, cpi
        )
        _, _, conv_tax_buggy, _ = compute_federal_tax(
            taxable_with, combined_gross, 60_000.0, 0.0, phased_ded, "MFJ", year, cpi
        )

        assert conv_tax_correct > conv_tax_buggy
        # recaptured $7,200 of deduction taxed at the 22% MFJ bracket = ~$1,584
        assert conv_tax_correct - conv_tax_buggy == approx(7_200.0 * 0.22, tol=5.0)
