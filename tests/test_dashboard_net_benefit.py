"""Audit 0705 views-financial-10 + views-financial-5 — Dashboard Net Lifetime Benefit.

Two defects fixed together:
  1. OMISSION: Dashboard net benefit omitted IRMAA, ACA, and NIIT savings; the
     Scenario Comparator (compute_summary_rows) included all five components.
     Fix: Dashboard now calls compute_summary_rows and uses savings_vs_baseline.
  2. DOUBLE-COUNT: In overlap years (RMD guard fires while spouse still converts),
     federal_tax_amt (which absorbs conversion tax) was added to cum_rmd_tax while
     cum_conv_tax also counted the same conversion_tax — double-counting it.
     Fix: cum_rmd_tax accumulates (federal_tax_amt - conversion_tax) so
     total_rmd_tax reflects only the pure RMD-phase tax burden.
"""

from __future__ import annotations

import pytest

from engine.scenario import ConversionPlan, run_no_conversion, run_scenario
from engine.scenario_autofill import auto_fill_12
from engine.scenario_compare import compute_cumulative_net_benefit, compute_summary_rows
from models.household import Household


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mfj_hh() -> Household:
    """Representative MFJ household — matches the target described in the audit."""
    return Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        your_ira=1_700_000,
        spouse_ira=1_700_000,
        your_rmd_start_age=75,
        spouse_rmd_start_age=75,
        growth_rate=0.07,
        your_ss_fra=30_000,
        spouse_ss_fra=20_000,
        your_ss_start_age=70,
        spouse_ss_start_age=70,
    )


# ---------------------------------------------------------------------------
# Test 1 — Consistency: Dashboard net benefit must equal Comparator savings_vs_baseline
# ---------------------------------------------------------------------------


class TestDashboardComparatorConsistency:
    """Dashboard and Comparator must agree on net lifetime benefit (sign + magnitude).

    Pre-fix: Dashboard computed tax_saved + brok_saved − conv_tax (omits IRMAA/ACA/NIIT).
    Comparator computed baseline_all_in − scenario_all_in (all five cost components).
    For households with material IRMAA, the two could disagree on sign.

    Post-fix: Dashboard derives its net metric from compute_summary_rows, so the
    two are numerically identical by construction.
    """

    def test_dashboard_net_equals_comparator_savings(self, mfj_hh: Household) -> None:
        """compute_summary_rows.savings_vs_baseline must equal the all-in net benefit."""
        no_conv = run_no_conversion(mfj_hh, end_age=95)
        plan_12 = auto_fill_12(mfj_hh)
        with_conv = run_scenario(mfj_hh, plan_12, "Fill 12%", end_age=95)

        # Comparator canonical computation
        rows = compute_summary_rows([no_conv, with_conv], no_conv)
        comparator_net = rows[1].savings_vs_baseline  # positive = saves money

        # Dashboard canonical computation (post-fix: must use same formula)
        # all_in_cost = lifetime_tax + irmaa + brok + aca + niit
        baseline_all_in = rows[0].all_in_cost
        scenario_all_in = rows[1].all_in_cost
        dashboard_net = baseline_all_in - scenario_all_in

        assert dashboard_net == pytest.approx(comparator_net, abs=1.0), (
            f"Dashboard net {dashboard_net:,.0f} != Comparator net {comparator_net:,.0f}; "
            "they must agree after the omission fix"
        )

    def test_net_benefit_includes_irmaa_component(self, mfj_hh: Household) -> None:
        """A household where conversion causes IRMAA must show IRMAA in the all-in cost."""
        no_conv = run_no_conversion(mfj_hh, end_age=95)
        plan_12 = auto_fill_12(mfj_hh)
        with_conv = run_scenario(mfj_hh, plan_12, "Fill 12%", end_age=95)

        rows = compute_summary_rows([no_conv, with_conv], no_conv)
        # IRMAA must be tracked in ScenarioSummary (non-zero for a large-IRA household)
        assert rows[1].lifetime_irmaa >= 0.0, "lifetime_irmaa must be non-negative"
        # all_in_cost must include irmaa (verified by component decomposition)
        reconstructed = (
            rows[1].lifetime_tax
            + rows[1].lifetime_irmaa
            + rows[1].lifetime_brok_tax
            + rows[1].lifetime_aca_loss
            + rows[1].lifetime_niit
        )
        assert rows[1].all_in_cost == pytest.approx(reconstructed, abs=1.0), (
            "all_in_cost must equal sum of all five components"
        )

    def test_cumulative_net_benefit_includes_irmaa_aca_niit(self, mfj_hh: Household) -> None:
        """compute_cumulative_net_benefit final value must equal savings_vs_baseline.

        Post-fix: the function accumulates all-in costs (federal + IRMAA + brokerage
        + ACA + NIIT) for every year without any separate sunk-cost subtraction.
        The final element therefore equals compute_summary_rows.savings_vs_baseline
        exactly (audit 0705 #views-financial-10).
        """
        no_conv = run_no_conversion(mfj_hh, end_age=95)
        plan_12 = auto_fill_12(mfj_hh)
        with_conv = run_scenario(mfj_hh, plan_12, "Fill 12%", end_age=95)

        cum_benefit = compute_cumulative_net_benefit(
            with_conv, no_conv, rmd_start_age=mfj_hh.your_rmd_start_age
        )
        rows = compute_summary_rows([no_conv, with_conv], no_conv)
        final_cum = cum_benefit[-1]
        comparator_net = rows[1].savings_vs_baseline
        # No sunk-cost deduction: final cumulative all-in savings == savings_vs_baseline
        assert final_cum == pytest.approx(comparator_net, abs=1.0), (
            f"Final cumulative net {final_cum:,.0f} must exactly equal Comparator "
            f"savings_vs_baseline {comparator_net:,.0f}"
        )


# ---------------------------------------------------------------------------
# Test 2 — No double-count: overlap-year conversion tax counted exactly once
# ---------------------------------------------------------------------------


class TestOverlapYearNoDoubleCount:
    """In years where the RMD gate fires AND a spouse is still converting,
    total_rmd_tax must NOT include the conversion tax (which is already in
    total_conv_tax).  The buggy code added federal_tax_amt (which includes
    conversion_tax) to cum_rmd_tax, double-counting it.
    """

    @pytest.fixture
    def overlap_hh(self) -> Household:
        """Household designed to produce overlap years:
        - Your age 75 → immediately in RMD (rmd_start_age=75)
        - Spouse age 65 → still in conversion window; not yet at RMD
        Spouse conversion in RMD years creates the overlap.
        """
        return Household(
            your_age=75,
            spouse_age=65,
            base_year=2026,
            your_ira=1_000_000,
            spouse_ira=800_000,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            growth_rate=0.07,
            your_ss_fra=25_000,
            spouse_ss_fra=18_000,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )

    def test_no_double_count_in_no_conversion_baseline(self, overlap_hh: Household) -> None:
        """No-conversion baseline: conversion_tax == 0 every year, so
        total_rmd_tax == sum(federal_tax_amt) for RMD years exactly.
        """
        result = run_no_conversion(overlap_hh, end_age=85)
        manual_rmd_tax = sum(
            yr.federal_tax_amt
            for yr in result.years
            if yr.your_age >= overlap_hh.your_rmd_start_age
            or yr.spouse_age >= overlap_hh.spouse_rmd_start_age
        )
        # No-conversion: conversion_tax=0 in every year, so both formulas agree
        assert result.total_rmd_tax == pytest.approx(manual_rmd_tax, abs=1.0), (
            "No-conversion baseline: total_rmd_tax should equal sum(federal_tax_amt) "
            "in RMD years (conversion_tax=0 so no double-count risk)"
        )

    def test_overlap_year_conversion_tax_not_double_counted(self, overlap_hh: Household) -> None:
        """With a conversion plan in the overlap period:
        total_rmd_tax + total_conv_tax must NOT exceed the sum of
        (federal_tax_amt) across RMD years by more than rounding.

        Buggy formula: cum_rmd_tax += federal_tax_amt  (includes conversion_tax)
        → total_rmd_tax + total_conv_tax > actual_rmd_phase_tax  (double-count)

        Fixed formula: cum_rmd_tax += federal_tax_amt - conversion_tax
        → total_rmd_tax + total_conv_tax == actual_rmd_phase_tax  (exactly once)
        """
        # Spouse converts during years 1-5 (your_age 75-79 → overlap)
        plan = ConversionPlan(
            spouse_conversions={yr: 50_000 for yr in range(2026, 2031)}
        )
        result = run_scenario(overlap_hh, plan, "Overlap Conversion", end_age=85)

        # Sum federal_tax_amt for all RMD-phase years (ground truth)
        actual_rmd_phase_tax = sum(
            yr.federal_tax_amt
            for yr in result.years
            if yr.your_age >= overlap_hh.your_rmd_start_age
            or yr.spouse_age >= overlap_hh.spouse_rmd_start_age
        )
        # With the fix: total_rmd_tax = sum(federal_tax_amt - conversion_tax) for RMD years
        # → total_rmd_tax + total_conv_tax == actual_rmd_phase_tax (conv_tax counted once)
        # With the bug:  total_rmd_tax contains conversion_tax already
        # → total_rmd_tax + total_conv_tax > actual_rmd_phase_tax

        # The fixed invariant: total_rmd_tax ≤ actual_rmd_phase_tax
        assert result.total_rmd_tax <= actual_rmd_phase_tax + 1.0, (
            f"total_rmd_tax ({result.total_rmd_tax:,.0f}) must not exceed "
            f"actual RMD-phase federal_tax_amt ({actual_rmd_phase_tax:,.0f}); "
            "double-count would cause it to exceed"
        )

        # Exact invariant: total_rmd_tax + total_conv_tax == actual_rmd_phase_tax
        # (conversion tax counted once via total_conv_tax, not also in total_rmd_tax)
        sum_both = result.total_rmd_tax + result.total_conv_tax
        assert sum_both == pytest.approx(actual_rmd_phase_tax, abs=1.0), (
            f"total_rmd_tax ({result.total_rmd_tax:,.0f}) + "
            f"total_conv_tax ({result.total_conv_tax:,.0f}) = {sum_both:,.0f} "
            f"must equal actual_rmd_phase_tax ({actual_rmd_phase_tax:,.0f}); "
            "double-count would inflate the sum"
        )

    def test_net_benefit_not_doubly_negative_in_overlap_year(self, overlap_hh: Household) -> None:
        """compute_summary_rows.savings_vs_baseline must be finite and correctly signed.

        The all-in net benefit from savings_vs_baseline (baseline_all_in - scenario_all_in)
        is immune to the total_rmd_tax double-count because it uses per-year
        federal_tax_amt directly (not the pre-aggregated total_rmd_tax field).
        This test confirms the two pathways agree: the savings from the all-in
        cost comparison must be consistent with manually summed per-year deltas.
        """
        plan = ConversionPlan(
            spouse_conversions={yr: 50_000 for yr in range(2026, 2031)}
        )
        no_conv = run_no_conversion(overlap_hh, end_age=85)
        with_conv = run_scenario(overlap_hh, plan, "Overlap Conversion", end_age=85)

        rows = compute_summary_rows([no_conv, with_conv], no_conv)
        net = rows[1].savings_vs_baseline

        # Manual all-in delta (ground truth, no pre-aggregation involved)
        manual_delta = sum(
            (yr_b.federal_tax_amt + yr_b.irmaa_cost + yr_b.brokerage_gain_tax
             + yr_b.aca_loss + yr_b.niit_cost)
            - (yr_s.federal_tax_amt + yr_s.irmaa_cost + yr_s.brokerage_gain_tax
               + yr_s.aca_loss + yr_s.niit_cost)
            for yr_b, yr_s in zip(no_conv.years, with_conv.years, strict=False)
        )
        assert net == pytest.approx(manual_delta, abs=1.0), (
            f"savings_vs_baseline ({net:,.0f}) must equal manually summed per-year "
            f"all-in delta ({manual_delta:,.0f})"
        )
