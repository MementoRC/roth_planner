"""Regression tests for deep-review 2026-06-18 PR-G1 (OBBBA senior-deduction fixes)."""

import pytest

from engine.tax import estimate_ytd_federal_tax, senior_bonus_deduction
from models.household import Household
from models.ytd_income import YTDSnapshot


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestObbbaSeniorDeduction:
    def test_phaseout_threshold_is_nominal_not_cpi_indexed(self):
        """A2 (tax-core-6/scenario-math-2): OBBBA phaseout start is a fixed statutory
        amount ($150k MFJ), NOT CPI-indexed. At MAGI 152k in 2027 the phaseout must
        fire on the $2k excess. Pre-fix the threshold inflated to ~153.75k and no
        phaseout occurred (full $12k)."""
        # both 65+, MAGI 152k, 2027: excess 2000 * 6 percent = 120 aggregate reduction
        # (audit-0722b OBBBA-1: reduction applies once to the aggregate bonus, not per person)
        ded = senior_bonus_deduction(66, 66, 152_000, year=2027, cpi=0.025)
        assert ded == approx(12_000 - 120)  # 11_880, not 12_000
        assert ded < 12_000

    def test_sunsets_before_2025(self):
        """G1 (audit-cleanup): the deduction is effective for tax years 2025-2028
        (Pub. L. 119-21 §70103); year 2024 must return 0, year 2025 must be active."""
        assert senior_bonus_deduction(66, 66, 100_000, year=2024) == 0.0
        # sanity: 2025 is the first active year
        assert senior_bonus_deduction(66, 66, 100_000, year=2025) > 0

    def test_estimate_applies_obbba_to_the_ltcg_base(self):
        """A1 (tax-core-4): estimate_ytd_federal_tax must fold the OBBBA bonus into the
        std-deduction used for the LTCG stack-walk base, lowering LTCG tax for 65+.

        Toggle OBBBA on/off via base_year (2026 active vs 2029 sunset) with cpi=0 so
        every other indexed value is identical -> the LTCG-tax delta is purely the bonus.
        """
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=130_000, ltcg_ytd=50_000)
        hh_obbba = Household(your_age=66, spouse_age=66, base_year=2026, cpi_assumption=0.0)
        hh_sunset = Household(your_age=66, spouse_age=66, base_year=2029, cpi_assumption=0.0)
        est_obbba = estimate_ytd_federal_tax(ytd, hh_obbba)
        est_sunset = estimate_ytd_federal_tax(ytd, hh_sunset)
        assert est_sunset.ltcg_tax > 0  # a real 15-percent portion exists to be reduced
        # OBBBA bonus pulls more LTCG into the 0-percent band -> strictly less LTCG tax.
        assert est_obbba.ltcg_tax < est_sunset.ltcg_tax
