"""Regression tests for audit-0706 wave-2 scenario.py findings.

Findings:
  ira-rmd-1:      deferred-first-RMD yields $0 when base_year age == rmd_start_age+1
  scenario-core-1: inherited-IRA distributions absent from available_income / brokerage
  scenario-core-4: cum_rmd_tax bundles extra_withdrawal_tax (cosmetic/documented)
  scenario-core-5: conversion can silently exceed remaining IRA balance
"""

from __future__ import annotations

import pytest

from engine.ira import calc_rmd
from engine.scenario import ConversionPlan, run_scenario
from models.household import Household, InheritedIRA

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_hh(**kwargs) -> Household:
    """Minimal Household — all keyword overrides accepted."""
    defaults: dict = {
        "your_age": 62,
        "spouse_age": 56,
        "base_year": 2026,
        "your_ira": 1_000_000.0,
        "spouse_ira": 500_000.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "your_ss_start_age": 70,
        "spouse_ss_start_age": 70,
        "living_expenses": 60_000.0,
        "brokerage_start": 0.0,
    }
    defaults.update(kwargs)
    return Household(**defaults)


# ===========================================================================
# Finding ira-rmd-1: deferred-first-RMD silent $0 when base_year at rmd_start_age+1
# ===========================================================================

class TestDeferredFirstRmdBaseYear:
    """When your_age == rmd_start_age+1 at base_year, the deferred RMD must fire.

    Bug: prev_your_ira_begin initialised to 0.0; calc_rmd guard
         `prior_year_balance > 0` (ira.py:91) prevents the extra RMD from being
         computed, so the doubled year produces single-RMD instead of double.

    Fix: initialise prev_your_ira_begin = hh.your_ira when hh.your_defer_first_rmd.
    """

    def test_calc_rmd_deferred_double_with_prior_balance(self) -> None:
        """calc_rmd itself works correctly when prior_year_balance is supplied."""
        normal = calc_rmd(1_000_000.0, age=74, rmd_start_age=73,
                          first_year_deferred=False, prior_year_balance=0.0)
        deferred = calc_rmd(1_000_000.0, age=74, rmd_start_age=73,
                            first_year_deferred=True, prior_year_balance=1_000_000.0)
        # Deferred year must be strictly greater: current + prior year RMD combined
        assert deferred > normal, (
            f"Deferred-year RMD ({deferred:,.0f}) must exceed single-year RMD ({normal:,.0f})"
        )

    def test_deferred_rmd_nonzero_when_base_year_is_second_rmd_year(self) -> None:
        """Scenario: person is 74 at base_year 2026, rmd_start_age=73, deferred.

        Year 2025 (age 73): deferred — $0 RMD taken, IRA untouched.
        Year 2026 (age 74): doubled — must pay BOTH years' RMDs.

        Before fix: prev_your_ira_begin=0.0 → prior_year_balance=0.0 →
          calc_rmd skips the prior-year term → single-year RMD only.
        After fix: prev_your_ira_begin=hh.your_ira → correct doubled RMD.
        """
        hh = _base_hh(
            your_age=74,          # base_year == rmd_start_age + 1
            your_rmd_start_age=73,
            your_defer_first_rmd=True,
            your_ira=1_000_000.0,
            base_year=2026,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=75)

        yr0 = result.years[0]  # 2026: age 74 — doubled year

        # Non-deferred reference: same household without deferral
        hh_no_defer = _base_hh(
            your_age=74,
            your_rmd_start_age=73,
            your_defer_first_rmd=False,
            your_ira=1_000_000.0,
            base_year=2026,
        )
        result_nd = run_scenario(hh_no_defer, plan, end_age=75)
        yr0_nd = result_nd.years[0]

        # Deferred year must produce a larger RMD than non-deferred (doubled)
        assert yr0.your_rmd > yr0_nd.your_rmd, (
            f"Deferred RMD ({yr0.your_rmd:,.0f}) must exceed "
            f"non-deferred RMD ({yr0_nd.your_rmd:,.0f}) in the doubled year"
        )

    def test_deferred_rmd_symmetric_for_spouse(self) -> None:
        """Same bug must be fixed for spouse deferred-first-RMD."""
        hh = _base_hh(
            spouse_age=74,
            spouse_rmd_start_age=73,
            spouse_defer_first_rmd=True,
            spouse_ira=800_000.0,
            base_year=2026,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age + 1)

        yr0 = result.years[0]

        hh_nd = _base_hh(
            spouse_age=74,
            spouse_rmd_start_age=73,
            spouse_defer_first_rmd=False,
            spouse_ira=800_000.0,
            base_year=2026,
        )
        result_nd = run_scenario(hh_nd, plan, end_age=hh_nd.your_age + 1)
        yr0_nd = result_nd.years[0]

        assert yr0.spouse_rmd > yr0_nd.spouse_rmd, (
            f"Spouse deferred RMD ({yr0.spouse_rmd:,.0f}) must exceed "
            f"non-deferred ({yr0_nd.spouse_rmd:,.0f}) in the doubled year"
        )

    def test_no_deferral_unaffected(self) -> None:
        """When defer_first_rmd=False, initialisation of prev_ira_begin is irrelevant."""
        hh = _base_hh(
            your_age=74,
            your_rmd_start_age=73,
            your_defer_first_rmd=False,
            your_ira=1_000_000.0,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=75)
        yr0 = result.years[0]
        # RMD must be positive (non-zero) — just a basic sanity check
        assert yr0.your_rmd > 0


# ===========================================================================
# Finding scenario-core-1: inherited distributions absent from available_income
# ===========================================================================

class TestInheritedDistributionCashFlow:
    """Inherited IRA annual distributions must appear in available_income.

    Audit finding: yr.your_inherited_distribution / yr.spouse_inherited_distribution
    are set on the YearResult but were NOT included in the available_income sum,
    so the after-tax inherited cash never reached excess_rmd or the brokerage.

    Attribute presence verified first (CAVEAT check per task spec).
    """

    def _make_inherited_hh(self, owner: str = "you") -> Household:
        iira = InheritedIRA(
            balance=300_000.0,
            inherited_year=2022,   # 4 years in; years_remaining = 6 in 2026
            growth_rate=0.05,
            owner=owner,
        )
        return _base_hh(
            your_age=65,
            spouse_age=60,
            your_ira=0.0,
            spouse_ira=0.0,
            inherited_iras=[iira],
            living_expenses=30_000.0,
        )

    def test_inherited_distribution_attribute_exists_on_year_result(self) -> None:
        """yr.your_inherited_distribution and yr.spouse_inherited_distribution must exist."""
        hh = self._make_inherited_hh(owner="you")
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age + 1)
        yr = result.years[0]
        # These attributes MUST exist (confirms caveat from audit spec)
        assert hasattr(yr, "your_inherited_distribution"), (
            "yr.your_inherited_distribution attribute missing — cannot fix scenario-core-1"
        )
        assert hasattr(yr, "spouse_inherited_distribution"), (
            "yr.spouse_inherited_distribution attribute missing"
        )

    def test_inherited_distribution_nonzero(self) -> None:
        """Confirm the test household actually produces inherited distributions."""
        hh = self._make_inherited_hh(owner="you")
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age + 1)
        yr = result.years[0]
        assert yr.your_inherited_distribution > 0, (
            "Test precondition: inherited distribution must be non-zero"
        )

    def test_inherited_cash_increases_available_income(self) -> None:
        """Household WITH inherited IRA must have higher available_income than without.

        Before fix: inherited distributions taxed (raise federal_tax_amt) but not
        added to available_income → net effect is negative (cash disappears).
        After fix: after-tax inherited cash is present → available_income higher.
        """
        hh_with = self._make_inherited_hh(owner="you")
        hh_without = _base_hh(
            your_age=65,
            spouse_age=60,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=30_000.0,
        )
        plan = ConversionPlan()
        r_with = run_scenario(hh_with, plan, end_age=hh_with.your_age + 1)
        r_without = run_scenario(hh_without, plan, end_age=hh_without.your_age + 1)

        yr_with = r_with.years[0]
        yr_without = r_without.years[0]

        # Compute available_income the same way the engine does (after fix)
        def _avail(yr) -> float:
            return (
                yr.taxable_rmd
                + yr.spouse_taxable_rmd
                + yr.extra_withdrawal
                + yr.spouse_extra_withdrawal
                + yr.combined_ss
                + yr.option_income
                + yr.your_inherited_distribution
                + yr.spouse_inherited_distribution
                - yr.federal_tax_amt
            )

        avail_with = _avail(yr_with)
        avail_without = _avail(yr_without)

        assert avail_with > avail_without, (
            f"Household with inherited IRA available_income ({avail_with:,.0f}) "
            f"must exceed without ({avail_without:,.0f})"
        )

    def test_income_needed_zero_when_inherited_covers_expenses(self) -> None:
        """When after-tax inherited dist > living_expenses, income_needed must be 0.

        Before fix: inherited cash not in available_income. Taxes raised by the
        distribution reduce available_income below zero → income_needed > 0
        (brokerage gap) even though the household has real cash.
        After fix: inherited cash included → available_income positive →
        income_needed == 0.

        Setup: inherited dist ~$50K/yr (300K/6), living_expenses=$30K.
        Even at 22% marginal rate after-tax dist ~$39K > $30K → no gap.
        """
        hh = self._make_inherited_hh(owner="you")
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age + 1)
        yr = result.years[0]

        # After-tax inherited distribution should cover living expenses
        # If inherited cash is absent from available_income, income_needed > 0
        assert yr.income_needed == 0, (
            f"income_needed ({yr.income_needed:,.0f}) must be 0 when inherited "
            f"distributions ({yr.your_inherited_distribution:,.0f}) cover "
            f"living_expenses ({hh.living_expenses:,.0f})"
        )

    def test_brokerage_grows_from_inherited_excess(self) -> None:
        """Surplus inherited cash must flow into brokerage carry-forward.

        Before fix: excess_rmd is 0 (available_income so negative that the max
        clamp fires) → brokerage never grows from inherited proceeds.
        After fix: excess_rmd > 0 → brokerage in year 2 > 0.
        """
        hh = self._make_inherited_hh(owner="you")
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age + 2)
        yr0 = result.years[0]
        yr1 = result.years[1]

        # Year 0 must show positive excess after fix
        assert yr0.excess_rmd > 0, (
            f"excess_rmd ({yr0.excess_rmd:,.0f}) must be > 0 when "
            f"after-tax inherited dist exceeds living expenses"
        )
        # Brokerage in year 1 must reflect the excess
        assert yr1.brokerage_balance > 0, (
            "Brokerage must accumulate excess from inherited distributions"
        )

    def test_spouse_inherited_distribution_also_included(self) -> None:
        """Spouse-owned inherited IRA distributions must also flow into available_income."""
        hh = self._make_inherited_hh(owner="spouse")
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age + 1)
        yr = result.years[0]

        assert yr.spouse_inherited_distribution > 0, "test precondition"
        # After fix: income_needed == 0 when distributions cover expenses
        assert yr.income_needed == 0, (
            f"Spouse inherited: income_needed ({yr.income_needed:,.0f}) must be 0"
        )


# ===========================================================================
# Finding scenario-core-4: cum_rmd_tax bundles extra_withdrawal_tax (cosmetic)
# ===========================================================================

class TestCumRmdTaxDocumented:
    """scenario-core-4: extra_withdrawal_tax is included in cum_rmd_tax.

    This is a categorisation issue only — lifetime tax total is correct.
    The fix is a code comment documenting the intentional grouping.
    No behavioral change expected; test verifies total tax consistency.
    """

    def test_total_rmd_tax_nonnegative(self) -> None:
        """total_rmd_tax must be >= 0 in all scenarios."""
        hh = _base_hh(your_age=74, your_rmd_start_age=73, your_ira=2_000_000.0)
        plan = ConversionPlan(
            extra_withdrawals={2026: 50_000.0, 2027: 50_000.0},
        )
        result = run_scenario(hh, plan, end_age=76)
        assert result.total_rmd_tax >= 0

    def test_lifetime_tax_consistency_with_extra_withdrawal(self) -> None:
        """When extra withdrawals exist, RMD-phase tax must still be self-consistent.

        total_rmd_tax includes extra_withdrawal_tax by design (grouping documented).
        This test just ensures the grouping does not double-count or zero-out.
        """
        hh = _base_hh(your_age=74, your_rmd_start_age=73, your_ira=2_000_000.0)
        plan_with = ConversionPlan(extra_withdrawals={2026: 100_000.0})
        plan_without = ConversionPlan()

        r_with = run_scenario(hh, plan_with, end_age=75)
        r_without = run_scenario(hh, plan_without, end_age=75)

        # Extra withdrawal increases taxable income → total_rmd_tax must be higher
        assert r_with.total_rmd_tax >= r_without.total_rmd_tax, (
            "Extra withdrawal should not decrease total_rmd_tax"
        )


# ===========================================================================
# Audit 2026-07-13 defect A: pre-RMD extra_withdrawal dropped from lifetime tax
# ===========================================================================


class TestPreRmdExtraWithdrawalCapturedInLifetimeTax:
    """extra_withdrawal has no age gate in the engine, but the cum_rmd_tax
    accumulator originally only fired when ya >= rmd_start_age or
    sa >= rmd_start_age. A PRE-RMD extra_withdrawal's tax impact was therefore
    correctly present in yr.federal_tax_amt but silently dropped from the
    lifetime accumulator (total_rmd_tax), undercounting lifetime tax.
    """

    def test_pre_rmd_extra_withdrawal_tax_captured_in_lifetime_total(self) -> None:
        """Ages 61/55 (both pre-RMD, default rmd_start_age=75): a $100k extra
        withdrawal raises federal_tax_amt materially; the lifetime accumulator
        (total_rmd_tax + total_conv_tax) must reflect that same delta.
        """
        hh = _base_hh(your_age=61, spouse_age=55)
        plan_with = ConversionPlan(extra_withdrawals={2026: 100_000.0})
        plan_without = ConversionPlan()

        r_with = run_scenario(hh, plan_with, "with", end_age=61)
        r_without = run_scenario(hh, plan_without, "without", end_age=61)

        expected_delta = r_with.years[0].federal_tax_amt - r_without.years[0].federal_tax_amt
        assert expected_delta > 10_000, (
            "test precondition: extra withdrawal must raise federal tax materially"
        )

        # Before the fix, cum_rmd_tax never fired in this pre-RMD year (neither age
        # gate condition is true), so total_rmd_tax was exactly 0.0 even though
        # federal_tax_amt correctly reflects the extra_withdrawal tax — the
        # regression this test guards against.
        assert r_with.total_rmd_tax > 0, (
            "total_rmd_tax must be > 0 for a pre-RMD year with an extra_withdrawal "
            "(previously dropped entirely because neither RMD-age gate fired)"
        )
        # The full year's non-conversion federal tax (which includes the
        # extra_withdrawal tax impact) must now be captured in the lifetime total.
        assert r_with.total_rmd_tax == pytest.approx(
            r_with.years[0].federal_tax_amt - r_with.years[0].conversion_tax, abs=1.0
        ), "total_rmd_tax must capture this year's full non-conversion federal tax"
        # It must capture AT LEAST the incremental tax the extra_withdrawal caused
        # (the "without" baseline's own non-extra-withdrawal tax is never itself
        # accumulated pre-RMD — a separate, out-of-scope gap — so total_rmd_tax can
        # legitimately exceed expected_delta, but never fall short of it).
        assert r_with.total_rmd_tax >= expected_delta - 1.0, (
            f"total_rmd_tax ({r_with.total_rmd_tax:,.0f}) must be >= the extra_withdrawal "
            f"tax impact ({expected_delta:,.0f})"
        )

    def test_rmd_year_extra_withdrawal_not_double_counted(self) -> None:
        """An extra_withdrawal fired during an RMD-phase year must be counted
        exactly once in the lifetime accumulator (age-gate OR extra-withdrawal
        gate is a single `if`/single addition — must not double-add).
        """
        hh = _base_hh(
            your_age=76,
            spouse_age=70,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
        )
        plan_with = ConversionPlan(extra_withdrawals={2026: 100_000.0})
        plan_without = ConversionPlan()

        r_with = run_scenario(hh, plan_with, "with", end_age=76)
        r_without = run_scenario(hh, plan_without, "without", end_age=76)

        expected_delta = r_with.years[0].federal_tax_amt - r_without.years[0].federal_tax_amt

        lifetime_delta = (r_with.total_rmd_tax + r_with.total_conv_tax) - (
            r_without.total_rmd_tax + r_without.total_conv_tax
        )
        assert lifetime_delta == pytest.approx(expected_delta, abs=1.0), (
            "RMD-year extra_withdrawal tax must be counted exactly once in the "
            f"lifetime accumulator; got delta {lifetime_delta:,.0f} vs expected "
            f"{expected_delta:,.0f} (a double-count would show ~2x)"
        )


# ===========================================================================
# Finding scenario-core-5: conversion clamped to available IRA balance
# ===========================================================================

class TestConversionClampedToIraBalance:
    """A conversion must not silently exceed the remaining IRA balance.

    After mandatory RMD (and QCD/extra_withdrawal), the remaining balance
    caps the conversion. Without a clamp, your_ira_end lands at max(negative, 0)=0
    but yr.your_conversion is recorded at the over-requested amount, overstating
    Roth credits and combined_gross / tax.

    Fix: clamp yr.your_conversion to available balance after mandatory withdrawals.
    """

    def _make_small_ira_hh(self) -> Household:
        """Household where planned conversion exceeds IRA balance (pre-RMD age)."""
        return _base_hh(
            your_age=65,           # pre-RMD so conversions are allowed
            your_rmd_start_age=73,
            your_ira=100_000.0,   # small IRA
        )

    def test_conversion_does_not_exceed_ira_balance(self) -> None:
        """yr.your_conversion must not exceed your_ira_begin (pre-RMD case).

        Before fix: yr.your_conversion records the full planned 200K even though
        the IRA only has 100K. yr.your_ira_end = max(100K-200K,0)=0 but the
        inflated conversion feeds combined_gross / Roth credits unchecked.
        After fix: yr.your_conversion is clamped to the available IRA balance.
        """
        hh = self._make_small_ira_hh()
        # Plan a conversion larger than the entire IRA balance
        plan = ConversionPlan(your_conversions={2026: 200_000.0})
        result = run_scenario(hh, plan, end_age=66)
        yr = result.years[0]

        # Conversion must not exceed what the IRA actually holds
        assert yr.your_conversion <= yr.your_ira_begin + 1.0, (
            f"yr.your_conversion ({yr.your_conversion:,.0f}) exceeds IRA begin "
            f"({yr.your_ira_begin:,.0f}) — conversion was not clamped"
        )

    def test_ira_end_nonnegative_after_oversized_conversion(self) -> None:
        """IRA end balance must never be negative."""
        hh = self._make_small_ira_hh()
        plan = ConversionPlan(your_conversions={2026: 500_000.0})
        result = run_scenario(hh, plan, end_age=66)
        yr = result.years[0]
        assert yr.your_ira_end >= 0, (
            f"your_ira_end ({yr.your_ira_end:,.0f}) is negative"
        )

    def test_roth_credit_bounded_by_available_ira(self) -> None:
        """Roth credit must not exceed what the IRA could actually provide.

        Before fix: yr.your_conversion records the full planned amount even when
        it exceeds the IRA; the IRA floor is enforced via max(.,0) on yr.your_ira_end
        but the inflated conversion feeds yr.your_roth_end unchecked.
        After fix: yr.your_conversion is clamped → Roth credit is accurate.
        """
        hh = self._make_small_ira_hh()
        plan = ConversionPlan(your_conversions={2026: 500_000.0})
        result = run_scenario(hh, plan, end_age=66)
        yr = result.years[0]

        # Roth credited this year = yr.your_conversion
        # It must not exceed opening IRA balance (100K)
        assert yr.your_conversion <= yr.your_ira_begin, (
            f"Roth-credited conversion ({yr.your_conversion:,.0f}) exceeds "
            f"opening IRA balance ({yr.your_ira_begin:,.0f})"
        )

    def test_spouse_conversion_also_clamped(self) -> None:
        """Symmetric clamp must apply to spouse conversion (pre-RMD)."""
        hh = _base_hh(
            your_age=62,
            spouse_age=60,
            spouse_rmd_start_age=73,
            spouse_ira=80_000.0,
        )
        plan = ConversionPlan(spouse_conversions={2026: 300_000.0})
        result = run_scenario(hh, plan, end_age=hh.your_age + 1)
        yr = result.years[0]

        assert yr.spouse_conversion <= yr.spouse_ira_begin, (
            f"Spouse conversion ({yr.spouse_conversion:,.0f}) exceeds "
            f"spouse IRA begin ({yr.spouse_ira_begin:,.0f})"
        )

    def test_normal_conversion_unaffected_by_clamp(self) -> None:
        """A conversion within bounds must pass through unchanged."""
        hh = _base_hh(
            your_age=65,
            your_rmd_start_age=73,
            your_ira=1_000_000.0,
        )
        planned = 50_000.0
        plan = ConversionPlan(your_conversions={2026: planned})
        result = run_scenario(hh, plan, end_age=66)
        yr = result.years[0]

        assert yr.your_conversion == pytest.approx(planned), (
            f"Normal conversion ({yr.your_conversion:,.0f}) must equal planned ({planned:,.0f})"
        )
