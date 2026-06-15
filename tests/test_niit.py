"""Tests for engine.niit — Net Investment Income Tax."""

import pytest

from engine.niit import niit
from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestNIIT:
    def test_below_threshold(self):
        assert niit(200_000, 50_000) == 0

    def test_above_threshold(self):
        # MAGI $300K, NII $50K → excess = $50K, min(50K, 50K) = $50K × 3.8%
        assert niit(300_000, 50_000) == approx(50_000 * 0.038)

    def test_nii_less_than_excess(self):
        # MAGI $400K, NII $20K → excess = $150K, min(20K, 150K) = $20K × 3.8%
        assert niit(400_000, 20_000) == approx(20_000 * 0.038)

    def test_excess_less_than_nii(self):
        # MAGI $260K, NII $50K → excess = $10K, min(50K, 10K) = $10K × 3.8%
        assert niit(260_000, 50_000) == approx(10_000 * 0.038)

    def test_zero_investment_income(self):
        assert niit(500_000, 0) == 0

    def test_niit_magi_ytd_excludes_tax_exempt_interest(self):
        """niit_magi_ytd must differ from magi_ytd by exactly tax_exempt_interest_ytd."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=200_000, tax_exempt_interest_ytd=15_000)
        assert ytd.magi_ytd - ytd.niit_magi_ytd == approx(15_000)

    def test_niit_uses_niit_magi_not_magi_ytd(self):
        """Muni interest must NOT push NIIT-MAGI over threshold when ordinary income is below.

        Scenario: wages=$240K, muni=$15K → magi_ytd=$255K (above $250K threshold),
        niit_magi_ytd=$240K (below). Engine must use niit_magi_ytd → zero NIIT.
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        hh = Household(your_age=62, spouse_age=60, your_ira=500_000, spouse_ira=500_000)
        ytd = YTDSnapshot(
            wages_ytd=240_000,
            tax_exempt_interest_ytd=15_000,  # pushes magi_ytd to $255K but NOT niit_magi_ytd
        )
        # niit_magi_ytd = $240K < $250K threshold → NIIT must be zero
        assert ytd.niit_magi_ytd == approx(240_000)
        result = estimate_ytd_federal_tax(ytd, hh)
        assert result.niit == 0.0

    def test_niit_magi_yearresult_excludes_tax_exempt_interest(self):
        """yr.niit_magi must differ from yr.magi by exactly tax_exempt_interest_ytd in base year.

        Mirrors PR #128's niit_magi_ytd field contract on YTDSnapshot. IRC §1411(d)(3)
        defines NIIT-MAGI strictly as AGI + foreign earned income/housing exclusions —
        no muni-interest add-back. yr.magi (IRMAA variant) adds muni interest back via
        ytd_year.magi_ytd, so yr.niit_magi must subtract it.
        """
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=200_000, tax_exempt_interest_ytd=15_000)
        plan = ConversionPlan(your_conversions={2026: 0})
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        assert yr2026.magi - yr2026.niit_magi == approx(15_000)

    def test_niit_magi_equals_magi_without_ytd(self):
        """yr.niit_magi must equal yr.magi when no YTD snapshot is provided.

        Forecast years have no muni-interest source (Household has no tax_exempt_interest
        forecast field), so niit_magi == magi outside the base-year YTD path.
        """
        hh = Household()
        plan = ConversionPlan(your_conversions={2026: 50_000})
        result = run_scenario(hh, plan, "test", end_age=65)
        yr2026 = result.years[0]

        assert yr2026.niit_magi == approx(yr2026.magi)
