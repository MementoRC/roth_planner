"""Tests for engine.sweet_spot_compute and sweet-spot review regressions."""

import pytest

from config.defaults import DEFAULTS
from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestSweetSpot:
    """Test the sweet spot finder computation helpers."""

    @pytest.fixture(autouse=True)
    def _require_plotly(self):
        pytest.importorskip("plotly")
        pytest.importorskip("streamlit")

    def test_base_income_no_ss_before_70(self):
        from engine.sweet_spot_compute import base_income_for_year

        hh = Household()
        base = base_income_for_year(hh, 2026)
        assert base.ya == DEFAULTS["your_age"]
        assert base.combined_ss == 0  # SS starts at 70

    def test_base_income_has_options(self):
        from engine.sweet_spot_compute import base_income_for_year

        hh = Household()
        # Default grants now default-exercise at their own expiry_year
        # (hold-to-expiration), not base_year, so base_year has no option
        # income and the first grant's spread shows up at its expiry_year.
        base_2026 = base_income_for_year(hh, 2026)
        assert base_2026.opt == 0.0
        base_expiry = base_income_for_year(hh, hh.grants[0].expiry_year)
        assert base_expiry.opt == approx(
            hh.grants[0].spread(hh.projected_txn_price(hh.grants[0].expiry_year))
        )

    def test_all_in_zero_conversion(self):
        from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year

        hh = Household()
        base = base_income_for_year(hh, 2026)
        result = all_in_at_conversion(hh, base, 0, 0)
        assert result.all_in == 0
        assert result.conv_tax == 0

    def test_all_in_increases_with_conversion(self):
        from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year

        hh = Household()
        base = base_income_for_year(hh, 2026)
        r50k = all_in_at_conversion(hh, base, 50_000, 0)
        r100k = all_in_at_conversion(hh, base, 100_000, 0)
        assert r100k.all_in > r50k.all_in
        assert r50k.conv_tax > 0

    def test_irmaa_triggers_at_threshold(self):
        from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year

        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        base = base_income_for_year(hh, 2029)  # age 64, no options
        # Find conversion just below and above IRMAA tier 1
        below = max(218_000 - base.base_magi - 1_000, 0)
        above = 218_000 - base.base_magi + 1_000
        if below > 0 and above > 0:
            r_below = all_in_at_conversion(hh, base, below, 0)
            r_above = all_in_at_conversion(hh, base, above, 0)
            assert r_above.irmaa_delta > r_below.irmaa_delta


class TestReviewRegressions:
    """Regression tests for deep-review 2026-06-18 high-severity findings (PR-A)."""

    def test_ytd_interest_included_in_base_year_gross(self):
        """scenario-math-1: interest_ytd must flow into base-year ordinary income."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        plan = ConversionPlan()
        ytd_zero = YTDSnapshot(tax_year=2026, interest_ytd=0.0)
        ytd_int = YTDSnapshot(tax_year=2026, interest_ytd=50_000.0)
        y0 = run_scenario(hh, plan, "no-int", end_age=65, ytd=ytd_zero).years[0]
        y1 = run_scenario(hh, plan, "int", end_age=65, ytd=ytd_int).years[0]
        # interest_ytd was omitted before the fix -> this delta would have been 0.
        assert y1.combined_gross - y0.combined_gross == approx(50_000.0)
        assert y1.federal_tax_amt > y0.federal_tax_amt
        assert y1.ytd_interest == approx(50_000.0)

    def test_fra_age_affects_sweet_spot_ss(self):
        """compare-sweetspot-2: ss_benefit_at_age must honor hh.your_fra_age."""
        from engine.sweet_spot_compute import base_income_for_year

        hh67 = Household(your_fra_age=67, spouse_fra_age=67)
        hh66 = Household(your_fra_age=66, spouse_fra_age=66)
        year = 2026 + (70 - hh67.your_age)  # year your_age reaches default claim age 70
        b67 = base_income_for_year(hh67, year)
        b66 = base_income_for_year(hh66, year)
        # Same claim age (70), earlier FRA -> more delayed-retirement credits -> higher SS.
        # Hardcoded fra_age=67 before the fix would make these equal.
        assert b66.combined_ss > b67.combined_ss
