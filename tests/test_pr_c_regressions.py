"""Regression tests for deep-review 2026-06-18 PR-C (ACA benchmark + IRMAA headcount)."""

from dataclasses import replace

import pytest

from engine.aca_irmaa_compute import compute_cost_curves
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestCostCurveRegressions:
    def test_user_benchmark_wired_into_aca_curves(self):
        """aca-6: hh.aca_benchmark_premium_annual must drive the ACA curves.

        Before the fix the benchmark was ignored (hardcoded default), so the
        curves were identical regardless of the household setting.
        """
        hh_default = Household(
            your_age=61,
            spouse_age=61,
            your_aca_enrolled=True,
            aca_benchmark_premium_annual=21_600.0,
        )
        hh_higher = replace(hh_default, aca_benchmark_premium_annual=40_000.0)

        magi_points = [30_000.0, 50_000.0, 70_000.0]  # below 400% FPL -> subsidy region
        d = compute_cost_curves(magi_points, 30_000.0, 0.0, hh_default, year=2026, cpi=0.0)
        h = compute_cost_curves(magi_points, 30_000.0, 0.0, hh_higher, year=2026, cpi=0.0)

        assert max(d.aca_subsidy_vals) > 0  # sanity: subsidy region is actually reached
        # The benchmark is wired through -> curves differ when only it changes.
        assert h.aca_subsidy_vals != d.aca_subsidy_vals
        # A higher benchmark yields a larger subsidy at the same MAGI.
        assert h.aca_subsidy_vals[0] > d.aca_subsidy_vals[0]
        assert h.aca_net_cost_vals[0] <= d.aca_net_cost_vals[0]

    def test_irmaa_scales_with_medicare_headcount(self):
        """irmaa-4: IRMAA must reflect actual enrollees, not a hardcoded 2."""
        hh_one = Household(your_age=65, spouse_age=55)  # 1 on Medicare in 2026
        hh_two = Household(your_age=65, spouse_age=65)  # 2 on Medicare in 2026

        magi_points = [250_000.0, 300_000.0]  # above MFJ IRMAA tier 1
        one = compute_cost_curves(magi_points, 250_000.0, 0.0, hh_one, year=2026, cpi=0.0)
        two = compute_cost_curves(magi_points, 250_000.0, 0.0, hh_two, year=2026, cpi=0.0)

        assert one.base_irmaa > 0  # sanity: above the surcharge threshold
        assert two.base_irmaa > one.base_irmaa
        for i in range(len(magi_points)):
            assert two.irmaa_vals[i] > one.irmaa_vals[i]
