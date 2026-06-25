"""Regression tests for cluster C — residual hardcoded RMD start age 74/75.

C1: scenario_autofill conversion cap used literal 74 instead of hh.your_rmd_start_age - 1
C2: dashboard net-benefit accumulation hardcoded >= 75
C3: compute_cumulative_net_benefit defaulted rmd_start_age=75
C4: dashboard milestone table had hardcoded 75 "RMDs begin"
C5: run_scenario docstring text (doc-only; no runtime assertion needed)
"""

from __future__ import annotations

import pytest

from engine.scenario import run_no_conversion, run_scenario
from engine.scenario_autofill import auto_fill_12
from engine.scenario_compare import compute_cumulative_net_benefit
from models.household import Household


def _hh_rmd73() -> Household:
    """Household whose RMD start age is 73 (pre-1960 cohort, SECURE 1.0).

    Using rmd_start_age != 75 exercises all sites that previously hardcoded 75.
    Using rmd_start_age - 1 = 72 (< 74) exercises the C1 autofill cap that
    previously hardcoded ``ya <= 74``.
    """
    return Household(
        your_age=61,
        spouse_age=55,
        your_rmd_start_age=73,
        spouse_rmd_start_age=73,
        your_ira=1_000_000.0,
        spouse_ira=500_000.0,
    )


class TestC1AutofillConversionCap:
    """C1 — autofill must stop conversions the year the owner reaches rmd_start_age."""

    def test_no_conversion_at_rmd_start_age(self) -> None:
        hh = _hh_rmd73()
        plan = auto_fill_12(hh)
        rmd_year = hh.base_year + (hh.your_rmd_start_age - hh.your_age)
        # No conversion should be allocated for you in the RMD-start year
        assert plan.your_conversions.get(rmd_year, 0.0) == pytest.approx(0.0, abs=1.0), (
            f"Expected no conversion at age {hh.your_rmd_start_age} (year {rmd_year}), "
            f"got {plan.your_conversions.get(rmd_year)}"
        )

    def test_conversions_present_before_rmd_start_age(self) -> None:
        hh = _hh_rmd73()
        plan = auto_fill_12(hh)
        # The year before RMD should have a conversion
        pre_rmd_year = hh.base_year + (hh.your_rmd_start_age - hh.your_age) - 1
        assert plan.your_conversions.get(pre_rmd_year, 0.0) > 0.0, (
            f"Expected conversion at age {hh.your_rmd_start_age - 1} (year {pre_rmd_year})"
        )

    def test_old_hardcode_74_would_have_allowed_extra_year(self) -> None:
        """Demonstrate the bug: with rmd_start_age=73, old code (ya <= 74) would
        have allocated conversions at ages 73 and 74, one year past RMD start."""
        hh = _hh_rmd73()
        plan = auto_fill_12(hh)
        # Age 73 is rmd_start_age — must NOT convert
        rmd_start_year = hh.base_year + (73 - hh.your_age)
        assert plan.your_conversions.get(rmd_start_year, 0.0) == pytest.approx(0.0, abs=1.0)
        # Age 74 — also past RMD start — must NOT convert
        age74_year = hh.base_year + (74 - hh.your_age)
        assert plan.your_conversions.get(age74_year, 0.0) == pytest.approx(0.0, abs=1.0)


class TestC3CumulativeNetBenefit:
    """C3 — compute_cumulative_net_benefit must use the supplied rmd_start_age."""

    def test_earlier_rmd_age_accumulates_savings_sooner(self) -> None:
        hh = _hh_rmd73()
        plan = auto_fill_12(hh)
        baseline = run_no_conversion(hh, end_age=95)
        scenario = run_scenario(hh, plan, "Fill 12%", end_age=95)

        benefit_73 = compute_cumulative_net_benefit(scenario, baseline, rmd_start_age=73)
        benefit_75 = compute_cumulative_net_benefit(scenario, baseline, rmd_start_age=75)
        # With rmd_start_age=73 savings accumulate 2 years earlier — benefit_73
        # should diverge from benefit_75 at the ages between 73 and 74 inclusive.
        ages = [yr.your_age for yr in baseline.years]
        idx_73 = next(i for i, a in enumerate(ages) if a == 73)
        idx_74 = next(i for i, a in enumerate(ages) if a == 74)
        # At age 73 the 73-threshold has started counting savings; 75-threshold has not
        assert benefit_73[idx_73] != pytest.approx(benefit_75[idx_73], abs=1.0), (
            "rmd_start_age=73 and rmd_start_age=75 should diverge at age 73"
        )
        # At age 74 the same: rmd_start_age=73 has 2 years of RMD savings accumulated
        assert benefit_73[idx_74] != pytest.approx(benefit_75[idx_74], abs=1.0)

    def test_rmd_start_age_is_required_kwarg(self) -> None:
        """rmd_start_age is now a required keyword arg (no default=75)."""
        hh = _hh_rmd73()
        plan = auto_fill_12(hh)
        baseline = run_no_conversion(hh, end_age=95)
        scenario = run_scenario(hh, plan, "Fill 12%", end_age=95)
        # Must be passed as keyword; omitting it should raise TypeError
        with pytest.raises(TypeError, match="rmd_start_age"):
            compute_cumulative_net_benefit(scenario, baseline)  # type: ignore[call-arg]
