"""Scenario engine — full multi-year Roth conversion projection.

Produces a year-by-year DataFrame with all income sources, taxes, costs,
IRA balances, brokerage tracking, and net benefit analysis.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from engine.aca import aca_applies, aca_subsidy_loss
from engine.ira import calc_rmd, inherited_ira_drain, ss_benefit_at_age, ss_with_cola
from engine.irmaa import irmaa_for_year, irmaa_next_threshold
from engine.niit import niit
from engine.tax import (
    BRACKETS_SINGLE,
    LTCG_THRESHOLDS_MFJ,
    LTCG_THRESHOLDS_SINGLE,
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
    federal_tax,
    federal_tax_single,
    marginal_rate,
    marginal_rate_single,
    room_to_12,
    room_to_22,
    room_to_bracket,
    senior_bonus_deduction,
    taxable_ss,
)
from models.household import Household, SurvivorScenario
from models.ytd_income import YTDSnapshot


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
    spouse_rmd: float = 0.0
    spouse_qcd: float = 0.0
    spouse_taxable_rmd: float = 0.0
    your_ss: float = 0.0
    spouse_ss: float = 0.0
    combined_ss: float = 0.0
    taxable_ss_amt: float = 0.0

    extra_withdrawal: float = (
        0.0  # voluntary excess withdrawal from your IRA (post-RMD bracket fill)
    )
    spouse_extra_withdrawal: float = (
        0.0  # voluntary excess withdrawal from spouse IRA (post-RMD bracket fill)
    )

    # YTD actuals (base year only, when ytd snapshot provided)
    ytd_wages: float = 0.0
    ytd_ltcg: float = 0.0
    ytd_stcg: float = 0.0
    ytd_dividends: float = 0.0  # aggregate (qualified + ordinary); backward compat
    ytd_qualified_dividends: float = 0.0
    ytd_ordinary_dividends: float = 0.0
    ytd_interest: float = 0.0
    ytd_conversions_done: float = 0.0
    ytd_ltcg_tax: float = 0.0  # LTCG tax computed separately

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
    niit_cost: float = 0.0
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
    brokerage_qual_div: float = 0.0  # qualified dividends (MAGI-only / LTCG rate)
    brokerage_ord_div: float = 0.0  # ordinary dividends (ordinary income stack)

    # Inherited IRA distributions (SECURE Act 10-year rule)
    your_inherited_distribution: float = 0.0
    spouse_inherited_distribution: float = 0.0
    your_inherited_balance_end: float = 0.0  # sum of inherited balances for "you" at end of year
    spouse_inherited_balance_end: float = (
        0.0  # sum of inherited balances for "spouse" at end of year
    )

    # IRA end of year
    your_ira_end: float = 0.0
    spouse_ira_end: float = 0.0


@dataclass
class ConversionPlan:
    """User-specified conversion amounts per year."""

    your_conversions: dict[int, float] = field(default_factory=dict)  # year -> amount
    spouse_conversions: dict[int, float] = field(default_factory=dict)
    qcds: dict[int, float] = field(default_factory=dict)  # year -> QCD amount
    spouse_qcds: dict[int, float] = field(default_factory=dict)  # year -> spouse QCD amount
    extra_withdrawals: dict[int, float] = field(
        default_factory=dict
    )  # year -> voluntary excess (your IRA)
    spouse_extra_withdrawals: dict[int, float] = field(
        default_factory=dict
    )  # year -> voluntary excess (spouse IRA)


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
    total_niit: float = 0.0
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
    ytd: YTDSnapshot | None = None,
) -> ScenarioResult:
    """
    Run a full projection from base_year through end_age.

    Phase 1 (your_age <= 74): Conversion years — you and/or spouse convert
    Phase 2 (your_age >= 75): RMD years — forced distributions, spouse may still convert
    """
    results = []
    your_ira = hh.your_ira
    spouse_ira = hh.spouse_ira
    # TODO(math-audit-2026-06-12 P3): Brokerage starting balance not initialized from YTD
    # snapshot. Projection always starts at 0.0, ignoring any brokerage balance already
    # accumulated by the snapshot date. Fix requires adding a brokerage_balance field to
    # YTDSnapshot (model extension deferred — see ai_docs/MATH_AUDIT_2026-06-12.md P3).
    brokerage = 0.0
    cum_conv_tax = 0.0
    cum_irmaa = 0.0
    cum_aca = 0.0
    cum_niit = 0.0
    cum_rmd_tax = 0.0
    cum_brok_tax = 0.0
    # Accumulates projected MAGI per calendar year for IRMAA 2-year lookback
    magi_history: dict[int, float] = {}

    # Survivor scenario pre-check
    surv: SurvivorScenario | None = hh.survivor
    _rollover_done: bool = False

    # Mutable copies of inherited IRA balances (one per InheritedIRA), keyed by index
    inherited_balances: list[float] = [iira.balance for iira in hh.inherited_iras]

    total_years = end_age - hh.your_age + 1

    for yr_idx in range(total_years):
        year = hh.base_year + yr_idx
        ya = hh.your_age + yr_idx
        sa = hh.spouse_age + yr_idx

        # === Survivor scenario: determine filing status and effective ages ===
        survivor_active = surv is not None and year >= surv.death_year + 1
        current_filing_status = "Single" if survivor_active else hh.filing_status

        # IRA rollover: at the first year survivor_active, roll deceased into survivor
        if survivor_active and not _rollover_done:
            assert surv is not None  # narrowing: survivor_active implies surv is not None
            if surv.who_dies == "you":
                spouse_ira += your_ira
                your_ira = 0.0
            else:
                your_ira += spouse_ira
                spouse_ira = 0.0
            _rollover_done = True

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

        # === Brokerage dividend forecast ===
        # Skip in base year if YTD actuals are provided (they already carry real dividends).
        # yield_rate defaults to 0.0 on GrowthProfile, so this is zero-cost when not configured.
        use_forecast_divs = ytd is None or year != hh.base_year
        if use_forecast_divs and hh.brokerage_growth is not None:
            qual_div_this_year = hh.brokerage_growth.qualified_div_for(year, brokerage)
            ord_div_this_year = hh.brokerage_growth.ordinary_div_for(year, brokerage)
        else:
            qual_div_this_year = 0.0
            ord_div_this_year = 0.0
        yr.brokerage_qual_div = qual_div_this_year
        yr.brokerage_ord_div = ord_div_this_year

        # === YTD injection (base year only) ===
        # Resolve to a concrete YTDSnapshot for the base year, or None.
        # This avoids repeated `ytd is not None` narrowing for mypy.
        ytd_year: YTDSnapshot | None = ytd if year == hh.base_year else None
        if ytd_year is not None:
            yr.ytd_wages = ytd_year.wages_ytd
            yr.ytd_ltcg = ytd_year.ltcg_ytd
            yr.ytd_stcg = ytd_year.stcg_ytd
            yr.ytd_qualified_dividends = ytd_year.qualified_dividends_ytd
            yr.ytd_ordinary_dividends = ytd_year.ordinary_dividends_ytd
            yr.ytd_dividends = ytd_year.dividends_ytd  # aggregate; backward compat
            yr.ytd_interest = ytd_year.interest_ytd
            yr.ytd_conversions_done = ytd_year.ira_conversions_ytd

        # === Conversions ===
        yr.your_conversion = plan.your_conversions.get(year, 0.0)
        if ya > 74:
            yr.your_conversion = 0.0  # can't convert after 74
        yr.spouse_conversion = plan.spouse_conversions.get(year, 0.0)
        if sa > 74:
            yr.spouse_conversion = 0.0  # past conversion window (RMDs begin)

        # Subtract conversions already done YTD from planned amount
        if ytd_year is not None and ytd_year.ira_conversions_ytd > 0:
            remaining = max(yr.your_conversion - ytd_year.ira_conversions_ytd, 0.0)
            yr.your_conversion = remaining

        # === RMD ===
        # When survivor_active, deceased's IRA was rolled to survivor at death_year+1.
        # The deceased's IRA variable is now 0, so calc_rmd returns 0 naturally.
        # QCD: after death the deceased's QCD limit is unavailable; survivor keeps
        # their own limit (qcd_limit is per-person, so no change needed for survivor).
        yr.your_rmd = calc_rmd(your_ira, ya, hh.your_rmd_start_age)
        yr.qcd = min(plan.qcds.get(year, 0.0), yr.your_rmd, hh.qcd_limit)
        yr.taxable_rmd = max(yr.your_rmd - yr.qcd, 0)
        yr.spouse_rmd = calc_rmd(spouse_ira, sa, hh.spouse_rmd_start_age)
        yr.spouse_qcd = min(plan.spouse_qcds.get(year, 0.0), yr.spouse_rmd, hh.qcd_limit)
        yr.spouse_taxable_rmd = max(yr.spouse_rmd - yr.spouse_qcd, 0)

        # === Extra voluntary withdrawals (bracket fill post-RMD) ===
        yr.extra_withdrawal = plan.extra_withdrawals.get(year, 0.0)
        yr.spouse_extra_withdrawal = plan.spouse_extra_withdrawals.get(year, 0.0)

        # === Inherited IRA drains (SECURE Act 10-year rule) ===
        your_inherited_distribution = 0.0
        spouse_inherited_distribution = 0.0
        for idx, iira in enumerate(hh.inherited_iras):
            if year < iira.inherited_year:
                continue  # not yet inherited
            years_in = year - iira.inherited_year
            years_remaining = 10 - years_in
            if years_remaining <= 0:
                continue  # fully drained
            drain = inherited_ira_drain(inherited_balances[idx], years_remaining)
            if iira.owner == "you":
                your_inherited_distribution += drain
            else:
                spouse_inherited_distribution += drain
            # Apply drain + growth to balance for next year
            inherited_balances[idx] = max(inherited_balances[idx] - drain, 0.0) * (
                1 + iira.growth_rate
            )
        yr.your_inherited_distribution = your_inherited_distribution
        yr.spouse_inherited_distribution = spouse_inherited_distribution

        # === Social Security ===
        your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
        spouse_ss_base = ss_benefit_at_age(
            hh.spouse_ss_fra, hh.spouse_ss_start_age, hh.spouse_fra_age
        )
        yr.your_ss = (
            ss_with_cola(your_ss_base, ya - hh.your_ss_start_age, hh.ss_cola)
            if ya >= hh.your_ss_start_age
            else 0.0
        )
        yr.spouse_ss = (
            ss_with_cola(spouse_ss_base, sa - hh.spouse_ss_start_age, hh.ss_cola)
            if sa >= hh.spouse_ss_start_age
            else 0.0
        )
        # Survivor: zero the deceased spouse's SS from death_year + 1 onward
        # (SS survivor benefit step-up is NOT yet modeled — deferred to future PR)
        if survivor_active:
            assert surv is not None  # narrowing: survivor_active implies surv is not None
            if surv.who_dies == "you":
                yr.your_ss = 0.0
            else:
                yr.spouse_ss = 0.0
        yr.combined_ss = yr.your_ss + yr.spouse_ss

        # === MAGI (for IRMAA/ACA — uses full amounts, not taxable) ===
        # QCD IS excluded from MAGI, so use taxable_rmd / spouse_taxable_rmd
        yr.magi = (
            yr.option_income
            + yr.your_conversion
            + yr.spouse_conversion
            + yr.taxable_rmd
            + yr.spouse_taxable_rmd
            + yr.extra_withdrawal
            + yr.spouse_extra_withdrawal
            + yr.combined_ss
            + yr.your_inherited_distribution
            + yr.spouse_inherited_distribution
        )
        # YTD: add all MAGI components via the canonical magi_ytd property.
        # Using the property ensures parity with _auto_fill_core and avoids
        # missing fields (nec_income_ytd, ira_conversions_ytd,
        # ira_distributions_ytd were absent in the prior manual enumeration).
        if ytd_year is not None:
            yr.magi += ytd_year.magi_ytd
        # Forecast brokerage dividends: both qual and ord affect MAGI
        yr.magi += qual_div_this_year + ord_div_this_year

        # Accumulate projected MAGI for future IRMAA lookback resolution
        magi_history[year] = yr.magi

        # === SS taxation ===
        other_inc = (
            yr.option_income
            + yr.your_conversion
            + yr.spouse_conversion
            + yr.taxable_rmd
            + yr.spouse_taxable_rmd
            + yr.extra_withdrawal
            + yr.spouse_extra_withdrawal
        )
        # YTD ordinary income affects SS taxation.
        # Includes wages, NEC, STCG, ordinary dividends, conversions already
        # done, and IRA distributions — matching total_ordinary_income minus
        # nqo_exercise_ytd (NQO spread is captured in option_income for the
        # base year, not double-counted here).
        if ytd_year is not None:
            other_inc += (
                ytd_year.wages_ytd
                + ytd_year.nec_income_ytd
                + ytd_year.stcg_ytd
                + ytd_year.ordinary_dividends_ytd
                + ytd_year.ira_conversions_ytd
                + ytd_year.ira_distributions_ytd
            )
        yr.taxable_ss_amt = taxable_ss(
            yr.combined_ss, other_inc, filing_status=current_filing_status
        )

        # === Combined gross (for tax) ===
        # Includes ordinary income only — LTCG taxed separately at preferential rate
        yr.combined_gross = (
            yr.option_income
            + yr.your_conversion
            + yr.spouse_conversion
            + yr.taxable_rmd
            + yr.spouse_taxable_rmd
            + yr.extra_withdrawal
            + yr.spouse_extra_withdrawal
            + yr.taxable_ss_amt
            + yr.your_inherited_distribution
            + yr.spouse_inherited_distribution
        )
        # YTD: add all ordinary income components to gross.
        # LTCG and qualified dividends are excluded (taxed at preferential rate).
        # nec_income_ytd and ira_distributions_ytd are ordinary income; include them.
        # ira_conversions_ytd: yr.your_conversion was already reduced by this amount
        # (line 281), so adding it back here makes the full planned conversion stack
        # into combined_gross correctly.
        if ytd_year is not None:
            yr.combined_gross += (
                ytd_year.wages_ytd
                + ytd_year.nec_income_ytd
                + ytd_year.stcg_ytd
                + ytd_year.ordinary_dividends_ytd
                + ytd_year.ira_conversions_ytd
                + ytd_year.ira_distributions_ytd
            )
        # Forecast ordinary dividends are ordinary income; qualified dividends are MAGI-only (like LTCG)
        yr.combined_gross += ord_div_this_year

        # === Deductions ===
        if survivor_active:
            assert surv is not None  # narrowing: survivor_active implies surv is not None
            # Use single-filer std deduction + senior extra; zero deceased age so
            # only the survivor counts toward the senior-extra and OBBBA bonus.
            ya_eff = 0 if surv.who_dies == "you" else ya
            sa_eff = 0 if surv.who_dies == "spouse" else sa
            yr.total_deductions = deductions(
                ya_eff, sa_eff, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE
            )
            yr.total_deductions += senior_bonus_deduction(
                ya_eff, sa_eff, yr.magi, filing_status="Single"
            )
        else:
            yr.total_deductions = deductions(ya, sa, hh.std_deduction, hh.senior_extra)
            yr.total_deductions += senior_bonus_deduction(ya, sa, yr.magi)

        # === Taxable income ===
        yr.taxable_income = max(yr.combined_gross - yr.total_deductions, 0)

        # === Federal tax ===
        if survivor_active:
            yr.federal_tax_amt = federal_tax_single(yr.taxable_income)
            yr.marginal_bracket = marginal_rate_single(yr.taxable_income)
        else:
            yr.federal_tax_amt = federal_tax(yr.taxable_income)
            yr.marginal_bracket = marginal_rate(yr.taxable_income)

        # === Conversion tax (incremental) ===
        base_gross = yr.combined_gross - yr.your_conversion - yr.spouse_conversion
        base_taxable = max(base_gross - yr.total_deductions, 0)
        if survivor_active:
            yr.conversion_tax = federal_tax_single(yr.taxable_income) - federal_tax_single(
                base_taxable
            )
        else:
            yr.conversion_tax = federal_tax(yr.taxable_income) - federal_tax(base_taxable)

        # === IRMAA (2-year lookback) ===
        # IRMAA paid in year Y is based on filed MAGI of year Y-2.
        # Resolution priority: prior_year_magi anchor > magi_history > same-year fallback.
        income_year = year - 2
        if income_year in hh.prior_year_magi:
            # User has provided actual filed MAGI for the lookback year
            magi_for_irmaa = hh.prior_year_magi[income_year]
        elif income_year in magi_history:
            # We've already projected the lookback year in this loop
            magi_for_irmaa = magi_history[income_year]
        else:
            # Fallback: lookback year predates the projection window and no anchor provided.
            # Use this year's projected MAGI as a same-year approximation
            # (only reached for yr_idx < 2 when prior_year_magi is empty).
            magi_for_irmaa = yr.magi
        # irmaa_for_year() adds +2 internally for the 2-year MAGI lookback;
        # pass income-year ages (ya - 2, sa - 2) so Medicare-year ages come out correctly.
        irmaa_cost, _ = irmaa_for_year(
            magi_for_irmaa,
            ya - 2,
            sa - 2,
            base_part_b=hh.medicare_part_b_base_monthly * 12,
            filing_status=current_filing_status,
        )
        yr.irmaa_cost = irmaa_cost
        yr.irmaa_room = irmaa_next_threshold(yr.magi)

        # === ACA subsidy loss ===
        # ACA applies if anyone in household is enrolled and pre-Medicare
        anyone_on_aca = aca_applies(ya, hh.your_aca_enrolled) or aca_applies(
            sa, hh.spouse_aca_enrolled
        )
        if anyone_on_aca:
            base_magi = yr.magi - yr.your_conversion - yr.spouse_conversion
            yr.aca_loss = aca_subsidy_loss(
                base_magi,
                yr.magi,
                hh.aca_benchmark_premium_annual,
                enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
            )
        else:
            yr.aca_loss = 0.0

        # === LTCG tax (computed separately at preferential rate) ===
        if ytd_year is not None and ytd_year.ltcg_ytd > 0:
            yr.ytd_ltcg_tax = ytd_year.ltcg_ytd * hh.ltcg_rate

        # === NIIT (3.8% surtax on investment income when MAGI > $250K) ===
        # Net investment income = realized appreciation gains + all dividends (qual + ord)
        # Computed on beginning brokerage balance (carry-forward from prior year)
        brok_rate = hh.brokerage_rate(year)
        if hh.brokerage_growth is not None:
            brok_appreciation_rate = hh.brokerage_growth.appreciation_for(year)
        else:
            brok_appreciation_rate = brok_rate
        net_investment_income = (
            brokerage * brok_appreciation_rate * hh.brok_turnover
            + qual_div_this_year
            + ord_div_this_year
        )
        # YTD: add realized gains, dividends, interest to investment income
        if ytd_year is not None:
            net_investment_income += ytd_year.total_investment_income
        yr.niit_cost = niit(yr.magi, net_investment_income, filing_status=current_filing_status)

        # === All-in cost of conversions ===
        yr.all_in_cost = yr.conversion_tax + yr.irmaa_cost + yr.aca_loss + yr.niit_cost

        # === Bracket room ===
        if survivor_active:
            # Single brackets: 12% ceiling = 50_400, 22% ceiling = 105_700
            yr.room_12 = room_to_bracket(
                yr.combined_gross, yr.total_deductions, BRACKETS_SINGLE[1][0]
            )
            yr.room_22 = room_to_bracket(
                yr.combined_gross, yr.total_deductions, BRACKETS_SINGLE[2][0]
            )
        else:
            yr.room_12 = room_to_12(yr.combined_gross, yr.total_deductions)
            yr.room_22 = room_to_22(yr.combined_gross, yr.total_deductions)

        # === Living expenses & brokerage ===
        years_from_base = yr_idx
        yr.living_expenses = hh.living_expenses * (1 + hh.expense_inflation) ** years_from_base

        after_tax_rmd = yr.your_rmd - yr.qcd  # taxable RMD (net of QCD)
        available_income = after_tax_rmd + yr.extra_withdrawal + yr.combined_ss - yr.federal_tax_amt
        yr.income_needed = max(yr.living_expenses - available_income, 0)
        yr.excess_rmd = max(available_income - yr.living_expenses, 0)

        # Brokerage: accumulates excess, grows (appreciation), dividends reinvest, pays cap gains
        yr.brokerage_balance = brokerage
        yr.brokerage_growth = brokerage * brok_appreciation_rate
        realized_gains = yr.brokerage_growth * hh.brok_turnover
        # Stack-walk LTCG brackets: ordinary taxable income sets the starting
        # point; realized gains + qualified dividends (IRC §1(h)(11)) walk up
        # through 0% / 15% / 20% bands.
        # yr.taxable_income is already ordinary-only; do NOT subtract realized_gains.
        # Thresholds depend on filing status: Single for survivor years, MFJ otherwise.
        ltcg_thresholds = LTCG_THRESHOLDS_SINGLE if survivor_active else LTCG_THRESHOLDS_MFJ
        ltcg_eligible = realized_gains + qual_div_this_year
        _ltcg_start = max(0.0, yr.taxable_income)
        _ltcg_end = _ltcg_start + max(0.0, ltcg_eligible)
        _ltcg_at_15 = max(
            0.0,
            min(_ltcg_end, ltcg_thresholds[1]) - max(_ltcg_start, ltcg_thresholds[0]),
        )
        _ltcg_at_20 = max(0.0, _ltcg_end - max(_ltcg_start, ltcg_thresholds[1]))
        yr.brokerage_gain_tax = _ltcg_at_15 * 0.15 + _ltcg_at_20 * 0.20
        total_div = qual_div_this_year + ord_div_this_year

        brokerage = (
            brokerage
            + yr.brokerage_growth
            - yr.brokerage_gain_tax
            + total_div  # dividends reinvested (taxable event already captured in income stacks)
            + yr.excess_rmd
        )

        # === IRA end of year ===
        your_withdrawal = yr.your_conversion + yr.your_rmd + yr.extra_withdrawal
        spouse_withdrawal = yr.spouse_conversion + yr.spouse_rmd + yr.spouse_extra_withdrawal

        yr.your_ira_end = max(your_ira - your_withdrawal, 0) * (1 + hh.your_ira_rate(year))
        yr.spouse_ira_end = max(spouse_ira - spouse_withdrawal, 0) * (1 + hh.spouse_ira_rate(year))

        # Inherited IRA end-of-year balances (sum by owner, after drain+growth applied above)
        yr.your_inherited_balance_end = sum(
            inherited_balances[i] for i, iira in enumerate(hh.inherited_iras) if iira.owner == "you"
        )
        yr.spouse_inherited_balance_end = sum(
            inherited_balances[i]
            for i, iira in enumerate(hh.inherited_iras)
            if iira.owner == "spouse"
        )

        # Carry forward
        your_ira = yr.your_ira_end
        spouse_ira = yr.spouse_ira_end

        # Accumulate totals
        cum_conv_tax += yr.conversion_tax
        cum_irmaa += yr.irmaa_cost
        cum_aca += yr.aca_loss
        cum_niit += yr.niit_cost
        if ya >= hh.your_rmd_start_age:
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
        total_niit=cum_niit,
        total_rmd_tax=cum_rmd_tax,
        total_brok_tax=cum_brok_tax,
    )


def run_no_conversion(
    hh: Household, end_age: int = 95, early_exercise: bool = True
) -> ScenarioResult:
    """Baseline scenario: no conversions at all."""
    return run_scenario(hh, ConversionPlan(), "No Conversion", end_age, early_exercise)


def _auto_fill_core(
    hh: Household,
    early_exercise: bool,
    ytd: YTDSnapshot | None,
    room_fn: Callable[[float, float, float], float],
) -> ConversionPlan:
    """Shared body of auto_fill_12 / auto_fill_22 / auto_fill_irmaa_safe.

    The only difference between those three is how ``room`` is computed each
    year. This core does everything else identically; the room calculation is
    delegated to ``room_fn(fixed_gross, ded, base_magi) -> float``.

    ``base_magi`` is always computed and passed (cheap; identical expression in
    all three originals). The 12% and 22% variants ignore it; the IRMAA-safe
    variant uses it to enforce the joint-MAGI ceiling.
    """
    plan = ConversionPlan()
    your_ira = hh.your_ira
    spouse_ira = hh.spouse_ira

    for yr_idx in range(
        hh.your_rmd_start_age - 1 - hh.your_age + 1 + 6
    ):  # +6 for spouse squeeze years
        year = hh.base_year + yr_idx
        ya = hh.your_age + yr_idx
        sa = hh.spouse_age + yr_idx
        ytd_year: YTDSnapshot | None = ytd if year == hh.base_year else None

        if ya > 80:
            break

        # Option income
        opt = hh.option_income(year, early_exercise)

        # SS
        your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
        spouse_ss_base = ss_benefit_at_age(
            hh.spouse_ss_fra, hh.spouse_ss_start_age, hh.spouse_fra_age
        )
        your_ss = (
            ss_with_cola(your_ss_base, ya - hh.your_ss_start_age, hh.ss_cola)
            if ya >= hh.your_ss_start_age
            else 0.0
        )
        spouse_ss = (
            ss_with_cola(spouse_ss_base, sa - hh.spouse_ss_start_age, hh.ss_cola)
            if sa >= hh.spouse_ss_start_age
            else 0.0
        )
        combined_ss = your_ss + spouse_ss

        # RMD
        rmd = calc_rmd(your_ira, ya, hh.your_rmd_start_age)
        taxable_rmd = rmd  # no QCD in auto-fill (QCDs reduce income but not conversion room)
        spouse_taxable_rmd = calc_rmd(
            spouse_ira, sa, hh.spouse_rmd_start_age
        )  # no spouse QCD in auto-fill

        # MAGI without conversion (full MAGI — includes LTCG for IRMAA)
        # Identical to approx_magi in the former 12/22 variants; passed to room_fn
        # so the IRMAA-safe variant can enforce its joint-MAGI ceiling.
        base_magi = (
            opt
            + combined_ss
            + (taxable_rmd if ya >= hh.your_rmd_start_age else 0)
            + spouse_taxable_rmd
        )
        if ytd_year is not None:
            base_magi += ytd_year.magi_ytd

        # Taxable SS (need to estimate with current other income)
        other_fixed = opt + (taxable_rmd if ya >= hh.your_rmd_start_age else 0) + spouse_taxable_rmd
        # YTD ordinary income affects SS taxation.
        # Mirrors run_scenario's combined_gross YTD block: wages, NEC, STCG,
        # ordinary dividends, conversions done, and IRA distributions all stack
        # into ordinary income. ordinary_dividends_ytd was previously omitted here
        # (math audit 2026-06-12 Priority 3), overstating bracket room by that amount.
        if ytd_year is not None:
            other_fixed += (
                ytd_year.wages_ytd
                + ytd_year.nec_income_ytd
                + ytd_year.stcg_ytd
                + ytd_year.ordinary_dividends_ytd
                + ytd_year.ira_conversions_ytd
                + ytd_year.ira_distributions_ytd
            )
        tss = taxable_ss(combined_ss, other_fixed)

        # Fixed gross (ordinary income — no LTCG)
        fixed_gross = (
            opt + (taxable_rmd if ya >= hh.your_rmd_start_age else 0) + spouse_taxable_rmd + tss
        )
        if ytd_year is not None:
            fixed_gross += (
                ytd_year.wages_ytd
                + ytd_year.nec_income_ytd
                + ytd_year.stcg_ytd
                + ytd_year.ordinary_dividends_ytd
                + ytd_year.ira_conversions_ytd
                + ytd_year.ira_distributions_ytd
            )

        # Deductions
        ded = deductions(ya, sa, hh.std_deduction, hh.senior_extra)
        ded += senior_bonus_deduction(ya, sa, base_magi)

        # Room — delegated to caller's room_fn
        room = room_fn(fixed_gross, ded, base_magi)

        # Allocate room
        # Symmetric allocation: older pre-RMD person first (drains the IRA closest to RMD).
        # On age tie, larger IRA first. Both criteria are symmetric under me↔spouse swap.
        you_first = (ya > sa) or (ya == sa and your_ira >= spouse_ira)

        if you_first:
            if ya <= 74 and room > 0:
                yc = min(room, your_ira)
                plan.your_conversions[year] = yc
                room -= yc
            else:
                yc = 0

            if sa <= 74 and room > 0:
                sc = min(room, spouse_ira)
                plan.spouse_conversions[year] = sc
                room -= sc
            else:
                sc = 0
        else:
            if sa <= 74 and room > 0:
                sc = min(room, spouse_ira)
                plan.spouse_conversions[year] = sc
                room -= sc
            else:
                sc = 0

            if ya <= 74 and room > 0:
                yc = min(room, your_ira)
                plan.your_conversions[year] = yc
                room -= yc
            else:
                yc = 0

        # Update IRAs for next year
        your_withdrawal = yc + rmd
        your_ira = max(your_ira - your_withdrawal, 0) * (1 + hh.your_ira_rate(year))

        spouse_rmd = calc_rmd(spouse_ira, sa, hh.spouse_rmd_start_age)
        spouse_ira = max(spouse_ira - sc - spouse_rmd, 0) * (1 + hh.spouse_ira_rate(year))

    return plan


def auto_fill_12(
    hh: Household,
    early_exercise: bool = True,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that fills to the 12% bracket ceiling each year.
    Runs iteratively since each year's conversion affects the next year's IRA balance.
    """
    return _auto_fill_core(
        hh,
        early_exercise,
        ytd,
        room_fn=lambda fg, ded, _base_magi: room_to_12(fg, ded),
    )


def auto_fill_22(
    hh: Household,
    early_exercise: bool = True,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that fills to the 22% bracket ceiling each year.
    More aggressive than fill_12 — converts more but at higher marginal rates.
    """
    return _auto_fill_core(
        hh,
        early_exercise,
        ytd,
        room_fn=lambda fg, ded, _base_magi: room_to_22(fg, ded),
    )


def auto_fill_irmaa_safe(
    hh: Household,
    early_exercise: bool = True,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that maximizes conversion without triggering IRMAA.
    Caps MAGI at the first IRMAA tier threshold ($218K for 2026).
    """
    from engine.irmaa import IRMAA_TIERS_MFJ

    irmaa_threshold = IRMAA_TIERS_MFJ[0][0]  # tier-1 joint MAGI ceiling

    def _irmaa_room(fixed_gross: float, ded: float, base_magi: float) -> float:
        # Room to IRMAA threshold, capped at 22% bracket room
        irmaa_room = max(irmaa_threshold - base_magi, 0.0)
        return min(irmaa_room, room_to_22(fixed_gross, ded))

    return _auto_fill_core(hh, early_exercise, ytd, room_fn=_irmaa_room)


def add_bracket_fill_withdrawals(
    hh: Household,
    base_plan: ConversionPlan,
    target_bracket: float = 0.22,
    early_exercise: bool = True,
) -> ConversionPlan:
    """
    Add voluntary excess withdrawals post-RMD to fill the target bracket.

    Takes an existing plan and adds extra_withdrawals for years where
    RMD + SS don't fill the bracket, withdrawing more to top it off.
    This depletes the IRA faster, reducing future RMD pressure.
    The after-tax proceeds flow to brokerage (not Roth).

    Args:
        hh: Household parameters
        base_plan: Existing conversion plan to augment
        target_bracket: Fill up to this bracket (default 22%)
    """
    from engine.tax import BRACKETS_MFJ

    # Run the base scenario first to get IRA balances and bracket room
    result = run_scenario(hh, base_plan, "temp", end_age=95, early_exercise=early_exercise)

    # Find the bracket ceiling for the target rate
    bracket_ceiling = 0.0
    for ceil, rate in BRACKETS_MFJ:
        if rate <= target_bracket:
            bracket_ceiling = ceil
        else:
            break

    plan = ConversionPlan(
        your_conversions=dict(base_plan.your_conversions),
        spouse_conversions=dict(base_plan.spouse_conversions),
        qcds=dict(base_plan.qcds),
        spouse_qcds=dict(base_plan.spouse_qcds),
    )

    for yr in result.years:
        if yr.your_age < hh.your_rmd_start_age:
            continue  # only post-RMD

        # Room to fill the target bracket
        room = max(yr.total_deductions + bracket_ceiling - yr.combined_gross, 0)
        if room <= 0:
            continue

        # Allocate withdrawal: your IRA first, then spouse IRA for remainder.
        # Mirror the "older first, larger on tie" rule from _auto_fill_core:
        # in post-RMD years you are at or past your RMD age, so "you first"
        # is the natural primary source.
        your_available = max(yr.your_ira_begin - yr.your_rmd - yr.your_conversion, 0)
        your_extra = min(room, your_available)
        if your_extra > 1000:  # only if meaningful
            plan.extra_withdrawals[yr.year] = your_extra
            room -= your_extra

        # Offer spouse IRA for any remaining room (spouse still has balance)
        if room > 1000 and yr.spouse_ira_begin > yr.spouse_rmd:
            spouse_available = max(yr.spouse_ira_begin - yr.spouse_rmd - yr.spouse_conversion, 0)
            spouse_extra = min(room, spouse_available)
            if spouse_extra > 1000:
                plan.spouse_extra_withdrawals[yr.year] = spouse_extra

    return plan
