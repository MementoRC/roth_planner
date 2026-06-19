"""Regression tests for deep-review 2026-06-18 PR-G6 (Sweet Spot ACA benchmark + YTD LTCG)."""

from dataclasses import replace

import pytest

from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year
from models.household import Household
from models.ytd_income import YTDSnapshot


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestSweetSpotYtdMagi:
    def test_base_magi_includes_ytd(self):
        """niit-5 (A11): base-year realized YTD income must enter Sweet Spot base MAGI."""
        hh = Household()
        year = hh.base_year
        base_no = base_income_for_year(hh, year)
        base_yt = base_income_for_year(hh, year, ytd=YTDSnapshot(tax_year=year, ltcg_ytd=60_000))
        assert base_yt.base_magi - base_no.base_magi == approx(60_000.0)

    def test_conversion_magi_consistent_with_base_at_zero_conv(self):
        """niit-5 (A11): the with-conversion MAGI must also include YTD, so at conv=0 it
        equals the no-conversion base MAGI (no spurious IRMAA delta). Before the full fix,
        only base.base_magi carried YTD and the recomputed magi did not."""
        hh = Household(your_age=66, spouse_age=66)  # on Medicare -> IRMAA active
        year = hh.base_year
        ytd = YTDSnapshot(tax_year=year, ltcg_ytd=300_000)  # large enough to cross an IRMAA tier
        base = base_income_for_year(hh, year, ytd=ytd)
        r0 = all_in_at_conversion(hh, base, 0, 0.0)
        assert r0.irmaa_delta == approx(0.0)


class TestSweetSpotAcaBenchmark:
    def test_user_benchmark_wired_into_aca_loss(self):
        """A6: hh.aca_benchmark_premium_annual must drive the Sweet Spot ACA subsidy loss.

        Base MAGI ~60k (below 400% FPL, subsidy active and benchmark-dependent); a 40k
        conversion pushes MAGI to ~100k (above the cliff, subsidy 0). The lost subsidy
        therefore equals the base subsidy = benchmark - contribution, so a higher
        benchmark yields a strictly larger loss. Before the fix the call used the
        hardcoded default benchmark for both households.
        """
        hh_lo = Household(
            your_age=61,
            spouse_age=61,
            your_aca_enrolled=True,
            your_ss_fra=0,
            spouse_ss_fra=0,
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
            aca_benchmark_premium_annual=21_600.0,
        )
        hh_hi = replace(hh_lo, aca_benchmark_premium_annual=45_000.0)
        year = hh_lo.base_year
        ytd = YTDSnapshot(tax_year=year, ltcg_ytd=60_000)  # base MAGI ~60k (below cliff)
        base_lo = base_income_for_year(hh_lo, year, ytd=ytd)
        base_hi = base_income_for_year(hh_hi, year, ytd=ytd)
        r_lo = all_in_at_conversion(hh_lo, base_lo, 40_000, 0.0)
        r_hi = all_in_at_conversion(hh_hi, base_hi, 40_000, 0.0)
        assert r_lo.aca_loss > 0
        assert r_hi.aca_loss > r_lo.aca_loss
