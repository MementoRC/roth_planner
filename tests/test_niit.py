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
        """yr.niit_magi must exclude tax_exempt_interest_ytd relative to yr.magi.

        Mirrors PR #128's niit_magi_ytd field contract on YTDSnapshot. IRC §1411(d)(3)
        defines NIIT-MAGI strictly as AGI + foreign earned income/housing exclusions —
        no muni-interest add-back. yr.magi (IRMAA variant) adds muni interest back via
        ytd_year.magi_ytd, so yr.niit_magi must subtract it. Both yr.magi and yr.niit_magi
        include realized_gains (added to each in the same operation), so the difference
        between them is exactly tax_exempt_interest_ytd.
        """
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=200_000, tax_exempt_interest_ytd=15_000)
        plan = ConversionPlan(your_conversions={2026: 0})
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        # yr.magi includes realized_gains; yr.niit_magi also includes realized_gains
        # but excludes tax_exempt_interest — so their difference is exactly tax_exempt_interest.
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

    def test_niit_cost_includes_realized_gains_in_magi_excess_bound(self):
        """niit() must receive MAGI including realized_gains (IRC §1411 — no exclusion).

        Exercises the excess-bound regime where MAGI-threshold is the binding term:
          - Ordinary MAGI (RMDs + conversion, no realized_gains) is just above $250K MFJ
          - Realized gains (from brokerage carry) are large enough that NII >> ordinary excess
          - Before fix: niit() saw MAGI without realized_gains → small excess → under-count
          - After fix: niit() sees MAGI including realized_gains → full §1411 MAGI → correct

        The test verifies the correct niit_cost using yr.niit_magi (which includes
        realized_gains post-fix) and brokerage_growth * brok_turnover as NII proxy.
        """
        from dataclasses import replace

        # Age-75 household: RMD factor ~22.9 → each $2M IRA yields ~$87K RMD.
        # Combined RMDs ~$174K + $80K conversion → ordinary MAGI ~$254K (just above $250K).
        # Low living expenses → large brokerage carry → material realized_gains in year 2.
        # brok_turnover=1.0 so all appreciation becomes realized gains (no yield).
        hh = replace(
            Household(grants=[]),
            your_age=75,
            spouse_age=75,
            your_ira=2_000_000.0,
            spouse_ira=2_000_000.0,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            living_expenses=20_000.0,
            brok_turnover=1.0,
            growth_rate=0.10,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
        )
        # Small conversion to nudge ordinary MAGI above $250K
        plan = ConversionPlan(your_conversions={hh.base_year: 80_000.0, hh.base_year + 1: 80_000.0})
        result = run_scenario(hh, plan, "niit_excess_bound", end_age=78)

        # Year 2 (index 1): brokerage carry from year-1 excess generates realized_gains
        yr = result.years[1]
        realized_gains = yr.brokerage_growth * hh.brok_turnover
        assert realized_gains > 0, "fixture must produce non-zero realized gains in year 2"

        # Verify the excess-bound regime:
        # 1. Ordinary MAGI (before adding realized_gains) is above $250K threshold
        threshold = 250_000.0
        ordinary_magi = yr.magi - realized_gains
        ordinary_excess = max(0.0, ordinary_magi - threshold)
        assert ordinary_excess > 0, (
            f"fixture must have ordinary MAGI above ${threshold:,.0f}; "
            f"ordinary_magi={ordinary_magi:,.2f}"
        )
        # 2. Realized gains (NII proxy) are larger than the ordinary excess alone,
        #    so the bug (omitting realized_gains from MAGI) would produce a smaller niit_cost.
        assert realized_gains > ordinary_excess, (
            "fixture must be in excess-bound regime: realized_gains > ordinary MAGI excess"
        )

        # Correct NIIT: niit_magi includes realized_gains (post-fix); no ytd so
        # yr.niit_magi == yr.magi. NIIT = min(NII, niit_magi - threshold) × 3.8%.
        # With full MAGI, excess is large enough that NII is the binding term.
        full_excess = max(0.0, yr.niit_magi - threshold)
        assert full_excess >= realized_gains, (
            "after including realized_gains in MAGI, full_excess must be >= NII"
        )
        # NII = realized_gains (brok_turnover=1.0, no yield rate, no ytd)
        expected_niit = realized_gains * 0.038  # min(NII, full_excess) == NII
        assert yr.niit_cost == approx(expected_niit, tol=1.0), (
            f"niit_cost={yr.niit_cost:,.2f} must equal {expected_niit:,.2f} "
            f"(niit_magi={yr.niit_magi:,.2f}, realized_gains={realized_gains:,.2f})"
        )
