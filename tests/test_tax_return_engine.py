"""Tests for tax return engine consumers (parsing + Form 8606 not modeled)."""

import pytest

from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from engine.tax import (
    deductions,
)
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestForm8606NotModeled:
    """Document and lock the 100%-pretax conversion assumption (no Form 8606 basis).

    Per IRC §408(d)(2), if a taxpayer has both pretax and after-tax (basis) dollars
    in a Traditional IRA, every distribution is pro-rated:
        taxable_fraction = pretax_balance / (pretax_balance + basis)

    This engine assumes basis = $0, so every converted dollar is fully taxable.
    See engine/scenario.py 'NOT MODELED: IRA non-deductible basis (Form 8606)' comment.
    """

    def test_conversion_treated_as_fully_pretax(self):
        """Conversions are 100% taxable as ordinary income (no Form 8606 basis pro-rata).

        A $50K conversion with zero other income must produce taxable_income equal to
        $50K minus the standard deduction — i.e., the full conversion amount enters
        the tax base. No basis reduction is applied.
        """
        from dataclasses import replace

        hh = replace(
            Household(grants=[]),
            your_age=61,
            spouse_age=55,
            your_ira=500_000.0,
            spouse_ira=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )
        conversion_amount = 50_000.0
        plan = ConversionPlan(your_conversions={hh.base_year: conversion_amount})
        result = run_scenario(hh, plan, "form8606_pretax", end_age=62)
        yr = result.years[0]

        # The full conversion amount must appear in combined_gross (no basis haircut)
        assert yr.your_conversion == pytest.approx(conversion_amount)
        # Taxable income = conversion_amount - standard deduction (no other income here)
        ded = deductions(hh.your_age, hh.spouse_age, hh.std_deduction, hh.senior_extra)
        expected_taxable = max(conversion_amount - ded, 0.0)
        assert yr.taxable_income == pytest.approx(expected_taxable, rel=1e-6), (
            "Full conversion amount must be taxable — Form 8606 basis pro-rata is not modeled. "
            f"Got taxable_income={yr.taxable_income:,.2f}, expected={expected_taxable:,.2f}"
        )


# ============================================================
#  View-layer filing_status threading regression tests (PR sweeps views)
# ============================================================
