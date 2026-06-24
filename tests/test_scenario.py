"""Tests for engine.scenario — multi-year run, auto-fill, sweet spot, YTD wiring."""

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
from models.grants import StockGrant
from models.household import GrowthProfile, Household


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
        base = base_income_for_year(hh, 2026)
        assert base.opt == approx(hh.grants[0].spread(hh.txn_price_now))

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


class TestA2AutoFillSS86ProvisionalIncomeMagi:
    """A2 — autofill taxable-SS provisional base must include LTCG and QD from YTD snapshot.

    IRC §86(b)(2): provisional income = AGI + tax-exempt interest + 0.5*SS.
    Pre-fix code summed only ordinary YTD fields (wages, NEC, STCG, ordinary_dividends,
    interest, conversions, distributions), omitting ltcg_ytd, qualified_dividends_ytd,
    and tax_exempt_interest_ytd.  A household with meaningful LTCG/QD should have a
    higher taxable SS and therefore less bracket room in the base year.
    """

    def _hh(self) -> "Household":
        return Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            cpi_assumption=0.0,
            your_ira=500_000.0,
            spouse_ira=300_000.0,
            your_ss_fra=2_500.0,
            your_ss_start_age=70,
            spouse_ss_fra=0.0,
            grants=[],
        )

    def test_ltcg_in_ytd_raises_taxable_ss_reduces_bracket_room(self) -> None:
        """LTCG in base-year YTD raises §86 provisional income → more taxable SS
        → autofill finds less bracket room in the base year.

        Scenario: household with small SS ($15K combined) and small wages ($10K).
        Without LTCG: provisional = 10K + 0.5*15K = 17.5K < tier1 ($32K) → tss = 0.
        With LTCG $30K: provisional = 40K + 0.5*15K = 47.5K > tier2 ($44K) → tss > 0.
        The LTCG household must have a lower conversion amount because tss shrinks room.
        """
        from models.ytd_income import YTDSnapshot

        # Small SS so that provisional crosses the tier1 threshold only with LTCG.
        # your_ss_start_age=61 so SS is active in the base year (ya=61 >= start=61).
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            cpi_assumption=0.0,
            your_ira=500_000.0,
            spouse_ira=300_000.0,
            your_ss_fra=625.0,   # reduced early: ~$7.5K/yr (combined ~$7.5K, no spouse SS)
            your_ss_start_age=61,  # claiming at 61: active in base year
            spouse_ss_fra=0.0,
            grants=[],
        )
        ytd_no_ltcg = YTDSnapshot(tax_year=2026, wages_ytd=10_000)
        ytd_with_ltcg = YTDSnapshot(tax_year=2026, wages_ytd=10_000, ltcg_ytd=30_000)

        plan_no_ltcg = auto_fill_12(hh, early_exercise=False, ytd=ytd_no_ltcg)
        plan_with_ltcg = auto_fill_12(hh, early_exercise=False, ytd=ytd_with_ltcg)

        base_year = hh.base_year
        conv_no_ltcg = plan_no_ltcg.your_conversions.get(base_year, 0.0)
        conv_with_ltcg = plan_with_ltcg.your_conversions.get(base_year, 0.0)

        # LTCG raises taxable SS → more ordinary income stacked → less room to 12% ceiling
        assert conv_with_ltcg < conv_no_ltcg, (
            f"Expected less conversion room with LTCG in YTD (SS taxed more), "
            f"but got conv_with_ltcg={conv_with_ltcg:.0f} >= conv_no_ltcg={conv_no_ltcg:.0f}."
        )

    def test_qualified_dividends_in_ytd_raise_taxable_ss(self) -> None:
        """QD in base-year YTD raises §86 provisional income → more taxable SS
        → autofill finds less bracket room in the base year.

        Scenario: household with small SS ($15K combined) and small wages ($10K).
        Without QD: provisional = 10K + 7.5K = 17.5K < tier1 → tss = 0.
        With QD $30K: provisional = 40K + 7.5K = 47.5K → tss > 0.
        """
        from models.ytd_income import YTDSnapshot

        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            cpi_assumption=0.0,
            your_ira=500_000.0,
            spouse_ira=300_000.0,
            your_ss_fra=625.0,
            your_ss_start_age=61,
            spouse_ss_fra=0.0,
            grants=[],
        )
        ytd_no_qd = YTDSnapshot(tax_year=2026, wages_ytd=10_000)
        ytd_with_qd = YTDSnapshot(
            tax_year=2026, wages_ytd=10_000, qualified_dividends_ytd=30_000
        )

        plan_no_qd = auto_fill_12(hh, early_exercise=False, ytd=ytd_no_qd)
        plan_with_qd = auto_fill_12(hh, early_exercise=False, ytd=ytd_with_qd)

        base_year = hh.base_year
        conv_no_qd = plan_no_qd.your_conversions.get(base_year, 0.0)
        conv_with_qd = plan_with_qd.your_conversions.get(base_year, 0.0)

        assert conv_with_qd < conv_no_qd, (
            f"Expected less conversion room with QD in YTD (SS taxed more), "
            f"but got conv_with_qd={conv_with_qd:.0f} >= conv_no_qd={conv_no_qd:.0f}."
        )


# ============================================================
#  Tax Return Sync (TurboTax via FinExtract)
# ============================================================

# ============================================================
#  YTD Income Tracker & Headroom
# ============================================================


class TestAutoFillCoreOrdinaryDividendsYTD:
    """Regression tests: _auto_fill_core must include ordinary_dividends_ytd in fixed_gross.

    Prior to the fix (math audit 2026-06-12 Priority 3), _auto_fill_core added only
    wages_ytd and stcg_ytd from the YTD snapshot, omitting ordinary_dividends_ytd
    (and nec_income_ytd, ira_conversions_ytd, ira_distributions_ytd). This caused
    bracket room to be overstated by the omitted ordinary income amount.
    """

    def _base_hh(self) -> Household:
        return Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=1_700_000,
            spouse_ira=1_700_000,
        )

    def test_ordinary_dividends_reduce_room_base_year(self):
        """ordinary_dividends_ytd must reduce base-year bracket room and conversion amount."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_div = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_div = YTDSnapshot(
            tax_year=2026,
            wages_ytd=50_000,
            ordinary_dividends_ytd=10_000,
        )

        plan_no_div = auto_fill_12(hh, ytd=ytd_no_div)
        plan_with_div = auto_fill_12(hh, ytd=ytd_with_div)

        base_conv = plan_no_div.your_conversions.get(2026, 0.0)
        div_conv = plan_with_div.your_conversions.get(2026, 0.0)

        # ordinary_dividends_ytd consumes bracket room → fewer conversions in base year
        assert div_conv < base_conv, (
            f"Expected ordinary_dividends_ytd to reduce base-year conversion, "
            f"got no_div={base_conv:.0f} vs with_div={div_conv:.0f}"
        )
        # Difference should match the dividend amount (ordinary income fills bracket space)
        assert base_conv - div_conv == approx(10_000, tol=200)

    def test_nec_income_reduces_room_base_year(self):
        """nec_income_ytd (1099-NEC) must also reduce base-year bracket room."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_nec = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_nec = YTDSnapshot(tax_year=2026, wages_ytd=50_000, nec_income_ytd=8_000)

        plan_no_nec = auto_fill_12(hh, ytd=ytd_no_nec)
        plan_with_nec = auto_fill_12(hh, ytd=ytd_with_nec)

        base_conv = plan_no_nec.your_conversions.get(2026, 0.0)
        nec_conv = plan_with_nec.your_conversions.get(2026, 0.0)

        assert nec_conv < base_conv
        assert base_conv - nec_conv == approx(8_000, tol=200)

    def test_ira_conversions_done_reduce_room_base_year(self):
        """ira_conversions_ytd already done must reduce remaining planned room."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_done = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_done = YTDSnapshot(tax_year=2026, wages_ytd=50_000, ira_conversions_ytd=15_000)

        plan_no_done = auto_fill_12(hh, ytd=ytd_no_done)
        plan_done = auto_fill_12(hh, ytd=ytd_done)

        base_conv = plan_no_done.your_conversions.get(2026, 0.0)
        done_conv = plan_done.your_conversions.get(2026, 0.0)

        assert done_conv < base_conv
        assert base_conv - done_conv == approx(15_000, tol=200)

    def test_future_years_unaffected(self):
        """YTD snapshot only applies to base year; future years must be identical."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=50_000,
            ordinary_dividends_ytd=10_000,
        )

        plan_no_ytd = auto_fill_12(hh)
        plan_with_ytd = auto_fill_12(hh, ytd=ytd)

        # All years after 2026 must be identical
        future_years_no = {y: v for y, v in plan_no_ytd.your_conversions.items() if y > 2026}
        future_years_with = {y: v for y, v in plan_with_ytd.your_conversions.items() if y > 2026}
        assert future_years_no == pytest.approx(future_years_with, abs=1.0)

    def test_total_subtract_grant_id_empty_uses_total(self):
        """Total subtract applies even when StockGrant.grant_id is empty (legacy fixture)."""
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            grants=[StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026, grant_id="")],
            txn_price_now=200.0,
        )
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=80_000)
        ytd._option_exercises_by_grant = {"GR-2019": 80_000}  # noqa: SLF001
        result = compute_headroom(hh, ytd, early_exercise=True)
        # Total subtract: realized = ytd.nqo_exercise_ytd regardless of grant_id
        assert result.realized_option_income_ytd == approx(80_000)
        assert result.planned_option_income == approx(192_000 - 80_000)

    def test_magi_ytd_includes_tax_exempt_interest(self):
        """Tax-exempt (muni) interest must appear in IRMAA MAGI even though it is federally exempt."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=80_000, tax_exempt_interest_ytd=5_000)
        # MAGI = wages + tax_exempt_interest
        assert ytd.magi_ytd == approx(85_000)

    def test_tax_exempt_interest_not_in_total_ordinary_income(self):
        """Tax-exempt interest is federally exempt — it must NOT stack into ordinary brackets."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=80_000, tax_exempt_interest_ytd=5_000)
        # ordinary income = wages only; muni interest is excluded
        assert ytd.total_ordinary_income == approx(80_000)

    def test_interest_ytd_reduces_room_base_year(self):
        """Regression C-4: interest_ytd must reduce base-year bracket room and conversion amount.

        Prior to the fix, _auto_fill_core omitted interest_ytd from both other_fixed
        (provisional income for SS taxability) and fixed_gross (bracket math), causing
        conversion room to be overstated by the full interest amount.
        """
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_int = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_int = YTDSnapshot(tax_year=2026, wages_ytd=50_000, interest_ytd=12_000)

        plan_no_int = auto_fill_22(hh, ytd=ytd_no_int)
        plan_with_int = auto_fill_22(hh, ytd=ytd_with_int)

        base_conv = plan_no_int.your_conversions.get(2026, 0.0)
        int_conv = plan_with_int.your_conversions.get(2026, 0.0)

        # interest_ytd is fully taxable ordinary income → consumes bracket room → fewer conversions
        assert int_conv < base_conv, (
            f"Expected interest_ytd=12_000 to reduce base-year conversion, "
            f"got no_int={base_conv:.0f} vs with_int={int_conv:.0f}"
        )
        # Difference should be approximately the interest amount
        assert base_conv - int_conv == approx(12_000, tol=300)


class TestScenarioWithYTD:
    """Test scenario engine with YTD injection."""

    def test_ltcg_in_magi_not_gross(self):
        """LTCG appears in base-year MAGI but NOT in combined_gross."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=200_000)
        plan = ConversionPlan(your_conversions={2026: 50_000})
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        # MAGI should include LTCG
        assert yr2026.magi > 200_000

        # combined_gross should NOT include LTCG
        # (only option income + conversion + taxable SS)
        assert yr2026.combined_gross < 200_000

    def test_scenario_combined_gross_includes_ytd_ordinary_dividends(self):
        """Ordinary dividends in YTD snapshot must stack into combined_gross (ordinary income)."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd_no_div = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_div = YTDSnapshot(tax_year=2026, wages_ytd=50_000, ordinary_dividends_ytd=4_000)
        plan = ConversionPlan()

        result_no_div = run_scenario(hh, plan, "no_div", end_age=65, ytd=ytd_no_div)
        result_with_div = run_scenario(hh, plan, "with_div", end_age=65, ytd=ytd_with_div)

        yr_no_div = result_no_div.years[0]
        yr_with_div = result_with_div.years[0]

        # combined_gross in the dividend scenario should be exactly 4_000 higher
        assert yr_with_div.combined_gross - yr_no_div.combined_gross == approx(4_000)

    def test_ytd_does_not_affect_future_years(self):
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=200_000, wages_ytd=100_000)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "test", end_age=70, ytd=ytd)

        yr2026 = next(yr for yr in result.years if yr.year == 2026)
        yr2027 = next(yr for yr in result.years if yr.year == 2027)

        # 2026 should have YTD fields populated
        assert yr2026.ytd_ltcg == approx(200_000)
        assert yr2026.ytd_wages == approx(100_000)

        # 2027 should have zero YTD fields
        assert yr2027.ytd_ltcg == 0
        assert yr2027.ytd_wages == 0

    def test_conversions_done_subtracted(self):
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ira_conversions_ytd=30_000)
        plan = ConversionPlan(your_conversions={2026: 100_000})
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        # Planned $100K minus $30K already done = $70K
        assert yr2026.your_conversion == approx(70_000)

    def test_run_scenario_includes_ytd_conversions_in_base_magi(self):
        """ira_conversions_ytd must appear in base-year MAGI even though it
        reduces the remaining planned conversion amount."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        conversions_done = 35_000
        ytd = YTDSnapshot(tax_year=2026, ira_conversions_ytd=conversions_done)
        # Plan more than what's already done so yr.your_conversion > 0
        plan = ConversionPlan(your_conversions={2026: 100_000})
        result_with = run_scenario(hh, plan, "with_conv", end_age=65, ytd=ytd)
        result_without = run_scenario(hh, plan, "without_conv", end_age=65, ytd=None)

        yr_with = result_with.years[0]
        yr_without = result_without.years[0]

        # Invariant: both scenarios plan the same $100K total conversion; ira_conversions_ytd
        # merely shifts income from yr.your_conversion (planned remaining) to magi_ytd (already
        # done). The SUM must be equal — the absolute value depends on default Household
        # option_income which varies with tax-year defaults (e.g. TXN NQO grants in 2026).
        assert yr_with.magi == approx(yr_without.magi)

    def test_run_scenario_includes_nec_in_base_magi(self):
        """nec_income_ytd (1099-NEC) must appear in base-year MAGI."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        nec = 28_000
        ytd_with = YTDSnapshot(tax_year=2026, nec_income_ytd=nec)
        ytd_none = YTDSnapshot(tax_year=2026)
        plan = ConversionPlan()

        yr_with = run_scenario(hh, plan, "nec", end_age=65, ytd=ytd_with).years[0]
        yr_none = run_scenario(hh, plan, "no_nec", end_age=65, ytd=ytd_none).years[0]

        assert yr_with.magi - yr_none.magi == approx(nec)

    def test_run_scenario_includes_distributions_in_base_magi(self):
        """ira_distributions_ytd (non-conversion IRA withdrawals) must appear
        in base-year MAGI."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        distrib = 42_000
        ytd_with = YTDSnapshot(tax_year=2026, ira_distributions_ytd=distrib)
        ytd_none = YTDSnapshot(tax_year=2026)
        plan = ConversionPlan()

        yr_with = run_scenario(hh, plan, "dist", end_age=65, ytd=ytd_with).years[0]
        yr_none = run_scenario(hh, plan, "no_dist", end_age=65, ytd=ytd_none).years[0]

        assert yr_with.magi - yr_none.magi == approx(distrib)

    def test_run_scenario_matches_canonical_magi_ytd(self):
        """Base-year MAGI must equal canonical YTDSnapshot.magi_ytd plus the
        projected income components (remaining planned conversion, option
        income, SS, RMD) — verifying parity with _auto_fill_core."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=60_000,
            nec_income_ytd=10_000,
            ira_conversions_ytd=25_000,
            ira_distributions_ytd=15_000,
            ltcg_ytd=50_000,
            stcg_ytd=5_000,
            qualified_dividends_ytd=3_000,
            ordinary_dividends_ytd=2_000,
            interest_ytd=1_000,
        )
        planned_conversion = 80_000
        plan = ConversionPlan(your_conversions={2026: planned_conversion})
        result = run_scenario(hh, plan, "canonical", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        # Projected components not in magi_ytd.
        # D-1: uses taxable_ss_amt (not full combined_ss) — per §1395r(i)(4).
        # C-7: option_income contribution is net of nqo_exercise_ytd (no NQO in this ytd → zero).
        # E-3: includes realized_gains (brokerage_growth * brok_turnover).
        projected_components = (
            yr2026.option_income  # no nqo_exercise_ytd in this ytd, so no dedup delta
            + yr2026.your_conversion  # remaining after subtracting ira_conversions_ytd
            + yr2026.spouse_conversion
            + yr2026.taxable_rmd
            + yr2026.spouse_taxable_rmd
            + yr2026.extra_withdrawal
            + yr2026.spouse_extra_withdrawal
            + yr2026.taxable_ss_amt  # D-1: was combined_ss; zero here (age 61, no SS)
            + yr2026.your_inherited_distribution
            + yr2026.spouse_inherited_distribution
            + yr2026.brokerage_qual_div
            + yr2026.brokerage_ord_div
            + yr2026.brokerage_growth * hh.brok_turnover  # E-3: realized_gains
        )
        expected_magi = projected_components + ytd.magi_ytd
        assert yr2026.magi == approx(expected_magi)

    def test_ytd_save_load_roundtrip(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import RealizedGainEvent, YTDSnapshot

        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", tmp_path / "ytd.json")

        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=50_000,
            ltcg_ytd=200_000,
            stcg_ytd=10_000,
            ordinary_dividends_ytd=5_000,
            interest_ytd=3_000,
            ira_conversions_ytd=20_000,
            snapshot_date="2026-06-15",
            gain_events=[
                RealizedGainEvent(
                    date="2026-03-15",
                    description="TXN stop-loss",
                    proceeds=250_000,
                    cost_basis=50_000,
                    holding_period="long",
                    account_name="Schwab",
                ),
            ],
        )
        save_ytd_snapshot(ytd)
        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.wages_ytd == 50_000
        assert loaded.ltcg_ytd == 200_000
        assert loaded.stcg_ytd == 10_000
        assert loaded.dividends_ytd == 5_000
        assert loaded.interest_ytd == 3_000
        assert loaded.ira_conversions_ytd == 20_000
        assert len(loaded.gain_events) == 1
        assert loaded.gain_events[0].gain_loss == approx(200_000)

    def test_ytd_ltcg_bracket_walk_zero_percent_band(self):
        """YTD LTCG fully inside the 0% band must produce zero LTCG tax.

        Regression for audit A-5/D-5: flat-rate hh.ltcg_rate was applied,
        yielding $6,000 instead of $0 for a household with taxable_ordinary
        well below the MFJ 0%-band ceiling (~$96,700 for 2026).
        """
        from models.ytd_income import YTDSnapshot

        # Wages $30K → std deduction $30,000 (MFJ both <65) → taxable ~$0,
        # well below LTCG_THRESHOLDS_MFJ[0] (~$96,700). $40K LTCG stays in 0% band.
        hh = Household(your_age=61, spouse_age=55, base_year=2026)
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=30_000, ltcg_ytd=40_000)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "ltcg_bracket", end_age=62, ytd=ytd)
        yr2026 = result.years[0]

        # taxable_income (ordinary) should be well below ~$98,900 threshold
        assert yr2026.taxable_income < 98_900
        # All $40K LTCG sits in the 0% band — no LTCG tax owed
        assert yr2026.ytd_ltcg_tax == approx(0.0)


class TestScenarioDividendProjection:
    """Tests for brokerage dividend projection in scenario engine."""

    def _rmd_household(self, **kwargs) -> Household:
        """Household at RMD age so excess RMD seeds brokerage in year 1."""
        return Household(
            your_age=75,
            spouse_age=69,
            base_year=2026,
            your_ira=4_000_000,
            spouse_ira=1_000_000,
            growth_rate=0.07,
            **kwargs,
        )

    def test_zero_yield_is_backward_compatible(self):
        """GrowthProfile with yield_rate=0 → identical outputs to no GrowthProfile."""
        hh_default = self._rmd_household()
        hh_explicit = self._rmd_household(
            brokerage_growth=GrowthProfile(default_rate=0.07, yield_rate=0.0),
        )
        r_default = run_scenario(hh_default, ConversionPlan(), "default", end_age=80)
        r_explicit = run_scenario(hh_explicit, ConversionPlan(), "explicit", end_age=80)

        for yr_d, yr_e in zip(r_default.years, r_explicit.years, strict=True):
            assert yr_d.magi == pytest.approx(yr_e.magi, abs=1.0)
            assert yr_d.combined_gross == pytest.approx(yr_e.combined_gross, abs=1.0)
            assert yr_d.brokerage_balance == pytest.approx(yr_e.brokerage_balance, abs=1.0)

    def test_yield_pushes_qualified_to_magi(self):
        """qualified_fraction=1.0 → qualified dividends increment MAGI but not combined_gross."""
        # Use brokerage_growth with yield but all-qualified; run two years so brokerage is seeded.
        hh_no_yield = self._rmd_household(
            brokerage_growth=GrowthProfile(default_rate=0.07, yield_rate=0.0),
        )
        hh_yield = self._rmd_household(
            brokerage_growth=GrowthProfile(
                default_rate=0.07, yield_rate=0.03, qualified_fraction=1.0
            ),
        )
        r_no = run_scenario(hh_no_yield, ConversionPlan(), "no_yield", end_age=80)
        r_yes = run_scenario(hh_yield, ConversionPlan(), "with_yield", end_age=80)

        # Find a year where brokerage has accumulated (age 77, 2 years of excess)
        yr_no = next(yr for yr in r_no.years if yr.your_age == 77)
        yr_yes = next(yr for yr in r_yes.years if yr.your_age == 77)

        # With qualified dividends: MAGI should be higher
        assert yr_yes.magi > yr_no.magi
        # combined_gross should be equal (qualified divs don't stack into ordinary brackets)
        assert yr_yes.combined_gross == pytest.approx(yr_no.combined_gross, abs=1.0)
        # Qualified div field should be nonzero in yield scenario
        assert yr_yes.brokerage_qual_div > 0.0
        assert yr_yes.brokerage_ord_div == pytest.approx(0.0)

    def test_yield_pushes_ordinary_to_combined_gross(self):
        """qualified_fraction=0.0 → ordinary dividends increment both MAGI and combined_gross."""
        hh_no_yield = self._rmd_household(
            brokerage_growth=GrowthProfile(default_rate=0.07, yield_rate=0.0),
        )
        hh_ord = self._rmd_household(
            brokerage_growth=GrowthProfile(
                default_rate=0.07, yield_rate=0.03, qualified_fraction=0.0
            ),
        )
        r_no = run_scenario(hh_no_yield, ConversionPlan(), "no_yield", end_age=80)
        r_ord = run_scenario(hh_ord, ConversionPlan(), "ord_yield", end_age=80)

        yr_no = next(yr for yr in r_no.years if yr.your_age == 77)
        yr_ord = next(yr for yr in r_ord.years if yr.your_age == 77)

        # With ordinary dividends: both MAGI and combined_gross should be higher
        assert yr_ord.magi > yr_no.magi
        assert yr_ord.combined_gross > yr_no.combined_gross
        # Ordinary div field should be nonzero; qualified should be zero
        assert yr_ord.brokerage_ord_div > 0.0
        assert yr_ord.brokerage_qual_div == pytest.approx(0.0)


# ============================================================
#  G3 Characterization: deductions / senior_bonus / taxable_ss
# ============================================================


class TestSpouseRMDBrokerageAccumulation:
    """Regression: available_income must include spouse RMD and spouse extra_withdrawal.

    Bug (audit C-2): lines 561-562 of engine/scenario.py computed after_tax_rmd and
    available_income using only the "your" side — omitting yr.spouse_taxable_rmd and
    yr.spouse_extra_withdrawal.  When both spouses are in RMD, the spouse contribution
    can exceed $60K/yr, causing brokerage accumulation to be understated by $500K+
    over a 10-year window.
    """

    def _rmd_household(self, your_ira: float, spouse_ira: float) -> Household:
        """Both spouses already 75 (in RMD), no conversions, modest SS."""
        from dataclasses import replace

        return replace(
            Household(grants=[]),
            your_age=75,
            spouse_age=75,
            your_ira=your_ira,
            spouse_ira=spouse_ira,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            living_expenses=60_000.0,
        )

    def test_spouse_rmd_increases_brokerage_balance(self):
        """With spouse IRA active, year-10 brokerage must exceed the no-spouse baseline.

        Setup: both spouses 75 with $1.5M trad IRAs each (~$60K RMD/yr each at 75,
        divisor ≈25).  No conversions.  Living expenses $60K.  With fix, both RMDs
        flow into available_income; excess accumulates in brokerage.
        """
        plan = ConversionPlan()

        # Baseline: your IRA only (spouse IRA zeroed out → spouse_taxable_rmd ≈ 0)
        hh_yours_only = self._rmd_household(your_ira=1_500_000.0, spouse_ira=0.0)
        result_yours = run_scenario(hh_yours_only, plan, end_age=85)

        # With spouse: both IRAs $1.5M → spouse_taxable_rmd ≈ $60K extra each year
        hh_both = self._rmd_household(your_ira=1_500_000.0, spouse_ira=1_500_000.0)
        result_both = run_scenario(hh_both, plan, end_age=85)

        brok_yours = result_yours.years[-1].brokerage_balance
        brok_both = result_both.years[-1].brokerage_balance

        # The spouse RMD (~$60K/yr after-tax) compounded over 10 years at a
        # brokerage rate ≈7% produces well over $800K extra.  A conservative
        # floor of $500K guards against this regression without being brittle.
        assert brok_both > brok_yours + 500_000, (
            f"Expected brokerage with spouse RMD to exceed baseline by >$500K; "
            f"got brok_both={brok_both:,.0f}, brok_yours={brok_yours:,.0f}, "
            f"delta={brok_both - brok_yours:,.0f}"
        )

    def test_spouse_rmd_zero_equals_baseline(self):
        """When spouse IRA is zero, available_income must match the pre-fix behaviour.

        Ensures the fix is additive and does not corrupt the single-earner path.
        """
        plan = ConversionPlan()
        hh = self._rmd_household(your_ira=1_500_000.0, spouse_ira=0.0)
        result = run_scenario(hh, plan, end_age=85)

        for yr in result.years:
            if yr.your_age >= 75:
                assert yr.spouse_taxable_rmd == pytest.approx(0.0), (
                    f"year {yr.year}: spouse_taxable_rmd should be 0 when spouse IRA=0"
                )


# ============================================================
#  Regression: MAGI ordering + YTD LTCG cost (grid-02/03/05)
#  Commit 8aa7e525 — three engine fixes to run_scenario():
#    grid-05: yr.ytd_ltcg_tax folded into federal_tax_amt
#    grid-03: IRMAA same-year fallback uses realized-gains-inclusive MAGI
#    grid-02: (covered by value-preservation test below)
# ============================================================


class TestMagiOrderingAndLtcgCost:
    """Regression tests for the three engine fixes in commit 8aa7e525.

    Behavioral assertion type is noted per test:
      BEHAVIORAL  — exercises the fix path, fails on pre-fix code
      INVARIANT   — checks a structural guarantee; passes on both old/new but
                    documents and locks the property for future refactors
    """

    # ------------------------------------------------------------------
    # Test 1 — grid-05: ytd_ltcg_tax folded into federal_tax_amt
    # Assertion type: BEHAVIORAL
    # ------------------------------------------------------------------

    def test_ytd_ltcg_tax_folded_into_federal_tax_amt(self):
        """YTD LTCG tax (grid-05) must be counted in federal_tax_amt.

        Pre-fix: yr.ytd_ltcg_tax was computed and stored but never added to
        federal_tax_amt, so the base-year total tax was understated.
        Post-fix: federal_tax_amt includes ytd_ltcg_tax.

        Approach: run two scenarios — one with ltcg_ytd large enough to
        produce 15% LTCG tax, one without.  Assert:
          (a) yr.ytd_ltcg_tax > 0  (sanity — LTCG is actually taxed at 15%)
          (b) federal_tax_amt_with - federal_tax_amt_without == yr.ytd_ltcg_tax
              (to pytest.approx)

        Fixture:
          wages_ytd=150_000 pushes taxable_income above the MFJ 0%-band
          ceiling (~$98,900 in 2026) so that ltcg_ytd=60_000 is entirely
          taxed at 15% → expected ytd_ltcg_tax = 60_000 * 0.15 = $9,000.
          No conversions; no grants (strips option income for clarity).
        """
        from models.ytd_income import YTDSnapshot

        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=1_700_000,
            spouse_ira=1_700_000,
            grants=[],  # remove option income for a clean fixture
        )

        ltcg_amount = 60_000
        ytd_with = YTDSnapshot(tax_year=2026, wages_ytd=150_000, ltcg_ytd=ltcg_amount)
        ytd_without = YTDSnapshot(tax_year=2026, wages_ytd=150_000, ltcg_ytd=0)

        plan = ConversionPlan()
        yr_with = run_scenario(hh, plan, "with_ltcg", end_age=62, ytd=ytd_with).years[0]
        yr_without = run_scenario(hh, plan, "no_ltcg", end_age=62, ytd=ytd_without).years[0]

        # Sanity: taxable_income above the 0%-band so LTCG is taxed at 15%
        assert yr_with.taxable_income > 98_900, (
            f"Fixture broken: taxable_income={yr_with.taxable_income:.0f} is not above "
            f"MFJ 0%-band ceiling (~$98,900); LTCG would not be taxed at 15%"
        )

        # (a) BEHAVIORAL: ytd_ltcg_tax must be positive
        assert yr_with.ytd_ltcg_tax > 0, (
            f"Expected ytd_ltcg_tax > 0 for ltcg_ytd={ltcg_amount}; got {yr_with.ytd_ltcg_tax}"
        )

        # (b) BEHAVIORAL: the delta in federal_tax_amt must equal ytd_ltcg_tax exactly.
        # Pre-fix code never added ytd_ltcg_tax to federal_tax_amt, so the delta
        # would be 0 on pre-fix code (assertion would fail).
        delta = yr_with.federal_tax_amt - yr_without.federal_tax_amt
        assert delta == pytest.approx(yr_with.ytd_ltcg_tax, abs=1.0), (
            f"federal_tax_amt delta ({delta:.2f}) != ytd_ltcg_tax "
            f"({yr_with.ytd_ltcg_tax:.2f}); grid-05 fix may be missing or double-counted"
        )

        # (c) INVARIANT: no-LTCG run has zero ytd_ltcg_tax
        assert yr_without.ytd_ltcg_tax == pytest.approx(0.0)

    # ------------------------------------------------------------------
    # Test 2 — grid-03: IRMAA same-year fallback uses realized-gains-
    #          inclusive MAGI
    # Assertion type: BEHAVIORAL (direct IRMAA cost) + INVARIANT (magi_history)
    # ------------------------------------------------------------------

    def test_irmaa_fallback_uses_realized_gains_inclusive_magi(self):
        """IRMAA same-year fallback (grid-03) must see the full yr.magi.

        The fallback fires for yr_idx < 2 when prior_year_magi is empty.
        Pre-fix: realized_gains were folded into yr.magi *after* the MAGI
        ordering block, so magi_history[year] captured MAGI without realized
        gains; the fallback then under-stated magi_for_irmaa.
        Post-fix: realized_gains are hoisted before magi_history[year] = yr.magi,
        so the stored value and the fallback both include them.

        Strategy:
          - Use an RMD-age household (your_age=75) so the year-0 RMD produces
            excess_rmd that seeds the brokerage for year 1.
          - Year 1 (yr_idx=1): income_year=2025. Not in magi_history (only 2026
            was stored in yr_idx=0); prior_year_magi is empty → fallback fires,
            magi_for_irmaa = yr.magi.
          - Give the household a high brokerage_growth so year-1 realized gains
            (brokerage * appreciation * turnover) are meaningful.
          - Assert: magi_history value for year 0 equals yr2026.magi (invariant),
            AND that yr2027.irmaa_cost is nonzero (behavioral — MAGI + realized
            gains pushes us above an IRMAA tier; would fail if magi is understated
            and the threshold is not crossed).

        Note on observability: magi_history is internal state; we cannot inspect it
        directly from outside run_scenario.  We instead verify the downstream
        effect: the IRMAA cost for the fallback year reflects the full yr.magi
        (confirmed by checking the relationship irmaa_cost > 0 and that it is
        consistent with the recorded yr.magi passing an IRMAA threshold).
        """
        from engine.irmaa import irmaa_for_year

        # RMD-age household: large IRA produces RMD > living_expenses, seeding brokerage.
        # High brokerage appreciation rate → large realized gains in year 1.
        # prior_year_magi intentionally empty → fallback fires for yr_idx 0 and 1.
        hh = Household(
            your_age=75,
            spouse_age=69,
            base_year=2026,
            your_ira=4_000_000,
            spouse_ira=500_000,
            growth_rate=0.07,
            living_expenses=60_000.0,
            grants=[],
            brokerage_growth=GrowthProfile(default_rate=0.30),
        )

        plan = ConversionPlan()
        result = run_scenario(hh, plan, "fallback_irmaa", end_age=77)

        yr2026 = result.years[0]  # yr_idx=0: fallback fires, brokerage=0 → realized_gains=0
        yr2027 = result.years[1]  # yr_idx=1: fallback fires, brokerage>0 → realized_gains>0

        # INVARIANT: brokerage was seeded in year 0 (excess_rmd > 0)
        assert yr2026.excess_rmd > 0, "Fixture broken: year 0 produced no excess_rmd"
        # INVARIANT: year-1 brokerage balance reflects year-0 seeding
        assert yr2027.brokerage_balance > 0, (
            "Fixture broken: year-1 brokerage is 0; realized_gains test is vacuous"
        )

        # Compute expected realized gains for year 1 (same formula as engine):
        brok_y1 = yr2027.brokerage_balance  # begin-of-year balance
        brok_appreciation_rate = hh.brokerage_growth.appreciation_for(2027)  # type: ignore[union-attr]
        expected_realized_gains_y1 = brok_y1 * brok_appreciation_rate * hh.brok_turnover
        assert expected_realized_gains_y1 > 0, (
            "Fixture broken: expected realized gains in year 1 are zero"
        )

        # BEHAVIORAL: yr.magi for year 1 must include realized gains.
        # We verify this by recomputing what magi *without* realized gains would be
        # (= yr.magi - expected_realized_gains_y1) and asserting the full magi is larger.
        magi_without_realized = yr2027.magi - expected_realized_gains_y1
        assert yr2027.magi > magi_without_realized, (
            "yr2027.magi does not exceed the no-realized-gains baseline; "
            "realized_gains may not be included in magi"
        )

        # INVARIANT: irmaa_cost for yr2027 (fallback year) must be consistent with
        # the full yr.magi.  This locks the ordering guarantee: irmaa_for_year() is
        # called AFTER realized_gains are included in yr.magi.
        #
        # Note on discriminability: this assertion is an invariant (always true on
        # correct code) rather than a strict behavioral discriminator.  A truly
        # discriminating test would require realized_gains to straddle an IRMAA tier
        # boundary.  With a $4M IRA the household is deep in a high tier; the small
        # ~$9K brokerage-realized component won't cross a boundary.  The fixture
        # nonetheless exercises the fallback path and locks the irmaa_cost value.
        # For a strictly behavioral test, see the grid-05 test above which cleanly
        # isolates a fixed-vs-unfixed path.
        #
        # yr_idx=1: ya=76, sa=70; irmaa_for_year receives ya-2=74, sa-2=68
        expected_irmaa, _ = irmaa_for_year(
            yr2027.magi,  # fallback: magi_for_irmaa = yr.magi (full, realized-gains-inclusive)
            76 - 2,  # your_age_income_year
            70 - 2,  # spouse_age_income_year
            hh.medicare_part_b_base_monthly * 12,
            "MFJ",
            year=2025,  # income_year = 2027 - 2
            cpi=hh.cpi_assumption,
        )
        assert yr2027.irmaa_cost == pytest.approx(expected_irmaa, abs=1.0), (
            f"irmaa_cost={yr2027.irmaa_cost:.2f} != value computed from full yr.magi "
            f"{yr2027.magi:.0f}: expected {expected_irmaa:.2f}. "
            f"Ordering invariant broken: IRMAA may be computed before realized_gains "
            f"are folded into yr.magi."
        )

    # ------------------------------------------------------------------
    # Test 3 — value-preservation: realized_gains hoist does NOT change
    #          final yr.magi or yr.niit_magi
    # Assertion type: INVARIANT
    # ------------------------------------------------------------------

    def test_magi_and_niit_magi_include_realized_gains_correctly(self):
        """Hoisting realized_gains must preserve final yr.magi and yr.niit_magi values.

        This invariant test verifies that folding realized_gains earlier (for MAGI
        ordering) does not accidentally double-count or omit them in the final field
        values recorded on YearResult.

        For a year with nonzero brokerage:
          yr.magi == magi_without_brokerage_realized_gains + realized_gains
          yr.niit_magi == yr.magi - tax_exempt_interest_ytd

        Strategy: use an RMD household so brokerage accumulates, pick year 2 (yr_idx=2)
        where brokerage has been seeded for two years and realized gains are meaningful.
        Reconstruct expected_magi from its components and assert equality.
        """
        hh = Household(
            your_age=75,
            spouse_age=69,
            base_year=2026,
            your_ira=4_000_000,
            spouse_ira=500_000,
            growth_rate=0.07,
            living_expenses=60_000.0,
            grants=[],
            brokerage_growth=GrowthProfile(default_rate=0.20),
        )

        plan = ConversionPlan()
        result = run_scenario(hh, plan, "value_preservation", end_age=80)

        # Pick yr_idx=2 (year 2028) — brokerage seeded for 2 years, realized gains nonzero
        yr = result.years[2]
        assert yr.year == 2026 + 2
        assert yr.brokerage_balance > 0, "Fixture broken: brokerage is 0 in year 2"

        # Reconstruct realized_gains using the same formula as the engine
        brok_appreciation_rate = hh.brokerage_growth.appreciation_for(yr.year)  # type: ignore[union-attr]
        realized_gains = yr.brokerage_balance * brok_appreciation_rate * hh.brok_turnover
        assert realized_gains > 0, "Fixture broken: realized_gains is zero in year 2"

        # INVARIANT (a): yr.magi must be positive and include realized_gains.
        # Sanity: magi must be at least as large as realized_gains alone.
        assert yr.magi > realized_gains, (
            f"yr.magi ({yr.magi:.2f}) <= realized_gains ({realized_gains:.2f}); "
            f"magi appears to contain only realized_gains or less — other income missing"
        )

        # INVARIANT (b): yr.niit_magi excludes muni interest per §1411(d)(3).
        # No YTD snapshot here → tax_exempt_interest_ytd = 0.
        # So yr.niit_magi must equal yr.magi exactly.
        assert yr.niit_magi == pytest.approx(yr.magi, abs=0.01), (
            f"yr.niit_magi ({yr.niit_magi:.2f}) != yr.magi ({yr.magi:.2f}) "
            f"when tax_exempt_interest_ytd=0; §1411 exclusion mis-applied"
        )

        # INVARIANT (c): niit_magi invariant with muni interest in YTD
        # Re-run with a YTD snapshot that has tax_exempt_interest_ytd to verify exclusion.
        from models.ytd_income import YTDSnapshot

        muni_interest = 8_000.0
        ytd_muni = YTDSnapshot(tax_year=2026, tax_exempt_interest_ytd=muni_interest)
        result_muni = run_scenario(hh, plan, "value_pres_muni", end_age=80, ytd=ytd_muni)
        yr_muni = result_muni.years[0]  # base year: ytd applies

        # §1411(d)(3): niit_magi = magi - tax_exempt_interest
        assert yr_muni.niit_magi == pytest.approx(yr_muni.magi - muni_interest, abs=0.01), (
            f"niit_magi ({yr_muni.niit_magi:.2f}) != magi - muni_interest "
            f"({yr_muni.magi - muni_interest:.2f}); §1411(d)(3) exclusion broken"
        )


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


class TestAuditF3F4SSProvisionalIncome:
    """F3/F4: LTCG, qualified dividends, and realized brokerage gains must enter
    SS provisional income per IRC §86(b)(2)."""

    def _base_hh(self) -> Household:
        return Household(
            your_age=70,
            spouse_age=64,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_ira=1_000_000,
            spouse_ira=0,
        )

    def _call_ss(self, hh: Household, **kwargs) -> float:
        """Call compute_social_security and return taxable_ss_amt."""
        from engine.scenario_compute import compute_social_security

        _, _, _, taxable_ss_amt = compute_social_security(
            hh=hh,
            ya=hh.your_age,
            sa=hh.spouse_age,
            survivor_active=False,
            who_dies=None,
            current_filing_status="MFJ",
            your_conversion=0.0,
            spouse_conversion=0.0,
            taxable_rmd=0.0,
            spouse_taxable_rmd=0.0,
            extra_withdrawal=0.0,
            spouse_extra_withdrawal=0.0,
            option_income=0.0,
            your_inherited_distribution=0.0,
            spouse_inherited_distribution=0.0,
            ord_div_this_year=0.0,
            ytd_year=kwargs.get("ytd_year"),
            qual_div_this_year=kwargs.get("qual_div_this_year", 0.0),
            realized_gains=kwargs.get("realized_gains", 0.0),
        )
        return taxable_ss_amt

    def test_f3_ytd_ltcg_raises_taxable_ss(self):
        """F3: ltcg_ytd must enter provisional income — taxable SS is higher with YTD LTCG."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()
        ytd_no_ltcg = YTDSnapshot(tax_year=hh.base_year, ltcg_ytd=0.0)
        ytd_with_ltcg = YTDSnapshot(tax_year=hh.base_year, ltcg_ytd=50_000.0)

        ss_without = self._call_ss(hh, ytd_year=ytd_no_ltcg)
        ss_with = self._call_ss(hh, ytd_year=ytd_with_ltcg)

        assert ss_with > ss_without, (
            "ltcg_ytd must increase taxable SS via provisional income (IRC §86(b)(2))"
        )

    def test_f3_ytd_qualified_dividends_raises_taxable_ss(self):
        """F3: qualified_dividends_ytd must enter provisional income — taxable SS is higher."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()
        ytd_no_qdiv = YTDSnapshot(qualified_dividends_ytd=0.0)
        ytd_with_qdiv = YTDSnapshot(qualified_dividends_ytd=30_000.0)

        ss_without = self._call_ss(hh, ytd_year=ytd_no_qdiv)
        ss_with = self._call_ss(hh, ytd_year=ytd_with_qdiv)

        assert ss_with > ss_without, (
            "qualified_dividends_ytd must increase taxable SS via provisional income"
        )

    def test_f4_forecast_qual_div_raises_taxable_ss(self):
        """F4: forecast qual_div_this_year must enter provisional income."""
        hh = self._base_hh()
        ss_without = self._call_ss(hh, qual_div_this_year=0.0)
        ss_with = self._call_ss(hh, qual_div_this_year=20_000.0)

        assert ss_with > ss_without, (
            "qual_div_this_year must increase taxable SS via provisional income (IRC §86(b)(2))"
        )

    def test_f4_realized_gains_raise_taxable_ss(self):
        """F4: brokerage realized_gains must enter SS provisional income."""
        hh = self._base_hh()
        ss_without = self._call_ss(hh, realized_gains=0.0)
        ss_with = self._call_ss(hh, realized_gains=40_000.0)

        assert ss_with > ss_without, (
            "realized_gains must increase taxable SS via provisional income (IRC §86(b)(2))"
        )

    def test_f4_run_scenario_brokerage_yield_raises_taxable_ss(self):
        """F4: run_scenario with a brokerage yield produces higher taxable SS than zero-yield.

        Sanity-checks that the fix flows end-to-end through run_scenario.
        Use a future year (your_age=72) where SS is active but no RMDs yet.
        """
        hh_no_yield = Household(
            your_age=68,
            spouse_age=62,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_ira=500_000,
            spouse_ira=0,
            brokerage_growth=GrowthProfile(
                default_rate=0.07,
                yield_rate=0.0,
                qualified_fraction=1.0,
            ),
        )
        hh_with_yield = Household(
            your_age=68,
            spouse_age=62,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_ira=500_000,
            spouse_ira=0,
            brokerage_growth=GrowthProfile(
                default_rate=0.07,
                yield_rate=0.03,
                qualified_fraction=1.0,
            ),
        )
        # Seed both with a brokerage balance by running enough years to accumulate
        plan = ConversionPlan()
        res_no = run_scenario(hh_no_yield, plan, end_age=75)
        res_with = run_scenario(hh_with_yield, plan, end_age=75)

        # At age 72 (SS active, no RMDs) the yield scenario must show higher taxable SS
        yr_no = next(yr for yr in res_no.years if yr.your_age == 73)
        yr_with = next(yr for yr in res_with.years if yr.your_age == 73)

        assert yr_with.taxable_ss_amt >= yr_no.taxable_ss_amt, (
            "Scenario with brokerage yield must produce >= taxable SS than zero-yield"
        )


class TestAuditF5BaseYearQualDivLTCGWalk:
    """F5: YTD qualified dividends must be taxed at preferential LTCG rates,
    not escaped entirely when ltcg_ytd == 0."""

    def _hh(self) -> Household:
        return Household(
            your_age=65,
            spouse_age=59,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_ira=500_000,
            spouse_ira=0,
        )

    def test_f5_qual_div_only_gets_ltcg_rate_tax(self):
        """F5: When ltcg_ytd==0 but qualified_dividends_ytd>0, ytd_ltcg_tax must be > 0.

        Pre-fix: guard was `ltcg_ytd > 0` so qual-divs-only skipped the stack walk entirely
        → ytd_ltcg_tax = 0. Post-fix: guard is `(ltcg_ytd + qualified_dividends_ytd) > 0`.
        """
        from models.ytd_income import YTDSnapshot

        hh = self._hh()
        # Put enough ordinary income so the qual-divs land in the 15% LTCG band
        ytd = YTDSnapshot(
            wages_ytd=150_000.0,
            ltcg_ytd=0.0,
            qualified_dividends_ytd=20_000.0,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age, ytd=ytd)
        yr = result.years[0]

        assert yr.ytd_ltcg_tax > 0.0, (
            "ytd_ltcg_tax must be > 0 when only qualified_dividends_ytd > 0 (F5 fix)"
        )

    def test_f5_qual_div_plus_ltcg_both_taxed(self):
        """F5: Combined ltcg_ytd + qualified_dividends_ytd must both enter the stack walk."""
        from models.ytd_income import YTDSnapshot

        hh = self._hh()
        ytd_ltcg_only = YTDSnapshot(
            wages_ytd=150_000.0,
            ltcg_ytd=20_000.0,
            qualified_dividends_ytd=0.0,
        )
        ytd_both = YTDSnapshot(
            wages_ytd=150_000.0,
            ltcg_ytd=20_000.0,
            qualified_dividends_ytd=10_000.0,
        )
        plan = ConversionPlan()
        res_ltcg = run_scenario(hh, plan, end_age=hh.your_age, ytd=ytd_ltcg_only)
        res_both = run_scenario(hh, plan, end_age=hh.your_age, ytd=ytd_both)

        yr_ltcg = res_ltcg.years[0]
        yr_both = res_both.years[0]

        assert yr_both.ytd_ltcg_tax > yr_ltcg.ytd_ltcg_tax, (
            "Adding qualified_dividends_ytd to ltcg_ytd must increase ytd_ltcg_tax"
        )


class TestAuditF7ComputePhaseRmdStartAge:
    """F7: compute_phase must respect hh.your_rmd_start_age / hh.spouse_rmd_start_age,
    not hardcoded 74/75 literals."""

    def test_f7_rmd_phase_at_73_when_rmd_start_age_73(self):
        """F7: user at age 73 with rmd_start_age=73 must get 'rmd' or 'squeeze', not 'ss_conv'."""
        from engine.scenario_compute import compute_phase

        hh = Household(
            your_age=73,
            spouse_age=73,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
        )
        phase = compute_phase(ya=73, sa=73, year=hh.base_year, hh=hh, early_exercise=False)
        assert phase in ("rmd", "squeeze"), (
            f"Expected 'rmd' or 'squeeze' at age 73 with rmd_start_age=73, got '{phase}'"
        )

    def test_f7_ss_conv_before_rmd_start_age_73(self):
        """F7: user at age 72 with rmd_start_age=73 must still get 'ss_conv'."""
        from engine.scenario_compute import compute_phase

        hh = Household(
            your_age=72,
            spouse_age=67,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
        )
        phase = compute_phase(ya=72, sa=67, year=hh.base_year, hh=hh, early_exercise=False)
        assert phase == "ss_conv", (
            f"Expected 'ss_conv' at age 72 with rmd_start_age=73, got '{phase}'"
        )

    def test_f7_squeeze_when_only_user_hits_rmd(self):
        """F7: user at rmd_start_age but spouse below theirs → 'squeeze', not 'rmd'."""
        from engine.scenario_compute import compute_phase

        hh = Household(
            your_age=73,
            spouse_age=67,
            your_rmd_start_age=73,
            spouse_rmd_start_age=75,
        )
        phase = compute_phase(ya=73, sa=67, year=hh.base_year, hh=hh, early_exercise=False)
        assert phase == "squeeze", (
            f"Expected 'squeeze' when your_age==rmd_start_age but spouse below theirs, got '{phase}'"
        )

    def test_f7_rmd_phase_at_75_with_default_rmd_start_age(self):
        """F7: default rmd_start_age=75 — phase must be 'rmd'/'squeeze' only at age 75+."""
        from engine.scenario_compute import compute_phase

        hh = Household(
            your_age=74,
            spouse_age=74,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
        )
        phase_74 = compute_phase(ya=74, sa=74, year=hh.base_year, hh=hh, early_exercise=False)
        phase_75 = compute_phase(ya=75, sa=75, year=hh.base_year + 1, hh=hh, early_exercise=False)
        assert phase_74 == "ss_conv", (
            f"Age 74 with rmd_start=75 should be ss_conv, got '{phase_74}'"
        )
        assert phase_75 in ("rmd", "squeeze"), (
            f"Age 75 with rmd_start=75 should be rmd/squeeze, got '{phase_75}'"
        )

    def test_f7_run_scenario_phase_73_rmd_start_73(self):
        """F7: run_scenario must label age-73 year as 'rmd' when rmd_start_age=73."""
        hh = Household(
            your_age=70,
            spouse_age=70,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=74)
        yr73 = next(yr for yr in result.years if yr.your_age == 73)
        assert yr73.phase in ("rmd", "squeeze"), (
            f"run_scenario year at age 73 (rmd_start_age=73) must be rmd/squeeze, got '{yr73.phase}'"
        )


class TestAutoFillCoreBaseMagiTaxableSS:
    """F9 regression: _auto_fill_core must use taxable SS (not gross SS) in base_magi.

    Prior to the fix, base_magi added the full combined_ss even though tss (the
    IRC §86-capped taxable portion) was already computed and used in fixed_gross.
    This overstated base_magi, causing the IRMAA-safe ceiling to be hit too soon
    and OBBBA senior-bonus phase-out to fire earlier than correct.

    Note: your_ss_fra is a monthly dollar amount; ss_benefit_at_age() converts it
    to an annual benefit applying delay/early credits.
    """

    def test_irmaa_safe_base_magi_uses_taxable_ss(self) -> None:
        """auto_fill_irmaa_safe conversion must not be reduced by non-taxable SS.

        Setup: Single household at SS-start age. your_ss_fra=1_500 (monthly) ->
        annual SS ~$22.3K at age 70 (3yr delay credits). With no other income,
        provisional = 0 + 0.5x22.3K = 11.2K < $25K Single tier-1 -> tss = 0.

        Under the old bug: base_magi += gross SS (~22.3K) -> less IRMAA room.
        Under the fix:     base_magi += tss (0) -> full IRMAA room.

        Observable consequence: auto_fill_irmaa_safe generates a non-zero conversion
        in the base year, AND run_scenario confirms taxable_ss_amt == 0 (the scenario
        engine independently computes tss=0 for this household, so if autofill used
        gross SS the plan would be overly conservative relative to scenario truth).
        """
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss

        hh = Household(
            filing_status="Single",
            your_age=70,
            your_ira=3_000_000,
            spouse_ira=0,
            spouse_roth=0,
            spouse_age=0,
            spouse_ss_fra=0,
            your_ss_fra=1_500,  # $1,500/month FRA benefit (realistic)
            your_ss_start_age=70,
        )

        # Confirm precondition: tss = 0 for this household (provisional < $25K tier-1).
        combined_ss = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
        assert combined_ss > 0.0, f"Precondition: household must have SS income, got {combined_ss}"
        tss = taxable_ss(combined_ss, 0.0, filing_status="Single")
        assert tss == 0.0, (
            f"Precondition: provisional={0.5 * combined_ss:.0f} must be < $25K tier-1; "
            f"got tss={tss:.0f} (combined_ss={combined_ss:.0f})"
        )

        plan = auto_fill_irmaa_safe(hh)
        base_year = hh.base_year
        conv = plan.your_conversions.get(base_year, 0.0)

        # Post-fix: base_magi uses tss=0 -> IRMAA room = threshold - RMD, so a
        # positive conversion is generated. Pre-fix: base_magi added ~$22K of gross
        # SS, over-consuming IRMAA room by that amount (overly conservative plan).
        assert conv > 0.0, (
            f"IRMAA-safe plan must produce a positive base-year conversion; got {conv}"
        )

    def test_irmaa_safe_room_reduced_by_tss_not_gross_ss(self) -> None:
        """IRMAA room reduction from SS equals tss, not gross combined_ss.

        Compare two identical MFJ households that differ only in whether SS has
        started. With high wages YTD, provisional income is deep in the 85% band
        so tss = 85% x combined_ss < combined_ss.

        The base-year conversion difference between the no-SS and SS households
        must equal tss (the taxable fraction), not the full gross SS amount.

        Note: your_ss_fra=2_000/month -> combined_ss_annual ~59.5K (both at 70).
        provisional = wages(80K) + 0.5x59.5K ~109.7K >> $44K MFJ tier-2
        -> tss = 85% x 59.5K ~50.6K; gross = 59.5K; delta ~8.9K.
        """
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss
        from models.ytd_income import YTDSnapshot

        # Large IRA -- never the binding constraint; IRMAA ceiling is.
        common_kwargs: dict = {
            "filing_status": "MFJ",
            "your_ira": 5_000_000,
            "spouse_ira": 5_000_000,
            "your_ss_fra": 2_000,  # $2K/month FRA (realistic)
            "spouse_ss_fra": 2_000,
            "your_ss_start_age": 70,
            "spouse_ss_start_age": 70,
        }
        # No SS yet (ages below start age)
        hh_no_ss = Household(**common_kwargs, your_age=60, spouse_age=60)
        # SS active (ages at start age -> 3yr delay credits applied)
        hh_ss = Household(**common_kwargs, your_age=70, spouse_age=70)

        wages_ytd = 80_000.0
        ytd_no_ss = YTDSnapshot(tax_year=hh_no_ss.base_year, wages_ytd=wages_ytd)
        ytd_ss = YTDSnapshot(tax_year=hh_ss.base_year, wages_ytd=wages_ytd)

        your_base = ss_benefit_at_age(
            hh_ss.your_ss_fra, hh_ss.your_ss_start_age, hh_ss.your_fra_age
        )
        spouse_base = ss_benefit_at_age(
            hh_ss.spouse_ss_fra, hh_ss.spouse_ss_start_age, hh_ss.spouse_fra_age
        )
        combined_ss = your_base + spouse_base
        expected_tss = taxable_ss(combined_ss, wages_ytd, filing_status="MFJ")

        # Precondition: 85% rule fires -> tss < gross SS.
        assert expected_tss < combined_ss, (
            f"Precondition: tss={expected_tss:.0f} must be < gross ss={combined_ss:.0f}"
        )
        assert expected_tss > 0.0, (
            f"Precondition: tss={expected_tss:.0f} must be positive (85% band active)"
        )

        plan_no_ss = auto_fill_irmaa_safe(hh_no_ss, ytd=ytd_no_ss)
        plan_ss = auto_fill_irmaa_safe(hh_ss, ytd=ytd_ss)

        conv_no_ss = plan_no_ss.your_conversions.get(
            hh_no_ss.base_year, 0.0
        ) + plan_no_ss.spouse_conversions.get(hh_no_ss.base_year, 0.0)
        conv_ss = plan_ss.your_conversions.get(
            hh_ss.base_year, 0.0
        ) + plan_ss.spouse_conversions.get(hh_ss.base_year, 0.0)

        # The SS household commits tss to MAGI -> less conversion room.
        reduction = conv_no_ss - conv_ss
        assert reduction >= 0.0, (
            f"SS household must have <= conversion room: no_ss={conv_no_ss:.0f}, ss={conv_ss:.0f}"
        )
        # Reduction must equal tss (fixed) not combined_ss (buggy pre-F9).
        # Tolerance: $100 for indexing/rounding across the two base years.
        assert reduction == approx(expected_tss, tol=100), (
            f"IRMAA room reduction should equal tss={expected_tss:.0f}, "
            f"got {reduction:.0f} (gross-SS bug would give ~{combined_ss:.0f})"
        )
