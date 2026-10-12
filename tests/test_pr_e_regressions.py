"""Regression tests for deep-review 2026-06-18 PR-E (RMD start-age hardcodes)."""

import pytest

from engine.scenario import ConversionPlan, run_scenario
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestRmdStartAgeConversionCutoff:
    def test_conversion_cutoff_follows_rmd_start_age(self):
        """rmd-1: conversions must be blocked once RMDs begin, per rmd_start_age.

        The old code hardcoded a cutoff of age > 74, so a pre-1960 planner
        (RMDs at 73) could still convert at 73-74 while already taking RMDs.
        """
        target_age = 73
        hh_late = Household(your_rmd_start_age=75)  # SECURE 2.0 default cohort
        hh_early = Household(your_rmd_start_age=73)  # pre-1960 cohort

        year = 2026 + (target_age - hh_late.your_age)
        plan = ConversionPlan(your_conversions={year: 50_000})

        yr_late = next(
            y for y in run_scenario(hh_late, plan, end_age=95).years if y.your_age == target_age
        )
        yr_early = next(
            y for y in run_scenario(hh_early, plan, end_age=95).years if y.your_age == target_age
        )

        # start age 75: a conversion at 73 is still before RMDs -> allowed.
        assert yr_late.your_conversion == approx(50_000)
        # start age 73: RMDs have begun at 73 -> conversion blocked.
        assert yr_early.your_conversion == 0
