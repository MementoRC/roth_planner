"""Tests for engine.scenario core — no-conversion baseline, auto-fill strategies, Roth balance tracking."""

import pytest

from config.defaults import DEFAULTS
from engine.ira import (
    project_ira,
)
from engine.scenario import (
    ConversionPlan,
    add_bracket_fill_withdrawals,
    auto_fill_12,
    auto_fill_22,
    auto_fill_irmaa_safe,
    run_no_conversion,
    run_scenario,
)
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestScenarios:
    def test_no_conversion_ira_at_75(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        years_to_75 = 75 - DEFAULTS["your_age"]
        expected_ira = project_ira(DEFAULTS["your_ira"], 0.07, years_to_75)
        assert yr75.your_ira_begin == approx(expected_ira, tol=500)

    def test_no_conversion_rmd_at_75(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        years_to_75 = 75 - DEFAULTS["your_age"]
        expected_ira = project_ira(DEFAULTS["your_ira"], 0.07, years_to_75)
        assert yr75.your_rmd == approx(expected_ira / 24.6, tol=100)

    def test_no_conversion_ss_at_75(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        ss_at_70 = DEFAULTS["your_ss_fra"] * 1.24 * 12
        years_cola = 75 - 70
        ss75 = ss_at_70 * 1.025**years_cola
        assert yr75.your_ss == approx(ss75, tol=100)

    def test_no_conversion_spouse_ss_starts_at_70(self):
        hh = Household()
        result = run_no_conversion(hh, end_age=95)
        # Spouse reaches 70 when you are (your_age + (70 - spouse_age)) years old
        your_age_when_spouse_70 = DEFAULTS["your_age"] + (70 - DEFAULTS["spouse_age"])
        yr_before = next(yr for yr in result.years if yr.your_age == your_age_when_spouse_70 - 1)
        yr_start = next(yr for yr in result.years if yr.your_age == your_age_when_spouse_70)
        assert yr_before.spouse_ss == 0
        assert yr_start.spouse_ss > 0

    def test_12pct_fill_reduces_ira(self):
        hh = Household()
        plan = auto_fill_12(hh)
        result = run_scenario(hh, plan, "Fill 12%", end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        assert yr75.your_ira_begin < 4_000_000

    def test_22pct_fill_more_aggressive(self):
        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        plan_12 = auto_fill_12(hh)
        plan_22 = auto_fill_22(hh)
        total_12 = sum(plan_12.your_conversions.values()) + sum(plan_12.spouse_conversions.values())
        total_22 = sum(plan_22.your_conversions.values()) + sum(plan_22.spouse_conversions.values())
        assert total_22 > total_12

    def test_22pct_fill_reduces_ira_more(self):
        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        r12 = run_scenario(hh, auto_fill_12(hh), "12%", end_age=95)
        r22 = run_scenario(hh, auto_fill_22(hh), "22%", end_age=95)
        yr75_12 = next(yr for yr in r12.years if yr.your_age == 75)
        yr75_22 = next(yr for yr in r22.years if yr.your_age == 75)
        assert yr75_22.your_ira_begin < yr75_12.your_ira_begin

    def test_irmaa_safe_stays_under_threshold(self):
        hh = Household()
        plan = auto_fill_irmaa_safe(hh)
        result = run_scenario(hh, plan, "IRMAA-Safe", end_age=95)
        # During conversion years (pre-75), MAGI should stay under $218K
        for yr in result.years:
            if yr.your_age <= 74 and yr.your_conversion > 0:
                assert yr.magi <= 220_000  # small tolerance for SS taxation effects

    def test_bracket_fill_reduces_late_ira(self):
        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        base = auto_fill_12(hh)
        plan_bf = add_bracket_fill_withdrawals(hh, base, target_bracket=0.22)
        r12 = run_scenario(hh, base, "12%", end_age=95)
        r_bf = run_scenario(hh, plan_bf, "BF", end_age=95)
        yr90_12 = next(yr for yr in r12.years if yr.your_age == 90)
        yr90_bf = next(yr for yr in r_bf.years if yr.your_age == 90)
        assert yr90_bf.your_ira_begin < yr90_12.your_ira_begin

    def test_bracket_fill_has_extra_withdrawals(self):
        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        base = auto_fill_12(hh)
        plan_bf = add_bracket_fill_withdrawals(hh, base, target_bracket=0.22)
        assert len(plan_bf.extra_withdrawals) > 0
        # Extra withdrawals should only be post-RMD (age 75+)
        for year in plan_bf.extra_withdrawals:
            assert hh.your_age_in(year) >= 75


class TestAutoFillCharacterization:
    """Pin per-year output of the three auto_fill_* functions before refactor.

    These snapshots characterize today's behavior. If the upcoming
    _auto_fill_core extraction produces different per-year amounts,
    these tests will catch the drift even if end-to-end totals match.

    Captured against development @ ecbc49d (post-PR #41).
    """

    def _fixture_household(self) -> Household:
        """Mirror the fixture used by the existing auto_fill_* behavioral tests."""
        return Household()

    def test_auto_fill_12_year_by_year_snapshot(self):
        hh = self._fixture_household()
        plan = auto_fill_12(hh)
        # Per-year tuples: (year, your_conv, spouse_conv)
        rows = [
            (
                yr,
                round(plan.your_conversions.get(yr, 0.0)),
                round(plan.spouse_conversions.get(yr, 0.0)),
            )
            for yr in sorted(set(plan.your_conversions) | set(plan.spouse_conversions))
        ]
        # Captured against development @ ecbc49d (post-PR #41)
        expected: list[tuple[int, int, int]] = [
            (2026, 83000, 0),
            (2027, 113000, 0),
            (2028, 120500, 0),
            (2029, 133000, 0),
            (2030, 127902, 5098),
            (2031, 0, 133000),
            (2032, 0, 133000),
            (2033, 0, 133000),
            (2034, 0, 133000),
            (2035, 0, 133000),
            (2036, 0, 134650),
            (2037, 0, 24489),
            (2038, 0, 0),
            (2039, 0, 0),
            (2040, 0, 0),
            (2041, 0, 0),
            (2042, 0, 0),
            (2043, 0, 0),
            (2044, 0, 0),
            (2045, 0, 0),
            (2046, 0, 0),
            (2047, 0, 0),
        ]
        assert rows == expected

    def test_auto_fill_22_year_by_year_snapshot(self):
        hh = self._fixture_household()
        plan = auto_fill_22(hh)
        rows = [
            (
                yr,
                round(plan.your_conversions.get(yr, 0.0)),
                round(plan.spouse_conversions.get(yr, 0.0)),
            )
            for yr in sorted(set(plan.your_conversions) | set(plan.spouse_conversions))
        ]
        # Captured against development @ ecbc49d (post-PR #41)
        expected: list[tuple[int, int, int]] = [
            (2026, 193600, 0),
            (2027, 223600, 0),
            (2028, 111545, 119555),
            (2029, 0, 243600),
            (2030, 0, 243600),
            (2031, 0, 15267),
            (2032, 0, 0),
            (2033, 0, 0),
            (2034, 0, 0),
            (2035, 0, 0),
            (2036, 0, 0),
            (2037, 0, 0),
            (2038, 0, 0),
            (2039, 0, 0),
            (2040, 0, 0),
            (2041, 0, 0),
            (2042, 0, 0),
            (2043, 0, 0),
            (2044, 0, 0),
            (2045, 0, 0),
            (2046, 0, 0),
            (2047, 0, 0),
        ]
        assert rows == expected

    def test_auto_fill_irmaa_safe_year_by_year_snapshot(self):
        hh = self._fixture_household()
        plan = auto_fill_irmaa_safe(hh)
        rows = [
            (
                yr,
                round(plan.your_conversions.get(yr, 0.0)),
                round(plan.spouse_conversions.get(yr, 0.0)),
            )
            for yr in sorted(set(plan.your_conversions) | set(plan.spouse_conversions))
        ]
        # Captured against development @ ecbc49d (post-PR #41)
        # IRMAA-safe diverges from fill_12/fill_22 in base_magi computation:
        # uses full combined_ss (not taxable_ss) — these per-year rows capture that.
        expected: list[tuple[int, int, int]] = [
            (2026, 168000, 0),
            (2027, 198000, 0),
            (2028, 168247, 37253),
            (2029, 0, 218000),
            (2030, 0, 218000),
            (2031, 0, 172791),
            (2032, 0, 0),
            (2033, 0, 0),
            (2034, 0, 0),
            (2035, 0, 0),
            (2036, 0, 0),
            (2037, 0, 0),
            (2038, 0, 0),
            (2039, 0, 0),
            (2040, 0, 0),
            (2041, 0, 0),
            (2042, 0, 0),
            (2043, 0, 0),
            (2044, 0, 0),
            (2045, 0, 0),
            (2046, 0, 0),
            (2047, 0, 0),
        ]
        assert rows == expected


class TestRothBalanceTracking:
    """Regression tests for Roth balance tracking (grid-01 fix).

    These tests guard three correctness properties introduced when
    run_scenario began crediting conversions to Roth and growing them
    tax-free rather than letting converted principal disappear from
    the total-asset picture.

    Assertion taxonomy:
      BEHAVIORAL — asserts a specific computed value derived from the
                   engine formula; will fail if the formula changes.
      INVARIANT  — asserts a structural property (sign, monotonicity,
                   conservation) that must hold regardless of parameter
                   tuning.
    """

    # ------------------------------------------------------------------
    # Minimal deterministic household — flat 7% growth, no options,
    # no SS, no IRMAA anchors, no ACA, no YTD, no inherited IRAs.
    # ConversionPlan.your_conversions keyed on calendar year (base 2026).
    # ------------------------------------------------------------------

    def _simple_hh(
        self,
        *,
        your_roth: float = 0.0,
        spouse_roth: float = 0.0,
        your_ira: float = 500_000.0,
        spouse_ira: float = 0.0,
        growth_rate: float = 0.07,
        your_age: int = 55,
        spouse_age: int = 53,
    ) -> Household:
        """Minimal household with no option grants and predictable growth."""
        return Household(
            your_age=your_age,
            spouse_age=spouse_age,
            your_ira=your_ira,
            spouse_ira=spouse_ira,
            your_roth=your_roth,
            spouse_roth=spouse_roth,
            growth_rate=growth_rate,
            # Disable stock grants so option_income is always 0
            grants=[],
            # No SS before 70 for cleaner arithmetic in early years
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
        )

    # ------------------------------------------------------------------
    # Test 1: conversions credited to Roth; RMDs/extra_withdrawals are NOT
    # ------------------------------------------------------------------

    def test_conversions_credited_to_roth_and_grow_tax_free(self):
        """BEHAVIORAL: first conversion year formula matches engine expression.

        Engine rule (scenario.py):
            yr.your_roth_end = (your_roth_begin + yr.your_conversion)
                               * (1 + hh.your_roth_rate(yr.year))

        We verify this exact identity holds for the first conversion year
        so that any future change to the crediting formula breaks visibly.
        """
        conv_amount = 50_000.0
        base_year = 2026
        hh = self._simple_hh()
        plan = ConversionPlan(your_conversions={base_year: conv_amount})
        result = run_scenario(hh, plan, end_age=60)

        yr0 = result.years[0]
        assert yr0.year == base_year

        # BEHAVIORAL: your_roth_end matches the engine formula exactly.
        rate = hh.your_roth_rate(base_year)
        expected_end = (yr0.your_roth_begin + yr0.your_conversion) * (1.0 + rate)
        assert yr0.your_roth_end == pytest.approx(expected_end, rel=1e-9)

        # INVARIANT: the final Roth end is positive (conversion was credited).
        assert yr0.your_roth_end > 0.0

    def test_rmd_and_extra_withdrawal_do_not_add_to_roth(self):
        """INVARIANT: Roth grows only from prior balance; RMDs/extra go to taxable.

        Set up a post-RMD year (age 75+) with conversion=0 so the only
        change in Roth should be pure growth on the existing balance.
        """
        # Start with a nonzero Roth so we can measure growth
        your_roth_start = 200_000.0
        hh = self._simple_hh(
            your_roth=your_roth_start,
            your_ira=1_000_000.0,
            your_age=74,
            spouse_age=72,
        )
        # No conversions anywhere in the plan — only RMDs will fire at 75
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=76)

        # Find a year where your_age == 75 (RMD fires, no conversion)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)

        assert yr75.your_conversion == 0.0  # guard: no conversion in plan
        assert yr75.your_rmd > 0.0  # guard: RMD is firing

        # BEHAVIORAL: Roth end == begin * (1 + rate) — no RMD contribution.
        rate = hh.your_roth_rate(yr75.year)
        expected_roth_end = yr75.your_roth_begin * (1.0 + rate)
        assert yr75.your_roth_end == pytest.approx(expected_roth_end, rel=1e-9)

        # INVARIANT: Roth did not shrink (no distributions from Roth modeled).
        assert yr75.your_roth_end >= yr75.your_roth_begin

    # ------------------------------------------------------------------
    # Test 2: starting Roth balance flows through unchanged in year 0,
    #         then compounds each year at the roth rate with no conversions
    # ------------------------------------------------------------------

    def test_starting_roth_balance_flows_through(self):
        """BEHAVIORAL: year-0 begin equals hh.your_roth / hh.spouse_roth.

        INVARIANT: balances grow monotonically at growth_rate when there
        are no conversions (pure compounding).
        """
        your_roth_start = 250_000.0
        spouse_roth_start = 100_000.0
        rate = 0.07
        hh = self._simple_hh(
            your_roth=your_roth_start,
            spouse_roth=spouse_roth_start,
            growth_rate=rate,
        )
        plan = ConversionPlan()  # no conversions
        result = run_scenario(hh, plan, end_age=60)

        yr0 = result.years[0]

        # BEHAVIORAL: year-0 begin balances equal the household inputs.
        assert yr0.your_roth_begin == pytest.approx(your_roth_start)
        assert yr0.spouse_roth_begin == pytest.approx(spouse_roth_start)

        # BEHAVIORAL: each subsequent year's begin equals the prior year's end
        # (pure compounding — engine carry-forward: your_roth = yr.your_roth_end).
        for i in range(1, len(result.years)):
            prev = result.years[i - 1]
            curr = result.years[i]
            assert curr.your_roth_begin == pytest.approx(prev.your_roth_end, rel=1e-9)
            assert curr.spouse_roth_begin == pytest.approx(prev.spouse_roth_end, rel=1e-9)

        # INVARIANT: balances grow each year (positive growth rate, no withdrawals).
        for i in range(1, len(result.years)):
            assert result.years[i].your_roth_begin > result.years[i - 1].your_roth_begin
            assert result.years[i].spouse_roth_begin > result.years[i - 1].spouse_roth_begin

    # ------------------------------------------------------------------
    # Test 3: grid-01 bias fix — converted principal still exists in Roth
    # ------------------------------------------------------------------

    def test_conversion_does_not_destroy_total_net_worth(self):
        """INVARIANT: conversion scenario net worth is not materially below no-conversion.

        Pre-fix behaviour (grid-01 bug): conversions were debited from IRA but
        the Roth was never credited, so total assets dropped by approximately
        the full converted principal — making Roth conversions appear to destroy
        wealth and biasing the grid metric against converting.

        Post-fix invariant: "total net worth" = IRA + Roth + brokerage for BOTH
        scenarios.  The no-conversion scenario accumulates RMD proceeds in the
        brokerage account (taxable), while the conversion scenario keeps money in
        tax-free, RMD-free Roth.  Including brokerage makes the comparison fair.

        The assertion proves that conversions are NOT penalised: the conversion
        scenario's net worth must not be more than 10% of total converted principal
        BELOW the no-conversion net worth.

        Pre-fix: total_conv ≈ total_noconv − principal  →  fails this bound.
        Post-fix: converted principal lives in Roth; total_conv ≥ total_noconv
                  (Roth's RMD-free, tax-free compounding is at least as good),
                  so passes with wide margin.
        """
        base_year = 2026
        conv_amount = 80_000.0

        hh_conv = self._simple_hh(your_ira=500_000.0)
        hh_noconv = self._simple_hh(your_ira=500_000.0)

        # Conversion plan: convert in the first 5 years
        plan_conv = ConversionPlan(your_conversions={base_year + i: conv_amount for i in range(5)})
        plan_noconv = ConversionPlan()  # baseline: no conversions

        end_age = 80
        result_conv = run_scenario(hh_conv, plan_conv, end_age=end_age)
        result_noconv = run_scenario(hh_noconv, plan_noconv, end_age=end_age)

        last_conv = result_conv.years[-1]
        last_noconv = result_noconv.years[-1]

        # True end-of-horizon net worth: IRA + Roth + brokerage.
        # Brokerage must be included because the no-conversion scenario parks
        # RMD proceeds there; omitting it would make that scenario look poorer
        # than it is and produce a spurious asymmetry.
        total_conv = (
            last_conv.your_ira_end
            + last_conv.spouse_ira_end
            + last_conv.your_roth_end
            + last_conv.spouse_roth_end
            + last_conv.brokerage_balance
        )
        total_noconv = (
            last_noconv.your_ira_end
            + last_noconv.spouse_ira_end
            + last_noconv.your_roth_end
            + last_noconv.spouse_roth_end
            + last_noconv.brokerage_balance
        )

        total_converted_principal = conv_amount * 5  # 400_000

        # INVARIANT: converted principal must not disappear.
        # Allow up to 10% of principal as slack for tax drag on conversion income.
        # Pre-fix: total_conv ≈ total_noconv - principal  (≫ 10% below) → FAIL.
        # Post-fix: total_conv ≥ total_noconv or only modestly below → PASS.
        assert total_conv >= total_noconv - total_converted_principal * 0.1, (
            f"Conversion scenario net worth is too far below no-conversion: "
            f"total_conv={total_conv:,.0f}, total_noconv={total_noconv:,.0f}, "
            f"gap={total_noconv - total_conv:,.0f} exceeds 10% of converted "
            f"principal {total_converted_principal:,.0f}. "
            f"This indicates converted principal is not being credited to Roth "
            f"(grid-01 bug) or is being taxed away entirely."
        )

    # ------------------------------------------------------------------
    # Test 4: _ira_at_age includes Roth — metric exceeds IRA-only sum
    # ------------------------------------------------------------------

    def test_compare_metric_includes_roth_balances(self):
        """BEHAVIORAL: _ira_at_age returns IRA + Roth, not IRA alone.

        engine/scenario_compare.py._ira_at_age (grid-01 fix):
            return yr.your_ira_begin + yr.spouse_ira_begin
                   + yr.your_roth_begin + yr.spouse_roth_begin

        We verify this by asserting that for a scenario with a nonzero
        Roth balance, the milestone balance at a late age exceeds the
        IRA-only sum (your_ira_begin + spouse_ira_begin) by exactly
        the roth begins at that age.
        """
        from engine.scenario_compare import compute_milestone_rows

        # Build a household with a meaningful starting Roth so it's visible
        # at age 75 even without any conversions.
        your_roth_start = 150_000.0
        hh = self._simple_hh(
            your_roth=your_roth_start,
            your_ira=500_000.0,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=95)

        target_age = 75
        yr75 = next(yr for yr in result.years if yr.your_age == target_age)

        ira_only = yr75.your_ira_begin + yr75.spouse_ira_begin
        roth_only = yr75.your_roth_begin + yr75.spouse_roth_begin
        full_metric = ira_only + roth_only

        # INVARIANT: the Roth is nonzero at age 75 (it grew from your_roth_start).
        assert roth_only > 0.0

        # BEHAVIORAL: milestone rows carry the same combined balance.
        milestones = compute_milestone_rows([result], milestone_ages=(target_age,))
        milestone_balance = milestones[0].ira_balance

        assert milestone_balance == pytest.approx(full_metric, rel=1e-9), (
            f"milestone ira_balance {milestone_balance:,.0f} != "
            f"IRA+Roth sum {full_metric:,.0f}. "
            f"Roth portion {roth_only:,.0f} may be excluded (grid-01 not applied to milestone rows)."
        )

        # BEHAVIORAL: milestone balance strictly exceeds IRA-only sum.
        assert milestone_balance > ira_only, (
            "milestone_balance should exceed IRA-only sum because Roth is included."
        )
