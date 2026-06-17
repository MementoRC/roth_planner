"""Tests for engine.niit — Net Investment Income Tax."""

import pytest

from engine.niit import NIIT_THRESHOLD_MFJ, niit
from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from models.household import GrowthProfile, Household


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

        This test discriminates the pre-fix vs post-fix behaviour in the regime where
        ordinary MAGI (RMDs + conversion, no realized gains) is BELOW the $250K MFJ
        threshold, but full MAGI including realized gains is ABOVE it:

          - Pre-fix code passed ordinary MAGI to niit() → threshold not crossed → niit_cost=0
          - Post-fix code passes full §1411 MAGI (including realized gains) → niit_cost > 0

        This test FAILS (niit_cost==0) against pre-fix code and PASSES post-fix.

        Fixture design:
          - Age-75 household with IRAs sized so RMDs + conversion keep ordinary MAGI
            well below $250K (~178K).
          - brokerage_growth uses a high appreciation rate (0.60) separate from IRA
            growth (0.10), so brokerage turnover produces realized gains that push
            full §1411 MAGI clearly above $250K.
          - brok_turnover=1.0 so all brokerage appreciation is realized (no yield).
          - No ytd snapshot → yr.niit_magi == yr.magi (no muni-interest exclusion).
          - Year index 1 (base_year+1): brokerage has one year of accumulated excess
            before the tested year begins.
        """
        from dataclasses import replace

        # Age-75 household: RMD factor ~22.9 → each $2M IRA yields ~$87K RMD.
        # Combined RMDs ~$174K + $4K conversion → ordinary MAGI ~$178K (below $250K).
        # brokerage_growth rate=0.60 (separate from IRA growth_rate=0.10) ensures
        # realized_gains in year 2 push full niit_magi well above $250K.
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
            brokerage_growth=GrowthProfile(default_rate=0.60),
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
        )
        # Small conversion — keeps ordinary MAGI below $250K threshold
        plan = ConversionPlan(
            your_conversions={hh.base_year: 4_000.0, hh.base_year + 1: 4_000.0}
        )
        result = run_scenario(hh, plan, "niit_excess_bound", end_age=78)

        # Year 2 (index 1): brokerage accumulated from year-1 excess generates realized_gains
        yr = result.years[1]

        # realized_gains_magi mirrors scenario.py's formula:
        #   realized_gains_magi = brokerage * brok_appreciation_rate * brok_turnover
        #                       = yr.brokerage_growth * brok_turnover  (brok_turnover=1.0)
        realized_gains_magi = yr.brokerage_growth * hh.brok_turnover
        assert realized_gains_magi > 0, "fixture must produce non-zero realized gains in year 2"

        # No ytd → yr.niit_magi == yr.magi (confirmed by test_niit_magi_equals_magi_without_ytd).
        # ordinary_magi = full magi minus the realized gains component.
        ordinary_magi = yr.niit_magi - realized_gains_magi

        # Guard: verify we are in the discriminating regime.
        # ordinary_magi must be BELOW the threshold (pre-fix code returns niit_cost=0 here).
        assert ordinary_magi <= NIIT_THRESHOLD_MFJ, (
            f"fixture ordinary_magi={ordinary_magi:,.2f} must be <= "
            f"NIIT_THRESHOLD_MFJ={NIIT_THRESHOLD_MFJ:,.0f} "
            f"(regime: pre-fix code sees below-threshold MAGI → niit_cost=0)"
        )
        # Full §1411 MAGI (including realized gains) must be ABOVE the threshold.
        assert yr.niit_magi > NIIT_THRESHOLD_MFJ, (
            f"fixture yr.niit_magi={yr.niit_magi:,.2f} must exceed "
            f"NIIT_THRESHOLD_MFJ={NIIT_THRESHOLD_MFJ:,.0f} "
            f"(post-fix code must see above-threshold MAGI → niit_cost > 0)"
        )

        # PRIMARY assertion: post-fix niit_cost must be positive.
        # Pre-fix code would have called niit(ordinary_magi, nii) with ordinary_magi <= 250K
        # → returned 0. Post-fix calls niit(yr.niit_magi, nii) with niit_magi > 250K → > 0.
        assert yr.niit_cost > 0, (
            f"niit_cost={yr.niit_cost} must be > 0; "
            f"ordinary_magi={ordinary_magi:,.2f}, niit_magi={yr.niit_magi:,.2f}"
        )

        # Sanity envelope: NIIT cannot exceed 3.8% of the threshold excess.
        assert yr.niit_cost <= 0.038 * (yr.niit_magi - NIIT_THRESHOLD_MFJ) + 1e-6, (
            f"niit_cost={yr.niit_cost:,.2f} exceeds 3.8% × "
            f"(niit_magi − threshold) = {0.038 * (yr.niit_magi - NIIT_THRESHOLD_MFJ):,.2f}"
        )
