"""Regression tests for deep-review 2026-06-18 PR-F (single-filer correctness)."""

import pytest

from engine.aca import FPL_1
from engine.aca_irmaa_compute import compute_cost_curves
from engine.irmaa import irmaa_tier
from engine.tax import estimate_ytd_federal_tax
from models.household import Household
from models.ytd_income import YTDSnapshot


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestEstimateYtdSingleFiler:
    def test_ordinary_brackets_dispatch_to_single(self):
        """tax-core-1: Single brackets are narrower -> more tax than MFJ on same income."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=150_000)
        mfj = estimate_ytd_federal_tax(ytd, Household(filing_status="MFJ"))
        single = estimate_ytd_federal_tax(ytd, Household(filing_status="Single"))
        assert single.ordinary_tax > mfj.ordinary_tax
        assert single.marginal_bracket_pct > mfj.marginal_bracket_pct

    def test_ltcg_thresholds_dispatch_to_single(self):
        """tax-core-2: Single 0%-LTCG band is lower, so Single owes LTCG tax where MFJ owes none."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=20_000, ltcg_ytd=80_000)
        mfj = estimate_ytd_federal_tax(ytd, Household(filing_status="MFJ"))
        single = estimate_ytd_federal_tax(ytd, Household(filing_status="Single"))
        assert mfj.ltcg_tax == approx(0.0)
        assert single.ltcg_tax > 1_000

    def test_niit_threshold_dispatch_to_single(self):
        """tax-core-3: MAGI 220k fires NIIT for Single (200k) but not MFJ (250k)."""
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=180_000, ltcg_ytd=40_000)
        mfj = estimate_ytd_federal_tax(ytd, Household(filing_status="MFJ"))
        single = estimate_ytd_federal_tax(ytd, Household(filing_status="Single"))
        assert mfj.niit == approx(0.0)
        assert single.niit > 0


class TestSingleFilerAcaIrmaa:
    def test_irmaa_tier_uses_single_thresholds(self):
        """irmaa-1: at MAGI 150k Single is already in a surcharge tier, MFJ is not."""
        assert irmaa_tier(150_000, filing_status="Single") > irmaa_tier(
            150_000, filing_status="MFJ"
        )
        # default preserves MFJ behavior
        assert irmaa_tier(150_000) == irmaa_tier(150_000, filing_status="MFJ")

    def test_fpl_single_is_2025_figure(self):
        """aca-1: single-person FPL must be the 2025 figure (15,650), not the stale 2024 15,060."""
        assert FPL_1 == 15_650

    def test_filing_status_flows_into_aca_curves(self):
        """aca-3: filing_status must reach the ACA calls in compute_cost_curves.

        At MAGI 70k a single filer is above the 400% FPL cliff (4 x 15,650 = 62,600)
        so gets no subsidy, while MFJ (cliff 4 x 21,150 = 84,600) still does. Before
        the fix the single call defaulted to MFJ and both got a subsidy.
        """
        magi_points = [70_000.0]
        hh_single = Household(filing_status="Single", your_age=61, your_aca_enrolled=True)
        hh_mfj = Household(filing_status="MFJ", your_age=61, spouse_age=61, your_aca_enrolled=True)
        single = compute_cost_curves(magi_points, 70_000.0, 0.0, hh_single, year=2026, cpi=0.0)
        mfj = compute_cost_curves(magi_points, 70_000.0, 0.0, hh_mfj, year=2026, cpi=0.0)
        assert mfj.aca_subsidy_vals[0] > 0
        assert single.aca_subsidy_vals[0] == 0
