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
        rmd_year = hh.base_year + (hh.your_rmd_start_age - hh.your_age)
        pre_rmd_years = range(hh.base_year, rmd_year)
        # With the hold-to-expiry exercise-schedule default (audit-0713/PR #373),
        # option income no longer competes for 2026-2028 bracket room, so the
        # $1M IRA autofill-converts faster and can be fully depleted before the
        # RMD-start year — a correct outcome once the balance hits zero, not a
        # regression. Rather than hardcoding every pre-RMD year > 0 (which broke
        # the moment depletion timing shifted), assert the real intent: the
        # autofill must actually be converting during the pre-RMD window (at
        # least the earliest years, before any depletion is plausible), and a
        # zero only appears once the balance is exhausted.
        conversions = [plan.your_conversions.get(yr, 0.0) for yr in pre_rmd_years]
        assert any(c > 0.0 for c in conversions), (
            f"Expected at least one conversion before RMD start age "
            f"{hh.your_rmd_start_age} (years {list(pre_rmd_years)}), got none"
        )
        assert plan.your_conversions.get(hh.base_year, 0.0) > 0.0, (
            f"Expected a conversion in the first pre-RMD year {hh.base_year} "
            "(the IRA cannot be exhausted before any conversion has occurred)"
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
    """C3 — compute_cumulative_net_benefit behaviour after audit-0705 fix.

    Post-fix: the function accumulates all-in cost savings (federal + IRMAA +
    brokerage + ACA + NIIT) for every year without gating on rmd_start_age.
    The rmd_start_age parameter is retained for API compatibility but no longer
    changes which years contribute (audit 0705 #views-financial-10).
    """

    def test_rmd_start_age_param_accepted_but_does_not_gate_years(self) -> None:
        """rmd_start_age is accepted as keyword-only but no longer gates years.

        Both calls must return identical arrays because the all-in per-year delta
        does not depend on rmd_start_age (conversion-year higher taxes already
        appear as negative deltas; RMD-year savings appear as positive deltas).
        """
        hh = _hh_rmd73()
        plan = auto_fill_12(hh)
        baseline = run_no_conversion(hh, end_age=95)
        scenario = run_scenario(hh, plan, "Fill 12%", end_age=95)

        benefit_73 = compute_cumulative_net_benefit(scenario, baseline, rmd_start_age=73)
        benefit_75 = compute_cumulative_net_benefit(scenario, baseline, rmd_start_age=75)
        # Parameter no longer gates — both calls produce the same result
        assert benefit_73 == pytest.approx(benefit_75, abs=1.0), (
            "rmd_start_age no longer gates years; both calls must return identical arrays"
        )

    def test_final_value_equals_comparator_savings_vs_baseline(self) -> None:
        """The final element must equal compute_summary_rows savings_vs_baseline."""
        from engine.scenario_compare import compute_summary_rows  # noqa: PLC0415

        hh = _hh_rmd73()
        plan = auto_fill_12(hh)
        baseline = run_no_conversion(hh, end_age=95)
        scenario = run_scenario(hh, plan, "Fill 12%", end_age=95)

        cum_benefit = compute_cumulative_net_benefit(scenario, baseline, rmd_start_age=73)
        rows = compute_summary_rows([baseline, scenario], baseline)
        assert cum_benefit[-1] == pytest.approx(rows[1].savings_vs_baseline, abs=1.0), (
            "Final cumulative net benefit must equal Comparator savings_vs_baseline"
        )

    def test_rmd_start_age_is_required_kwarg(self) -> None:
        """rmd_start_age is a required keyword arg (no default)."""
        hh = _hh_rmd73()
        plan = auto_fill_12(hh)
        baseline = run_no_conversion(hh, end_age=95)
        scenario = run_scenario(hh, plan, "Fill 12%", end_age=95)
        # Must be passed as keyword; omitting it should raise TypeError
        with pytest.raises(TypeError, match="rmd_start_age"):
            compute_cumulative_net_benefit(scenario, baseline)  # type: ignore[call-arg]
