"""Scenario engine — full multi-year Roth conversion projection.

Produces a year-by-year DataFrame with all income sources, taxes, costs,
IRA balances, brokerage tracking, and net benefit analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.aca import aca_applies, aca_subsidy_loss
from engine.ira import calc_rmd, ss_benefit_at_age, ss_with_cola
from engine.irmaa import irmaa_for_year, irmaa_next_threshold
from engine.tax import (
    deductions,
    federal_tax,
    marginal_rate,
    room_to_12,
    room_to_22,
    taxable_ss,
)
from models.household import Household


@dataclass
class YearResult:
    """All computed values for a single year."""

    year: int
    your_age: int
    spouse_age: int
    phase: str  # "options", "clean", "ss_conv", "squeeze"

    # IRA balances (beginning of year)
    your_ira_begin: float = 0.0
    spouse_ira_begin: float = 0.0

    # Income sources
    option_income: float = 0.0
    your_conversion: float = 0.0
    spouse_conversion: float = 0.0
    your_rmd: float = 0.0
    qcd: float = 0.0
    taxable_rmd: float = 0.0
    your_ss: float = 0.0
    spouse_ss: float = 0.0
    combined_ss: float = 0.0
    taxable_ss_amt: float = 0.0

    # Aggregates
    combined_gross: float = 0.0
    total_deductions: float = 0.0
    taxable_income: float = 0.0
    magi: float = 0.0  # for IRMAA/ACA (uses full RMD, full SS)

    # Tax & costs
    federal_tax_amt: float = 0.0
    marginal_bracket: float = 0.0
    conversion_tax: float = 0.0
    irmaa_cost: float = 0.0
    aca_loss: float = 0.0
    all_in_cost: float = 0.0

    # Bracket room
    room_12: float = 0.0
    room_22: float = 0.0
    irmaa_room: float = 0.0

    # Brokerage (excess RMD tracking)
    living_expenses: float = 0.0
    income_needed: float = 0.0
    excess_rmd: float = 0.0
    brokerage_balance: float = 0.0
    brokerage_growth: float = 0.0
    brokerage_gain_tax: float = 0.0

    # IRA end of year
    your_ira_end: float = 0.0
    spouse_ira_end: float = 0.0


@dataclass
class ConversionPlan:
    """User-specified conversion amounts per year."""

    your_conversions: dict[int, float] = field(default_factory=dict)  # year -> amount
    spouse_conversions: dict[int, float] = field(default_factory=dict)
    qcds: dict[int, float] = field(default_factory=dict)  # year -> QCD amount


@dataclass
class ScenarioResult:
    """Complete multi-year projection output."""

    name: str
    years: list[YearResult]
    household: Household
    plan: ConversionPlan

    # Summary
    total_your_conv: float = 0.0
    total_spouse_conv: float = 0.0
    total_conv_tax: float = 0.0
    total_irmaa: float = 0.0
    total_aca_loss: float = 0.0
    total_rmd_tax: float = 0.0  # cumulative tax during RMD years
    total_brok_tax: float = 0.0  # cumulative brokerage capital gains tax

    def years_as_dicts(self) -> list[dict]:
        """Convert to list of dicts for DataFrame creation."""
        return [yr.__dict__ for yr in self.years]


def run_scenario(
    hh: Household,
    plan: ConversionPlan,
    name: str = "Scenario",
    end_age: int = 95,
    early_exercise: bool = True,
) -> ScenarioResult:
    """
    Run a full projection from base_year through end_age.

    Phase 1 (your_age <= 74): Conversion years — you and/or spouse convert
    Phase 2 (your_age >= 75): RMD years — forced distributions, spouse may still convert
    """
    results = []
    your_ira = hh.your_ira
    spouse_ira = hh.spouse_ira
    brokerage = 0.0
    cum_conv_tax = 0.0
    cum_irmaa = 0.0
    cum_aca = 0.0
    cum_rmd_tax = 0.0
    cum_brok_tax = 0.0

    total_years = end_age - hh.your_age + 1

    for yr_idx in range(total_years):
        year = hh.base_year + yr_idx
        ya = hh.your_age + yr_idx
        sa = hh.spouse_age + yr_idx

        yr = YearResult(year=year, your_age=ya, spouse_age=sa, phase="")

        # === Phase classification ===
        if (
            ya <= min(74, hh.base_year + 2 - hh.base_year + hh.your_age)
            and hh.option_income(year, early_exercise) > 0
        ):
            yr.phase = "options"
        elif ya <= 74 and ya < 70:
            yr.phase = "clean"
        elif ya <= 74 and ya >= 70:
            yr.phase = "ss_conv"
        elif ya >= 75:
            yr.phase = "squeeze" if sa <= 74 else "rmd"
        else:
            yr.phase = "clean"

        # === IRA balances ===
        yr.your_ira_begin = your_ira
        yr.spouse_ira_begin = spouse_ira

        # === Option income ===
        yr.option_income = hh.option_income(year, early_exercise)

        # === Conversions ===
        yr.your_conversion = plan.your_conversions.get(year, 0.0)
        if ya > 74:
            yr.your_conversion = 0.0  # can't convert after 74
        yr.spouse_conversion = plan.spouse_conversions.get(year, 0.0)
        if sa < 60 or sa > 74:
            yr.spouse_conversion = 0.0  # penalty protection / past window

        # === RMD ===
        yr.your_rmd = calc_rmd(your_ira, ya, hh.rmd_start_age)
        yr.qcd = min(plan.qcds.get(year, 0.0), yr.your_rmd, hh.qcd_limit)
        yr.taxable_rmd = max(yr.your_rmd - yr.qcd, 0)

        # === Social Security ===
        your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.ss_start_age)
        spouse_ss_base = ss_benefit_at_age(hh.spouse_ss_fra, hh.ss_start_age)
        yr.your_ss = (
            ss_with_cola(your_ss_base, ya - hh.ss_start_age, hh.ss_cola)
            if ya >= hh.ss_start_age
            else 0.0
        )
        yr.spouse_ss = (
            ss_with_cola(spouse_ss_base, sa - hh.ss_start_age, hh.ss_cola)
            if sa >= hh.ss_start_age
            else 0.0
        )
        yr.combined_ss = yr.your_ss + yr.spouse_ss

        # === MAGI (for IRMAA/ACA — uses full amounts, not taxable) ===
        yr.magi = (
            yr.option_income
            + yr.your_conversion
            + yr.spouse_conversion
            + yr.your_rmd
            + yr.combined_ss
        )  # full RMD (before QCD for MAGI? QCD excluded from MAGI)
        # Actually QCD IS excluded from MAGI:
        yr.magi = (
            yr.option_income
            + yr.your_conversion
            + yr.spouse_conversion
            + yr.taxable_rmd
            + yr.combined_ss
        )

        # === SS taxation ===
        other_inc = yr.option_income + yr.your_conversion + yr.spouse_conversion + yr.taxable_rmd
        yr.taxable_ss_amt = taxable_ss(yr.combined_ss, other_inc)

        # === Combined gross (for tax) ===
        yr.combined_gross = (
            yr.option_income
            + yr.your_conversion
            + yr.spouse_conversion
            + yr.taxable_rmd
            + yr.taxable_ss_amt
        )

        # === Deductions ===
        yr.total_deductions = deductions(ya, sa, hh.std_deduction, hh.senior_extra)

        # === Taxable income ===
        yr.taxable_income = max(yr.combined_gross - yr.total_deductions, 0)

        # === Federal tax ===
        yr.federal_tax_amt = federal_tax(yr.taxable_income)
        yr.marginal_bracket = marginal_rate(yr.taxable_income)

        # === Conversion tax (incremental) ===
        base_gross = yr.combined_gross - yr.your_conversion - yr.spouse_conversion
        base_taxable = max(base_gross - yr.total_deductions, 0)
        yr.conversion_tax = federal_tax(yr.taxable_income) - federal_tax(base_taxable)

        # === IRMAA (2-year lookback) ===
        irmaa_cost, _ = irmaa_for_year(yr.magi, ya, sa)
        yr.irmaa_cost = irmaa_cost
        yr.irmaa_room = irmaa_next_threshold(yr.magi)

        # === ACA subsidy loss ===
        if aca_applies(ya):
            base_magi = yr.magi - yr.your_conversion - yr.spouse_conversion
            yr.aca_loss = aca_subsidy_loss(base_magi, yr.magi)
        else:
            yr.aca_loss = 0.0

        # === All-in cost of conversions ===
        yr.all_in_cost = yr.conversion_tax + yr.irmaa_cost + yr.aca_loss

        # === Bracket room ===
        yr.room_12 = room_to_12(yr.combined_gross, yr.total_deductions)
        yr.room_22 = room_to_22(yr.combined_gross, yr.total_deductions)

        # === Living expenses & brokerage ===
        years_from_base = yr_idx
        yr.living_expenses = hh.living_expenses * (1 + hh.expense_inflation) ** years_from_base

        after_tax_rmd = yr.your_rmd - yr.qcd  # taxable RMD (net of QCD)
        available_income = after_tax_rmd + yr.combined_ss - yr.federal_tax_amt
        yr.income_needed = max(yr.living_expenses - available_income, 0)
        yr.excess_rmd = max(available_income - yr.living_expenses, 0)

        # Brokerage: accumulates excess, grows, pays cap gains
        yr.brokerage_balance = brokerage
        yr.brokerage_growth = brokerage * hh.growth_rate
        realized_gains = yr.brokerage_growth * hh.brok_turnover
        yr.brokerage_gain_tax = realized_gains * hh.ltcg_rate

        brokerage = brokerage + yr.brokerage_growth - yr.brokerage_gain_tax + yr.excess_rmd

        # === IRA end of year ===
        your_withdrawal = yr.your_conversion + yr.your_rmd
        spouse_withdrawal = yr.spouse_conversion
        # Spouse RMD (if spouse hits 75)
        spouse_rmd = calc_rmd(spouse_ira, sa, hh.rmd_start_age)
        spouse_withdrawal += spouse_rmd

        yr.your_ira_end = max(your_ira - your_withdrawal, 0) * (1 + hh.growth_rate)
        yr.spouse_ira_end = max(spouse_ira - spouse_withdrawal, 0) * (1 + hh.growth_rate)

        # Carry forward
        your_ira = yr.your_ira_end
        spouse_ira = yr.spouse_ira_end

        # Accumulate totals
        cum_conv_tax += yr.conversion_tax
        cum_irmaa += yr.irmaa_cost
        cum_aca += yr.aca_loss
        if ya >= hh.rmd_start_age:
            cum_rmd_tax += yr.federal_tax_amt
        cum_brok_tax += yr.brokerage_gain_tax

        results.append(yr)

    return ScenarioResult(
        name=name,
        years=results,
        household=hh,
        plan=plan,
        total_your_conv=sum(yr.your_conversion for yr in results),
        total_spouse_conv=sum(yr.spouse_conversion for yr in results),
        total_conv_tax=cum_conv_tax,
        total_irmaa=cum_irmaa,
        total_aca_loss=cum_aca,
        total_rmd_tax=cum_rmd_tax,
        total_brok_tax=cum_brok_tax,
    )


def run_no_conversion(
    hh: Household, end_age: int = 95, early_exercise: bool = True
) -> ScenarioResult:
    """Baseline scenario: no conversions at all."""
    return run_scenario(hh, ConversionPlan(), "No Conversion", end_age, early_exercise)


def auto_fill_12(hh: Household, early_exercise: bool = True) -> ConversionPlan:
    """
    Generate a ConversionPlan that fills to the 12% bracket ceiling each year.
    Runs iteratively since each year's conversion affects the next year's IRA balance.
    """
    plan = ConversionPlan()
    your_ira = hh.your_ira
    spouse_ira = hh.spouse_ira

    for yr_idx in range(hh.rmd_start_age - 1 - hh.your_age + 1 + 6):  # +6 for spouse squeeze years
        year = hh.base_year + yr_idx
        ya = hh.your_age + yr_idx
        sa = hh.spouse_age + yr_idx

        if ya > 80:
            break

        # Option income
        opt = hh.option_income(year, early_exercise)

        # SS
        your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.ss_start_age)
        spouse_ss_base = ss_benefit_at_age(hh.spouse_ss_fra, hh.ss_start_age)
        your_ss = (
            ss_with_cola(your_ss_base, ya - hh.ss_start_age, hh.ss_cola)
            if ya >= hh.ss_start_age
            else 0.0
        )
        spouse_ss = (
            ss_with_cola(spouse_ss_base, sa - hh.ss_start_age, hh.ss_cola)
            if sa >= hh.ss_start_age
            else 0.0
        )
        combined_ss = your_ss + spouse_ss

        # RMD
        rmd = calc_rmd(your_ira, ya, hh.rmd_start_age)
        taxable_rmd = rmd  # no QCD in auto-fill

        # Taxable SS (need to estimate with current other income)
        other_fixed = opt + (taxable_rmd if ya >= hh.rmd_start_age else 0)
        tss = taxable_ss(combined_ss, other_fixed)

        # Fixed gross
        fixed_gross = opt + (taxable_rmd if ya >= hh.rmd_start_age else 0) + tss

        # Deductions
        ded = deductions(ya, sa, hh.std_deduction, hh.senior_extra)

        # Room to 12%
        room = room_to_12(fixed_gross, ded)

        # Allocate room
        if ya <= 74 and room > 0:
            yc = min(room, your_ira)
            plan.your_conversions[year] = yc
            room -= yc
        else:
            yc = 0

        if 60 <= sa <= 74 and room > 0:
            sc = min(room, spouse_ira)
            plan.spouse_conversions[year] = sc
        else:
            sc = 0

        # Update IRAs for next year
        your_withdrawal = yc + rmd
        your_ira = max(your_ira - your_withdrawal, 0) * (1 + hh.growth_rate)

        spouse_rmd = calc_rmd(spouse_ira, sa, hh.rmd_start_age)
        spouse_ira = max(spouse_ira - sc - spouse_rmd, 0) * (1 + hh.growth_rate)

    return plan
