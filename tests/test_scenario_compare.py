"""Regression tests for engine.scenario_compare.survivor_year_tax."""

import pytest

from engine.scenario_compare import survivor_year_tax
from engine.tax import (
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    federal_tax_single,
    senior_bonus_deduction,
    taxable_ss,
)


def test_survivor_year_tax_indexes_to_projection_year() -> None:
    # Inflation-grown future income taxed against INDEXED brackets + deduction must
    # be strictly less than the same nominal income taxed against raw 2026 values.
    age, rmd, ss = 81, 150_000.0, 40_000.0
    cpi = 0.025
    tax_fixed, bracket_fixed, taxable_fixed = survivor_year_tax(age, rmd, ss, year=2051, cpi=cpi)
    assert tax_fixed == pytest.approx(federal_tax_single(taxable_fixed, year=2051, cpi=cpi))
    assert 0.0 < bracket_fixed < 1.0
    tss = taxable_ss(ss, rmd, filing_status="Single")
    gross = rmd + tss
    ded_buggy = float(STD_DEDUCTION_SINGLE + SENIOR_EXTRA_SINGLE)
    ded_buggy += senior_bonus_deduction(age, 0, gross, year=2051, cpi=cpi, filing_status="Single")
    taxable_buggy = max(gross - ded_buggy, 0.0)
    tax_buggy = federal_tax_single(taxable_buggy)
    assert tax_fixed < tax_buggy
