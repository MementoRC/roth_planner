"""Cluster-D fixes (2026-06-29 audit): auto-fill inherited-IRA room (M4) and the
survivor-year bracket ceiling in add_bracket_fill_withdrawals (U2)."""

from engine.scenario import ConversionPlan, run_scenario
from engine.scenario_autofill import add_bracket_fill_withdrawals, auto_fill_22
from engine.tax import BRACKETS_SINGLE
from engine.tax_indexing import index_value
from models.household import Household, InheritedIRA, SurvivorScenario


class TestAutofillInheritedIraRoom:
    """M4: SECURE-Act inherited-IRA distributions are ordinary income and must
    reduce the conversion room auto-fill allocates (else it over-converts)."""

    def test_inherited_ira_reduces_conversion_room(self):
        base = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        with_iira = Household(
            your_age=61,
            spouse_age=55,
            your_ira=1_700_000,
            spouse_ira=1_700_000,
            inherited_iras=[
                InheritedIRA(balance=500_000.0, inherited_year=base.base_year, owner="you")
            ],
        )

        def _total(plan):
            return sum(plan.your_conversions.values()) + sum(plan.spouse_conversions.values())

        total_base = _total(auto_fill_22(base))
        total_iira = _total(auto_fill_22(with_iira))

        assert total_iira < total_base, (
            f"inherited-IRA ordinary income should shrink conversion room: "
            f"with={total_iira:,.0f} !< without={total_base:,.0f}"
        )

    def test_no_inherited_ira_still_fills(self):
        # Regression guard: households without inherited IRAs are unaffected.
        hh = Household(your_age=61, spouse_age=55, your_ira=1_700_000, spouse_ira=1_700_000)
        plan = auto_fill_22(hh)
        assert sum(plan.your_conversions.values()) > 0


class TestBracketFillSurvivorCeiling:
    """U2: add_bracket_fill_withdrawals must fill to the Single 22% ceiling in
    survivor years, not the household's original MFJ ceiling."""

    def test_yearresult_carries_filing_status(self):
        hh = Household(
            your_age=61,
            spouse_age=55,
            your_ira=500_000,
            spouse_ira=300_000,
            survivor=SurvivorScenario(who_dies="spouse", death_year=2030),
        )
        result = run_scenario(hh, ConversionPlan(), "surv", end_age=95)
        pre = next(yr for yr in result.years if yr.year == 2029)
        post = next(yr for yr in result.years if yr.year == 2031)
        assert pre.filing_status == "MFJ"
        assert post.filing_status == "Single"

    def test_survivor_fill_respects_single_ceiling(self):
        hh = Household(
            your_age=61,
            spouse_age=55,
            your_ira=500_000,
            spouse_ira=300_000,
            survivor=SurvivorScenario(who_dies="spouse", death_year=2030),
        )
        plan = add_bracket_fill_withdrawals(hh, ConversionPlan(), target_bracket=0.22)
        result = run_scenario(hh, plan, "fill", end_age=95)

        single_22_base = BRACKETS_SINGLE[2][0]
        exercised = False
        for yr in result.years:
            if yr.filing_status != "Single":
                continue
            if (yr.extra_withdrawal + yr.spouse_extra_withdrawal) <= 0:
                continue
            exercised = True
            ceiling = index_value(single_22_base, yr.year, hh.cpi_assumption)
            taxable_ord = yr.combined_gross - yr.total_deductions
            assert taxable_ord <= ceiling + 8_000, (
                f"survivor year {yr.year}: taxable {taxable_ord:,.0f} exceeds Single "
                f"22% ceiling {ceiling:,.0f} — fill used the MFJ ceiling (U2 bug)"
            )
        assert exercised, "fixture did not exercise a survivor-year bracket fill"
