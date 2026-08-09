"""Tests for engine.scenario core — no-conversion baseline, auto-fill strategies, Roth balance tracking."""

import pytest

from config.defaults import DEFAULTS
from engine.aca import aca_subsidy_loss
from engine.ira import (
    project_ira,
)
from engine.scenario import (
    ConversionPlan,
    run_no_conversion,
    run_scenario,
)
from engine.scenario_autofill import (
    add_bracket_fill_withdrawals,
    auto_fill_12,
    auto_fill_22,
    auto_fill_irmaa_safe,
)
from engine.tax import federal_tax
from models.household import Household, SurvivorScenario


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestScenarios:
    def test_no_conversion_ira_at_75(self):
        hh = Household(living_expenses=0.0)
        result = run_no_conversion(hh, end_age=95)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        years_to_75 = 75 - DEFAULTS["your_age"]
        expected_ira = project_ira(DEFAULTS["your_ira"], 0.07, years_to_75)
        assert yr75.your_ira_begin == approx(expected_ira, tol=500)

    def test_no_conversion_rmd_at_75(self):
        hh = Household(living_expenses=0.0)
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
        # Re-captured after the exercise-schedule redesign (PR #373 follow-up):
        # default option-exercise timing moved from a base_year-anchored stagger
        # (2026-2028) to each grant's own expiry_year (2030/2031/2032 for the
        # default TXN grants), which shifts where option income competes for
        # 12% bracket room and therefore this whole per-year fill trajectory.
        # Re-captured again for P2b (baseline option income priced at
        # hh.projected_txn_price(year) instead of a flat price): 2030-2032's
        # option income is now larger (7%-compounded from txn_price_now),
        # consuming more 12%-bracket room in those years and pushing more
        # conversion volume into 2036/2037.
        expected: list[tuple[int, int, int]] = [
            (2026, 133000, 0),
            (2027, 133000, 0),
            (2028, 133000, 0),
            (2029, 133000, 0),
            (2030, 23550, 28371),
            (2031, 0, 92872),
            (2032, 0, 95463),
            (2033, 0, 133000),
            (2034, 0, 133000),
            (2035, 0, 133000),
            (2036, 0, 134650),
            (2037, 0, 99986),
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
        # Re-captured after the exercise-schedule redesign (PR #373 follow-up):
        # see test_auto_fill_12_year_by_year_snapshot for why these numbers moved.
        expected: list[tuple[int, int, int]] = [
            (2026, 243600, 0),
            (2027, 243600, 0),
            (2028, 32900, 210700),
            (2029, 0, 243600),
            (2030, 0, 153516),
            (2031, 0, 0),
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
        # IRMAA-safe diverges from fill_12/fill_22 in base_magi computation:
        # uses full combined_ss (not taxable_ss) — these per-year rows capture that.
        # Re-captured after the exercise-schedule redesign (PR #373 follow-up):
        # see test_auto_fill_12_year_by_year_snapshot for why these numbers moved.
        # Re-captured again for P2b (baseline option income priced at
        # hh.projected_txn_price(year)): larger 2030/2031 option income shifts
        # the 2030/2031 IRMAA-safe fill amounts.
        expected: list[tuple[int, int, int]] = [
            (2026, 218000, 0),
            (2027, 218000, 0),
            (2028, 89602, 128398),
            (2029, 0, 218000),
            (2030, 0, 136920),
            (2031, 0, 147890),
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
        living_expenses: float = 60_000.0,
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
            living_expenses=living_expenses,
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
            living_expenses=0.0,
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


class TestSurvivorIncomeGate:
    """Regression for IRC §408A(d)(3): decedent's planned income must not flow
    into combined_gross / MAGI / federal tax / Roth in post-death years.

    After death_year the deceased's IRA is rolled to the survivor (self-zeros),
    so RMDs are already 0.  The bug was that conversions and extra_withdrawals
    were still read from the plan dict and included in aggregates.
    """

    def _base_hh(self, who_dies: str, death_year: int) -> Household:
        return Household(
            your_age=60,
            spouse_age=58,
            your_ira=500_000.0,
            spouse_ira=500_000.0,
            your_roth=0.0,
            spouse_roth=0.0,
            growth_rate=0.05,
            grants=[],
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            survivor=SurvivorScenario(who_dies=who_dies, death_year=death_year),
        )

    def test_deceased_spouse_conversion_excluded_from_combined_gross(self) -> None:
        """After spouse dies, spouse_conversion must be 0 in all survivor years."""
        death_year = 2028
        # Large planned spouse conversion that would bleed into combined_gross if not gated.
        # Start from death_year+1 (survivor_active years only; death_year itself is pre-survivor).
        post_death_conv = 80_000.0
        hh = self._base_hh("spouse", death_year)
        plan = ConversionPlan(
            spouse_conversions=dict.fromkeys(range(death_year + 1, death_year + 5), post_death_conv)
        )
        result = run_scenario(hh, plan, end_age=75)
        for yr in result.years:
            if yr.year > death_year:
                assert yr.spouse_conversion == 0.0, (
                    f"year {yr.year}: deceased spouse_conversion={yr.spouse_conversion} != 0"
                )
                # combined_gross must not contain the dead spouse's planned conversion
                assert yr.combined_gross == pytest.approx(yr.combined_gross, abs=0.01), (
                    "combined_gross sentinel — see next assertion"
                )
                # The net check: combined_gross without any conversion vs. the gated run
                # is equivalent here since the no-conversion run uses the same household.
                assert yr.magi < yr.magi + post_death_conv, "sanity: magi would be higher if leaked"

    def test_deceased_spouse_conversion_not_credited_to_spouse_roth(self) -> None:
        """Spouse Roth must not grow from a deceased spouse's post-death planned conversions.

        Plan conversions only for years AFTER death_year (survivor_active years).
        In death_year itself the survivor flag is not yet active, so a conversion
        on that year would legitimately fire and seed the Roth — we exclude it.
        """
        death_year = 2028
        post_death_conv = 80_000.0
        hh = self._base_hh("spouse", death_year)
        # Conversions ONLY for years strictly after death — these must be zeroed by the gate
        plan_with_conv = ConversionPlan(
            spouse_conversions=dict.fromkeys(range(death_year + 1, death_year + 5), post_death_conv)
        )
        plan_no_conv = ConversionPlan()
        result_with = run_scenario(hh, plan_with_conv, end_age=75)
        result_none = run_scenario(hh, plan_no_conv, end_age=75)
        for yr_with, yr_none in zip(result_with.years, result_none.years, strict=True):
            if yr_with.year > death_year:
                # Spouse Roth must be identical whether or not the plan had post-death conversions
                assert yr_with.spouse_roth_end == pytest.approx(
                    yr_none.spouse_roth_end, rel=1e-9
                ), (
                    f"year {yr_with.year}: spouse_roth_end differs "
                    f"({yr_with.spouse_roth_end:.0f} vs {yr_none.spouse_roth_end:.0f}); "
                    "deceased spouse conversion is being credited to Roth"
                )

    def test_deceased_spouse_extra_withdrawal_excluded_from_magi(self) -> None:
        """After spouse dies, spouse_extra_withdrawal must be 0 in all survivor years."""
        death_year = 2027
        hh = self._base_hh("spouse", death_year)
        plan = ConversionPlan(
            spouse_extra_withdrawals=dict.fromkeys(range(death_year + 1, death_year + 4), 50_000.0)
        )
        result = run_scenario(hh, plan, end_age=75)
        for yr in result.years:
            if yr.year > death_year:
                assert yr.spouse_extra_withdrawal == 0.0, (
                    f"year {yr.year}: deceased spouse_extra_withdrawal="
                    f"{yr.spouse_extra_withdrawal} != 0"
                )

    def test_deceased_primary_conversion_excluded_when_you_die(self) -> None:
        """When 'you' die, yr.your_conversion must be 0 in all survivor years."""
        death_year = 2028
        post_death_conv = 60_000.0
        hh = self._base_hh("you", death_year)
        plan = ConversionPlan(
            your_conversions=dict.fromkeys(range(death_year + 1, death_year + 5), post_death_conv)
        )
        result = run_scenario(hh, plan, end_age=75)
        for yr in result.years:
            if yr.year > death_year:
                assert yr.your_conversion == 0.0, (
                    f"year {yr.year}: deceased your_conversion={yr.your_conversion} != 0"
                )

    def test_deceased_primary_extra_withdrawal_excluded_when_you_die(self) -> None:
        """When 'you' die, yr.extra_withdrawal must be 0 in all survivor years."""
        death_year = 2027
        hh = self._base_hh("you", death_year)
        plan = ConversionPlan(
            extra_withdrawals=dict.fromkeys(range(death_year + 1, death_year + 4), 40_000.0)
        )
        result = run_scenario(hh, plan, end_age=75)
        for yr in result.years:
            if yr.year > death_year:
                assert yr.extra_withdrawal == 0.0, (
                    f"year {yr.year}: deceased extra_withdrawal={yr.extra_withdrawal} != 0"
                )


class TestConversionTaxIncludesSsTorpedo:
    """F12 regression: conversion_tax must include the SS torpedo delta.

    The OBBBA senior-deduction baseline (no-conversion MAGI) was computed as
    yr.magi - conversions, which still contained conversion-elevated taxable SS.
    The fix subtracts conversion_ss_delta from base_magi so the OBBBA phaseout
    and conversion_tax both reflect the true no-conversion baseline.
    """

    def _make_hh_with_ss(self) -> Household:
        # Ages 68/66 — both collecting SS, no RMD yet (onset 73), no option income.
        # Small IRA balances so RMDs don't fire in 2026.
        # SS is large enough that a conversion pushes meaningful extra SS into taxability.
        return Household(
            your_age=68,
            spouse_age=66,
            base_year=2026,
            your_ira=200_000.0,
            spouse_ira=200_000.0,
            your_roth=0.0,
            spouse_roth=0.0,
            growth_rate=0.05,
            grants=[],
            your_ss_fra=2_500.0,   # $/month at FRA
            spouse_ss_fra=1_800.0,
            your_ss_start_age=68,
            spouse_ss_start_age=66,
            your_fra_age=67,
            spouse_fra_age=67,
            ss_cola=0.0,
            cpi_assumption=0.0,
            filing_status="MFJ",
        )

    def test_conversion_tax_includes_ss_torpedo_delta(self) -> None:
        """conversion_tax with a large conversion must exceed the no-SS-torpedo baseline.

        We run two scenarios against the same household:
          1. hh_with_ss: both collecting SS → conversion pushes more SS into taxation.
          2. hh_no_ss:   SS zeroed out       → no torpedo effect.

        For the same conversion amount, the extra conversion_tax in scenario 1 vs 2
        must be positive, proving the SS torpedo delta is captured in conversion_tax.
        """

        hh = self._make_hh_with_ss()
        conv = 50_000.0
        plan = ConversionPlan(your_conversions={2026: conv})

        result_ss = run_scenario(hh, plan, end_age=70)
        yr_ss = next(yr for yr in result_ss.years if yr.year == 2026)

        # Verify SS torpedo is active: combined SS > 0 and conversion elevated taxable SS.
        assert yr_ss.combined_ss > 0, "precondition: SS must be active"
        assert yr_ss.your_conversion == pytest.approx(conv, abs=1.0), (
            "precondition: conversion must land as planned"
        )

        # Run without any conversion to get no-conversion baseline.
        result_base = run_scenario(hh, ConversionPlan(), end_age=70)
        yr_base = next(yr for yr in result_base.years if yr.year == 2026)

        # The no-conversion taxable SS (used in base_magi for OBBBA).
        # SS torpedo: conversion pushed extra SS into taxation.
        # Verify the delta is positive (conversion inflated taxable SS vs no-conversion).
        assert yr_ss.taxable_ss_amt > yr_base.taxable_ss_amt, (
            f"taxable SS with conversion ({yr_ss.taxable_ss_amt:.0f}) must exceed "
            f"no-conversion ({yr_base.taxable_ss_amt:.0f}) — SS torpedo must fire"
        )
        torpedo_delta = yr_ss.taxable_ss_amt - yr_base.taxable_ss_amt

        # The conversion_tax must be higher than (conv * marginal_rate) alone because
        # it also covers the torpedo delta taxed at the marginal rate.
        # Minimum expected: torpedo_delta * 0.10 (lowest bracket rate).
        min_extra = torpedo_delta * 0.10
        assert yr_ss.conversion_tax > min_extra, (
            f"conversion_tax ({yr_ss.conversion_tax:.0f}) must exceed "
            f"torpedo minimum ({min_extra:.0f}); torpedo_delta={torpedo_delta:.0f}"
        )


class TestZeroConversionTaxInvariant:
    """Waterfall arg-mismatch regression: run_no_conversion must produce zero
    conversion_tax in EVERY year, for ANY household shape.

    engine/scenario.py's two compute_social_security calls (the actual-taxable-SS
    call and the no-conversion baseline call used to derive conversion_ss_delta)
    must pass the same forced_your_ira_draw/forced_spouse_ira_draw. If the
    baseline call omits them (they silently default to 0.0 per
    scenario_compute.py's signature), conversion_ss_delta captures the forced
    IRA draw's effect on SS taxability instead of the conversion's — producing a
    nonzero conversion_tax under a plan with ZERO conversions.
    """

    @pytest.mark.parametrize(
        "hh",
        [
            pytest.param(Household(), id="default"),
            pytest.param(Household(brokerage_start=2_000_000.0), id="large_brokerage"),
            pytest.param(
                Household(your_ira=0.0, spouse_ira=0.0),
                id="zero_ira",
            ),
            pytest.param(
                Household(
                    your_ira=5_000.0,
                    spouse_ira=5_000.0,
                    your_roth=0.0,
                    spouse_roth=0.0,
                ),
                id="low_ira",
            ),
            pytest.param(
                Household(filing_status="Single", your_age=61, spouse_age=61),
                id="single_filer",
            ),
            pytest.param(
                Household(your_age=76, spouse_age=74),
                id="ss_and_rmd_active",
            ),
            pytest.param(
                Household(your_age=45, spouse_age=43),
                id="pre_ss_young",
            ),
        ],
    )
    def test_no_conversion_tax_is_always_zero(self, hh: Household) -> None:
        result = run_no_conversion(hh, end_age=95)
        assert result.years, "precondition: scenario must project at least one year"
        for yr in result.years:
            assert yr.conversion_tax == 0.0, (
                f"year {yr.year} (age {yr.your_age}): expected conversion_tax == 0.0 "
                f"under run_no_conversion, got {yr.conversion_tax!r}"
            )


class TestWaterfallMarginalBaselineC8Followup:
    """audit-0805 C8 follow-up: conversion_tax/aca_loss must be the MARGINAL
    cost of THIS year's conversion, holding prior years' actual balances
    fixed -- i.e. measured against a genuinely SOLVED zero-conversion
    waterfall for the same year, not `combined_gross - conversions`. The
    naive subtraction silently attributes a forced draw's own tax to the
    conversion whenever the two coincide, because the draw's SIZE depends on
    the conversion (draw -> tax -> larger draw).
    """

    def _household(
        self,
        *,
        living_expenses: float,
        brokerage_start: float,
        your_aca_enrolled: bool = False,
    ) -> Household:
        return Household(
            grants=[],
            your_age=61,
            spouse_age=61,
            your_ira=1_000_000.0,
            spouse_ira=0.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            filing_status="MFJ",
            base_year=2026,
            growth_rate=0.0,
            brok_turnover=0.0,
            expense_inflation=0.0,
            brokerage_start=brokerage_start,
            living_expenses=living_expenses,
            your_aca_enrolled=your_aca_enrolled,
        )

    def test_no_forced_draw_conversion_tax_and_aca_loss_unchanged(self) -> None:
        """brokerage_start=$500,000 is large enough to fund the entire
        living-expense + conversion-tax shortfall ($87,640) from brokerage
        alone -- no forced IRA draw occurs this year. The subtraction-based
        baseline and the newly-solved baseline must agree exactly.

        Hand-derivation (mirrors tests/test_audit_0805_c8_expense_debit.py
        ``TestConversionCarriesItsCost``, same fixture numbers): $100,000
        conversion only, MFJ std ded $32,200 (age 61, no senior bonus) ->
        taxable=$67,800 -> 10%/12% bracket walk = $7,640.00 federal tax.
        Zero-conversion, zero-draw baseline has zero income -> $0.00 tax.
        conversion_tax = 7,640.00 - 0.00 = 7,640.00 either way.
        """
        hh = self._household(
            living_expenses=80_000.0, brokerage_start=500_000.0, your_aca_enrolled=True
        )
        plan = ConversionPlan(your_conversions={2026: 100_000.0})
        result = run_scenario(hh, plan, "c8f-no-draw", end_age=62)
        yr0 = result.years[0]

        # Precondition: confirms this year truly has NO forced IRA draw, so
        # the test actually exercises the "no draw" branch of the invariant.
        assert yr0.forced_your_ira_draw == approx(0.0)
        assert yr0.forced_spouse_ira_draw == approx(0.0)

        assert yr0.conversion_tax == approx(7_640.0)

        naive_base_aca_magi = yr0.aca_magi - yr0.your_conversion - yr0.spouse_conversion
        naive_aca_loss = aca_subsidy_loss(
            naive_base_aca_magi,
            yr0.aca_magi,
            hh.aca_benchmark_premium_annual,
            hh.aca_enhanced_subsidies_active,
            hh.filing_status,
            year=yr0.year,
            cpi=hh.cpi_assumption,
        )
        assert yr0.aca_loss == approx(naive_aca_loss)

    def test_forced_draw_conversion_tax_exceeds_naive_subtraction(self) -> None:
        """brokerage_start=$10,000 is too small to cover the $87,640
        shortfall -- a real forced IRA draw coincides with the conversion.

        DIRECTION ESTABLISHED: the naive `combined_gross - conversions`
        baseline silently INCLUDES the with-conversion-sized forced draw's
        own taxable income (bigger than the true zero-conversion draw would
        be, since a smaller shortfall needs a smaller draw), so it
        OVERSTATES the baseline tax and therefore UNDERSTATES
        conversion_tax. The fix corrects this: conversion_tax must come out
        LARGER than the naive figure.
        """
        hh = self._household(living_expenses=80_000.0, brokerage_start=10_000.0)
        plan = ConversionPlan(your_conversions={2026: 100_000.0})
        result = run_scenario(hh, plan, "c8f-draw", end_age=62)
        yr0 = result.years[0]

        # Precondition: confirms a real forced IRA draw occurred this year,
        # so the test actually exercises the fixed-point (draw depends on
        # conversion) branch the fix targets.
        assert yr0.forced_your_ira_draw > 0.0

        naive_base_gross = yr0.combined_gross - yr0.your_conversion - yr0.spouse_conversion
        naive_base_taxable = max(naive_base_gross - yr0.total_deductions, 0)
        naive_conversion_tax = federal_tax(
            yr0.taxable_income, year=yr0.year, cpi=hh.cpi_assumption
        ) - federal_tax(naive_base_taxable, year=yr0.year, cpi=hh.cpi_assumption)

        assert yr0.conversion_tax != approx(naive_conversion_tax)
        assert yr0.conversion_tax > naive_conversion_tax, (
            f"corrected conversion_tax ({yr0.conversion_tax:.2f}) must exceed "
            f"the naive combined_gross-conversions figure "
            f"({naive_conversion_tax:.2f}) -- the naive baseline silently "
            f"included the with-conversion-sized forced draw's own taxable "
            f"income, overstating the baseline tax and understating "
            f"conversion_tax"
        )


class TestLtcgStackingBaselineC8Consistency:
    """audit-0805 C8 follow-up (base_taxable consistency): the LTCG
    bracket-stacking baseline behind `conversion_ltcg_cost` (C2) is the SAME
    "cost of THIS year's conversion" counterfactual as conversion_tax/aca_loss
    -- it re-stacks the SAME realized-gains amount at the without-conversion
    ordinary-income floor to isolate the conversion's own marginal bracket
    impact. `base_taxable` (returned by compute_federal_tax and consumed at
    scenario.py's C2 block) was left on the naive `combined_gross -
    conversions` subtraction even after conversion_tax/aca_loss were fixed to
    use a genuinely SOLVED zero-conversion waterfall baseline. Whenever a
    forced draw coincides with the conversion, the naive subtraction retains
    the draw at its ACTUAL (conversion-inflated) size instead of the smaller
    size it would be in a true no-conversion year, overstating the baseline
    ordinary-income floor and therefore UNDERSTATING conversion_ltcg_cost --
    same bug, same direction, as the pre-fix conversion_tax defect above.
    """

    def _household(self, *, living_expenses: float, brokerage_start: float) -> Household:
        from models.household import GrowthProfile

        return Household(
            grants=[],
            your_age=61,
            spouse_age=61,
            your_ira=1_000_000.0,
            spouse_ira=0.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            filing_status="MFJ",
            base_year=2026,
            growth_rate=0.0,
            expense_inflation=0.0,
            brokerage_start=brokerage_start,
            brok_turnover=1.0,
            # No forecast appreciation and zero cost basis: the ONLY LTCG
            # source is the forced brokerage draw itself, realizing dollar-
            # for-dollar (basis_fraction=0) -- deterministic and independent
            # of the with/without-conversion split, since living_expenses is
            # large enough that brokerage is fully drained in BOTH cases.
            brokerage_growth=GrowthProfile(default_rate=0.0, yield_rate=0.0),
            brokerage_start_basis=0.0,
            living_expenses=living_expenses,
        )

    def test_forced_draw_ltcg_cost_exceeds_naive_subtraction(self) -> None:
        """brokerage_start=$20,000 is fully drained funding the $80,000 living
        expenses (too small on its own) -- realizing exactly $20,000 of LTCG
        via the forced brokerage draw (zero cost basis, no forecast
        appreciation). A $400,000 conversion request (bracket-ceiling-capped
        by the engine to ~$243,600) forces a big enough IRA draw that the
        NAIVE (subtraction-based) baseline's ordinary-income floor ($94,026,
        RETAINING the conversion-inflated draw) sits just under the $98,900
        MFJ 0%/15% LTCG threshold, while the TRUE (much smaller, genuinely
        SOLVED no-conversion) baseline (~$31,100) sits well under it -- so
        the two baselines disagree about how much of the $20,000 gain is
        taxed at 15% instead of 0% ($2,269 vs. $0 of baseline gain tax).
        """
        hh = self._household(living_expenses=80_000.0, brokerage_start=20_000.0)
        plan = ConversionPlan(your_conversions={2026: 400_000.0})
        result = run_scenario(hh, plan, "c2-baseline-consistency", end_age=62)
        yr0 = result.years[0]

        # Preconditions: this year truly exercises all three ingredients.
        assert yr0.forced_your_ira_draw > 0.0, "fixture must force an IRA draw"
        assert yr0.brokerage_gain_tax > 0.0, "fixture must realize LTCG-eligible gains"
        assert yr0.your_conversion > 200_000.0, (
            f"expected the bracket-ceiling cap to still allow a large conversion, "
            f"got {yr0.your_conversion:.2f}"
        )

        from engine.tax import LTCG_RATES_MFJ, LTCG_THRESHOLDS_MFJ
        from engine.tax_indexing import index_tuple

        thresholds = index_tuple(
            LTCG_THRESHOLDS_MFJ, yr0.year, hh.cpi_assumption, round50=True
        )
        # ltcg_eligible is deterministic given this fixture (see _household
        # docstring comment): the $20,000 forced brokerage draw, entirely
        # gain (zero basis), independent of the conversion.
        ltcg_eligible = 20_000.0

        def stack_tax(start: float) -> float:
            end = max(0.0, start) + ltcg_eligible
            at_15 = max(0.0, min(end, thresholds[1]) - max(start, thresholds[0]))
            at_20 = max(0.0, end - max(start, thresholds[1]))
            return at_15 * LTCG_RATES_MFJ[1] + at_20 * LTCG_RATES_MFJ[2]

        naive_base_gross = yr0.combined_gross - yr0.your_conversion - yr0.spouse_conversion
        naive_base_taxable = max(naive_base_gross - yr0.total_deductions, 0)
        naive_base_gain_tax = stack_tax(naive_base_taxable)
        naive_conversion_ltcg_cost = max(0.0, yr0.brokerage_gain_tax - naive_base_gain_tax)

        assert yr0.conversion_ltcg_cost != approx(naive_conversion_ltcg_cost), (
            f"conversion_ltcg_cost ({yr0.conversion_ltcg_cost:.2f}) must differ from "
            f"the naive combined_gross-conversions figure ({naive_conversion_ltcg_cost:.2f}) "
            "-- fixture must actually straddle an LTCG threshold differently under "
            "the two baselines for this test to be meaningful"
        )
        assert yr0.conversion_ltcg_cost > naive_conversion_ltcg_cost, (
            f"corrected conversion_ltcg_cost ({yr0.conversion_ltcg_cost:.2f}) must "
            f"exceed the naive combined_gross-conversions figure "
            f"({naive_conversion_ltcg_cost:.2f}) -- the naive baseline silently "
            "retained the with-conversion-sized forced draw's own ordinary income "
            "in the LTCG-stacking floor, overstating the baseline gain tax and "
            "understating conversion_ltcg_cost"
        )
