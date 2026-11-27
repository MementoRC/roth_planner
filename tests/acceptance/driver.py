"""Driver: the ONLY acceptance module that touches engine/ and models/.

Thin adapters over PUBLIC engine functions. No tax math lives here: every
number returned is produced by an engine call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from engine.income_control import build_income_control
from engine.ira import calc_rmd, rmd_divisor
from engine.irmaa import (
    BASE_PART_B,
    IRMAA_TIERS_MFJ,
    IRMAA_TIERS_SINGLE,
    irmaa_payment_year,
    irmaa_tier,
)
from engine.niit import niit
from engine.scenario import (
    ProjectionState,
    project_year_with_draw,
    run_no_conversion,
    run_scenario,
)
from engine.scenario_autofill import auto_fill_irmaa_safe
from engine.scenario_types import ConversionPlan
from engine.tax import estimate_ytd_federal_tax, senior_bonus_deduction, taxable_ss
from engine.withdrawal_waterfall import WaterfallDraw
from engine.yr_reprice import reprice
from models.household import GrowthProfile, Household
from models.ytd_income import YTDSnapshot
from views.income_control import (
    HARVEST_ENTER_BASIS,
    HARVEST_USED_BY_CONVERSION,
    harvest_notes,
    harvest_text,
)

_MONTHS = 12
_MEDICARE_AGE = 66
_MAGI_TOLERANCE = 1.0
_NO_INFLATION = 0.0
_FRA_AGE = 67
_BENEFIT_TOLERANCE = 1.0


def distribution_period(
    age: int, beneficiary_age: int | None, *, spouse_sole_beneficiary: bool = True
) -> float:
    """engine.ira.rmd_divisor (Uniform Lifetime or Joint-Life table as the engine selects)."""
    return rmd_divisor(age, beneficiary_age, spouse_sole_beneficiary=spouse_sole_beneficiary)


def rmd_for(
    balance: float,
    age: int,
    beneficiary_age: int | None,
    *,
    spouse_sole_beneficiary: bool = True,
) -> float:
    """engine.ira.calc_rmd for a lifetime owner (no first-year deferral)."""
    return calc_rmd(
        balance,
        age,
        beneficiary_age=beneficiary_age,
        spouse_sole_beneficiary=spouse_sole_beneficiary,
    )


def part_b_premium(status: str, magi: float, year: int) -> tuple[float, float]:
    """Monthly (IRMAA, total Part B) for ``year`` given the MAGI that sets it.

    ``status`` is an engine filing status ("Single" / "MFJ"). The tier comes
    from engine.irmaa.irmaa_tier; the tier totals and the base premium are the
    engine's published tables (annual, per person), shown monthly.
    """
    tiers = IRMAA_TIERS_SINGLE if status == "Single" else IRMAA_TIERS_MFJ
    tier = irmaa_tier(magi, status, year=year)
    annual_total = BASE_PART_B if tier == 0 else tiers[tier - 1][1]
    total = annual_total / _MONTHS
    return total - BASE_PART_B / _MONTHS, total


def magi_that_sets_premium(magi_by_year: Mapping[int, float], premium_year: int) -> float:
    """Pick the income-year MAGI the engine's lookback ties to ``premium_year``."""
    for income_year, magi in magi_by_year.items():
        if irmaa_payment_year(income_year) == premium_year:
            return magi
    raise AssertionError(
        f"no income year in {sorted(magi_by_year)} feeds the {premium_year} premium "
        f"under engine.irmaa.irmaa_payment_year"
    )


def niit_for(status: str, magi: float, nii: float) -> float:
    """engine.niit.niit."""
    return niit(magi, nii, status)


def projected_part_b_premium(
    status: str, magi_by_year: Mapping[int, float], year: int
) -> tuple[float, float]:
    """Monthly (IRMAA, total Part B) for ``year`` from a one-year engine projection.

    A synthetic single-filer household aged 66 earns only wages equal to
    ``magi_by_year[year]``; every other entry is filed MAGI for the engine's
    lookback. engine.scenario.run_no_conversion prices the year's IRMAA
    (annual Part B surcharge, YearResult.irmaa_part_b, shown monthly) on top of
    the household's Part B base. The projected MAGI must reproduce the stated
    figure, proving that income really was low and that the engine ignored it.
    """
    hh = Household(
        your_age=_MEDICARE_AGE,
        spouse_age=0,
        filing_status=status,
        base_year=year,
        cpi_assumption=0.0,
        your_ira=0.0,
        spouse_ira=0.0,
        your_roth=0.0,
        spouse_roth=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        grants=[],
        brokerage_start=0.0,
        living_expenses=0.0,
        medicare_part_b_base_monthly=BASE_PART_B / _MONTHS,
        prior_year_magi={y: m for y, m in magi_by_year.items() if y != year},
    )
    ytd = YTDSnapshot(tax_year=year, wages_ytd=magi_by_year[year])
    projected = run_no_conversion(hh, end_age=_MEDICARE_AGE + 1, ytd=ytd).years[0]
    assert projected.year == year
    assert abs(projected.magi - magi_by_year[year]) <= _MAGI_TOLERANCE, projected.magi
    irmaa = projected.irmaa_part_b / _MONTHS
    return irmaa, hh.medicare_part_b_base_monthly + irmaa


@dataclass(frozen=True)
class TaxOutcome:
    ordinary_tax: float
    ltcg_tax: float
    total: float
    magi: float
    ltcg_at_0: float = 0.0
    ltcg_at_15: float = 0.0
    ltcg_at_20: float = 0.0


def federal_tax_outcome(
    status: str,
    *,
    your_age: int,
    spouse_age: int,
    year: int,
    wages: float,
    conversion: float = 0.0,
    ltcg: float = 0.0,
) -> TaxOutcome:
    """engine.tax.estimate_ytd_federal_tax on a one-year snapshot (standard deduction)."""
    hh = Household(
        your_age=your_age,
        spouse_age=spouse_age,
        filing_status=status,
        base_year=year,
        cpi_assumption=0.0,
    )
    ytd = YTDSnapshot(tax_year=year, wages_ytd=wages, ira_conversions_ytd=conversion, ltcg_ytd=ltcg)
    est = estimate_ytd_federal_tax(ytd, hh)
    return TaxOutcome(
        est.ordinary_tax,
        est.ltcg_tax,
        est.total,
        ytd.magi_ytd,
        est.ltcg_at_0,
        est.ltcg_at_15,
        est.ltcg_at_20,
    )


def taxable_social_security(
    status: str,
    *,
    benefits: float,
    other_income: float,
    tax_exempt_interest: float,
    year: int,
) -> float:
    """engine.tax.taxable_ss, fed the MAGI the engine itself builds (YTDSnapshot.magi_ytd).

    ``other_income`` is entered as an IRA distribution; tax-exempt interest is
    its own snapshot field, which magi_ytd folds into provisional income.
    """
    ytd = YTDSnapshot(
        tax_year=year,
        ira_distributions_ytd=other_income,
        tax_exempt_interest_ytd=tax_exempt_interest,
    )
    return taxable_ss(benefits, ytd.magi_ytd, filing_status=status)


def enhanced_senior_deduction(
    status: str, *, your_age: int, spouse_age: int, magi: float, year: int
) -> float:
    """engine.tax.senior_bonus_deduction (the OBBBA enhanced deduction for seniors)."""
    return senior_bonus_deduction(
        your_age, spouse_age, magi, year=year, cpi=_NO_INFLATION, filing_status=status
    )


@dataclass(frozen=True)
class RothYear:
    year: int
    age: int
    converted: float
    roth_draw: float
    ira_draw: float
    early_withdrawal_penalty: float
    gross_income: float = 0.0
    # YearResult.magi (IRMAA basis; also what feeds the 2-year lookback) and
    # YearResult.aca_magi (IRC 36B(d)(2)(B) basis).
    magi: float = 0.0
    aca_magi: float = 0.0
    # The earnings part of this year's Roth draw, as the engine reports it.
    # None while the engine does not expose
    # YearResult.forced_your_roth_earnings_draw.
    roth_earnings_drawn: float | None = None
    # Engine requirement 5: Expose YearResult.irmaa_lookback_magi (MAGI from
    # year-2 used for that year's IRMAA). None while the engine lacks it.
    irmaa_lookback_magi: float | None = None


def roth_conversion_projection(
    *,
    age: int,
    base_year: int,
    ira_balance: float,
    conversions: Mapping[int, float],
    living_expenses: float,
    years: int,
    roth_balance: float = 0.0,
) -> list[RothYear]:
    """engine.scenario.run_scenario for a single filer living on a converted Roth.

    The household starts with a traditional IRA, a Roth balance of
    ``roth_balance`` (regular contributions; zero by default), no wages,
    no Social Security and no brokerage account. ``conversions`` (year ->
    amount) is projected in full (no bracket cap). Living expenses the engine
    cannot fund from other income are drawn by the engine's own withdrawal
    waterfall (brokerage, then traditional IRA, then Roth). Every figure is
    read from the projection's YearResult rows.
    """
    hh = Household(
        your_age=age,
        spouse_age=0,
        filing_status="Single",
        base_year=base_year,
        cpi_assumption=_NO_INFLATION,
        your_ira=ira_balance,
        spouse_ira=0.0,
        your_roth=roth_balance,
        spouse_roth=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        grants=[],
        brokerage_start=0.0,
        living_expenses=living_expenses,
    )
    plan = ConversionPlan(your_conversions=dict(conversions), bracket_cap=False)
    result = run_scenario(hh, plan, end_age=age + years)
    return [
        RothYear(
            year=yr.year,
            age=yr.your_age,
            converted=yr.your_conversion,
            roth_draw=yr.forced_your_roth_draw,
            ira_draw=yr.forced_your_ira_draw,
            early_withdrawal_penalty=yr.forced_early_withdrawal_penalty,
            gross_income=yr.combined_gross,
            magi=yr.magi,
            aca_magi=yr.aca_magi,
            roth_earnings_drawn=getattr(yr, "forced_your_roth_earnings_draw", None),
            irmaa_lookback_magi=getattr(yr, "irmaa_lookback_magi", None),
        )
        for yr in result.years
    ]


@dataclass(frozen=True)
class RetireeReturn:
    taxable_benefits: float
    agi: float
    standard_deduction: float
    senior_deduction: float
    taxable_income: float
    ordinary_tax: float
    total_tax: float


def retiree_return(
    *, age: int, year: int, benefits: float, ira_distribution: float
) -> RetireeReturn:
    """Whole-year MFJ federal return for two spouses of ``age`` on SS plus an IRA distribution.

    Every figure is read from the engine.tax.estimate_ytd_federal_tax result.
    """
    hh = Household(
        your_age=age,
        spouse_age=age,
        filing_status="MFJ",
        base_year=year,
        cpi_assumption=_NO_INFLATION,
    )
    ytd = YTDSnapshot(tax_year=year, ira_distributions_ytd=ira_distribution)
    est = estimate_ytd_federal_tax(ytd, hh, combined_ss=benefits)
    return RetireeReturn(
        taxable_benefits=est.taxable_ss,
        agi=est.agi,
        standard_deduction=est.standard_deduction,
        senior_deduction=est.senior_deduction,
        taxable_income=est.taxable_income,
        ordinary_tax=est.ordinary_tax,
        total_tax=est.total,
    )


@dataclass(frozen=True)
class StackOutcome:
    ordinary_stack_total: float  # ordinary lines, Social Security taken as taxable_ss_amt
    combined_gross: float
    conversion_done: float
    conversion_remaining: float


def income_control_stack(
    *,
    age: int,
    base_year: int,
    ira_balance: float,
    wages_ytd: float,
    converted_ytd: float,
    planned_conversion: float,
) -> StackOutcome:
    """engine.income_control.build_income_control for a single filer with this year's actuals."""
    hh = Household(
        your_age=age,
        spouse_age=0,
        filing_status="Single",
        base_year=base_year,
        cpi_assumption=_NO_INFLATION,
        your_ira=ira_balance,
        spouse_ira=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        grants=[],
        brokerage_start=0.0,
        living_expenses=0.0,
    )
    plan = ConversionPlan(your_conversions={base_year: planned_conversion}, bracket_cap=False)
    ytd = YTDSnapshot(tax_year=base_year, wages_ytd=wages_ytd, ira_conversions_ytd=converted_ytd)
    row = build_income_control(hh, plan, plan_label="acceptance", ytd=ytd).years[0]
    yr = row.year_result
    conversion = next(line for line in row.stack_lines if line.key == "conversion")
    ordinary = sum(
        yr.taxable_ss_amt if line.key == "social_security" else line.full_year
        for line in row.stack_lines
        if not line.magi_only
    )
    return StackOutcome(ordinary, yr.combined_gross, conversion.done, conversion.remaining)


@dataclass(frozen=True)
class HarvestView:
    amount: float
    binding: str
    detail: str
    taxable_ss_at_harvest: float
    conversion_uses_room_note: bool
    enter_basis_note: bool
    federal_tax: float = 0.0  # YearResult.federal_tax_amt + YearResult.brokerage_gain_tax
    magi_phaseout_basis: float = 0.0  # YearResult.magi_phaseout_basis, before any harvest
    year_end_unrealized_gain: float = 0.0  # YearResult.brokerage_balance_end - brokerage_basis
    ltcg_zero_room: float = 0.0  # the year's 0% LTCG room (room_after, key ltcg_zero)
    ltcg_room_without_conversion: float | None = None  # the IncomeControlYear field of that name
    opening_balance: float = 0.0  # YearResult.brokerage_balance, at the start of the year


def income_control_harvest(
    *,
    status: str,
    age: int,
    year: int,
    ira_balance: float,
    conversion: float,
    ira_distribution: float,
    social_security: float,
    brokerage_balance: float,
    brokerage_basis: float | None,
    tax_paid_from_cash: bool = False,
    brokerage_return: float = 0.0,
    turnover: float = 0.0,
    standard_deduction: float | None = None,
    ytd_net_long_term: float = 0.0,
    living_expenses: float = 0.0,
    returns: Mapping[int, float] | None = None,
    view_year: int | None = None,
) -> HarvestView:
    """The ``year`` row's 0% LTCG harvest from engine.income_control.build_income_control.

    Two spouses of ``age`` filing as ``status`` with a traditional IRA and no ACA coverage; the
    brokerage account has no dividends. ``living_expenses``, its ``brokerage_return`` (total
    annual return, all appreciation) and ``turnover`` default to none; ``standard_deduction``
    overrides the standard deduction (a stand-in for large itemized deductions) and
    ``ytd_net_long_term`` is the year-to-date net long-term gain (negative: a loss).
    ``social_security`` is the annual benefit, entered as the monthly benefit at full retirement
    age; the engine's own combined_ss is checked against it. An IRA distribution is a
    year-to-date actual and a conversion is planned. The detail line and notes are the Income
    Control view's own text; no tax figure is computed here.
    ``returns`` (year -> total return) override ``brokerage_return`` in those years; ``view_year``
    (default ``year``, the base year) names a later forecast year whose row is read instead.
    """
    hh = Household(
        your_age=age,
        spouse_age=age,
        filing_status=status,
        base_year=year,
        cpi_assumption=_NO_INFLATION,
        your_ira=ira_balance,
        spouse_ira=0.0,
        your_roth=0.0,
        spouse_roth=0.0,
        your_ss_fra=social_security / _MONTHS,
        your_ss_start_age=_FRA_AGE,
        spouse_ss_fra=0.0,
        grants=[],
        brokerage_start=brokerage_balance,
        brokerage_start_basis=brokerage_basis,
        brokerage_growth=GrowthProfile(
            default_rate=brokerage_return, yearly_overrides=dict(returns or {}), yield_rate=0.0
        ),
        brok_turnover=turnover,
        living_expenses=living_expenses,
        your_aca_enrolled=False,
        spouse_aca_enrolled=False,
        income_tax_paid_from_outside_cash=tax_paid_from_cash,
    )
    if standard_deduction is not None:
        hh.std_deduction = standard_deduction
    plan = ConversionPlan(your_conversions={year: conversion}, bracket_cap=False)
    ytd: YTDSnapshot | None = None
    if ira_distribution or ytd_net_long_term:
        ytd = YTDSnapshot(
            tax_year=year, ira_distributions_ytd=ira_distribution, ltcg_ytd=ytd_net_long_term
        )
    rows = build_income_control(hh, plan, plan_label="acceptance", ytd=ytd).years
    row = next(r for r in rows if r.year == (view_year or year))
    harvest = row.harvest
    assert harvest is not None
    yr = row.year_result
    assert abs(yr.combined_ss - social_security) <= _BENEFIT_TOLERANCE, yr.combined_ss
    notes = harvest_notes(row)
    return HarvestView(
        amount=harvest.amount,
        binding=harvest.binding,
        detail=harvest_text(harvest),
        taxable_ss_at_harvest=reprice(yr, 0.0, harvest.amount, hh.cpi_assumption).taxable_ss,
        conversion_uses_room_note=HARVEST_USED_BY_CONVERSION in notes,
        enter_basis_note=HARVEST_ENTER_BASIS in notes,
        federal_tax=yr.federal_tax_amt + yr.brokerage_gain_tax,
        magi_phaseout_basis=yr.magi_phaseout_basis,
        year_end_unrealized_gain=yr.brokerage_balance_end - yr.brokerage_basis,
        ltcg_zero_room=row.rooms["ltcg_zero"] or 0.0,
        ltcg_room_without_conversion=row.ltcg_room_without_conversion,
        opening_balance=yr.brokerage_balance,
    )


@dataclass(frozen=True)
class FundedYear:
    realized_gains: float  # YearResult.realized_gains
    federal_tax: float  # YearResult.federal_tax_amt + YearResult.brokerage_gain_tax
    magi: float  # YearResult.magi
    shortfall: float  # YearResult.income_needed


def _project_funded(hh: Household, plan: ConversionPlan, draw: WaterfallDraw | None) -> FundedYear:
    state = ProjectionState.initial(hh)
    yr = project_year_with_draw(0, hh, plan, None, 0.0, state, draw).yr
    return FundedYear(
        realized_gains=yr.realized_gains,
        federal_tax=yr.federal_tax_amt + yr.brokerage_gain_tax,
        magi=yr.magi,
        shortfall=yr.income_needed,
    )


def brokerage_funded_year(
    *,
    age: int,
    year: int,
    living_expenses: float,
    brokerage_balance: float,
    brokerage_basis: float,
    explicit_sale: bool,
) -> FundedYear:
    """Two spouses of ``age`` with no income, living off a brokerage account, in ``year``.

    Entry point: engine.scenario.project_year_with_draw, which engine/scenario_autofill.py
    calls. With ``explicit_sale`` False (draw=None) the shortfall is funded by the engine's
    expense debit. With it True, the same shortfall (the engine's own yr.income_needed from
    that first projection) is funded as a forced brokerage draw, which realizes gain and
    prices it in the engine; the WaterfallDraw's own realized_gain slot is not read there.
    """
    hh = Household(
        your_age=age,
        spouse_age=age,
        filing_status="MFJ",
        base_year=year,
        cpi_assumption=_NO_INFLATION,
        your_ira=0.0,
        spouse_ira=0.0,
        your_roth=0.0,
        spouse_roth=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        grants=[],
        brokerage_start=brokerage_balance,
        brokerage_start_basis=brokerage_basis,
        brokerage_growth=GrowthProfile(default_rate=0.0, yield_rate=0.0),
        brok_turnover=0.0,
        living_expenses=living_expenses,
        your_aca_enrolled=False,
        spouse_aca_enrolled=False,
    )
    plan = ConversionPlan(your_conversions={}, bracket_cap=False)
    debit = _project_funded(hh, plan, None)
    if not explicit_sale:
        return debit
    sale = WaterfallDraw(
        brokerage_draw=debit.shortfall,
        realized_gain=0.0,
        your_ira_draw=0.0,
        spouse_ira_draw=0.0,
        early_withdrawal_penalty=0.0,
        roth_draw=0.0,
        unfunded=0.0,
        iterations=0,
        converged=True,
    )
    return _project_funded(hh, plan, sale)


_CAPITAL_BALANCE = 1_000_000.0
_CAPITAL_YEARS = 2


@dataclass(frozen=True)
class CapitalYear:
    year: int
    realized_gains: float  # YearResult.realized_gains (negative: a net capital loss)
    magi: float  # YearResult.magi
    ordinary_gross: float  # YearResult.combined_gross
    taxable_income: float  # YearResult.taxable_income (ordinary income only)
    federal_tax: float  # YearResult.federal_tax_amt + YearResult.brokerage_gain_tax


def capital_gain_years(
    *,
    age: int,
    year: int,
    conversion: float,
    turnover: float,
    returns: Mapping[int, float],
    qualified_dividends: Mapping[int, float] | None = None,
    ytd_wages: float = 0.0,
    ytd_net_long_term: float = 0.0,
    living_expenses: float = 0.0,
    brokerage_basis: float | None = None,
    autofill: bool = False,
    prior_lt_carryover: float = 0.0,
) -> list[CapitalYear]:
    """Forecast years of an MFJ couple of ``age`` whose brokerage account gains or loses.

    engine.scenario.run_scenario for a traditional IRA with ``conversion`` planned in ``year``
    (its tax paid from cash), no other income and no ACA coverage. The $1,000,000 brokerage
    account pays no dividends but ``qualified_dividends`` (year -> dollars); ``turnover`` of it
    is sold each year and ``returns`` (year -> appreciation) sets the gain or loss; a year's
    dividend yield is added to its total return so the appreciation is exactly ``returns``.
    Every figure is a YearResult field. ``ytd_wages`` and ``ytd_net_long_term`` (a loss is
    negative) are the base year's YTD actuals, wages and net long-term gain.
    ``living_expenses`` is the yearly cash need (a shortfall is funded by selling brokerage
    shares, which realizes gain); ``brokerage_basis`` is the account's cost basis (None: engine default).
    """
    yields = {y: amount / _CAPITAL_BALANCE for y, amount in (qualified_dividends or {}).items()}
    totals = dict(returns)
    for yield_year, dividend_yield in yields.items():
        totals[yield_year] = totals.get(yield_year, 0.0) + dividend_yield
    hh = Household(
        your_age=age,
        spouse_age=age,
        filing_status="MFJ",
        base_year=year,
        cpi_assumption=_NO_INFLATION,
        your_ira=_CAPITAL_BALANCE,
        spouse_ira=0.0,
        your_roth=0.0,
        spouse_roth=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        grants=[],
        brokerage_start=_CAPITAL_BALANCE,
        brokerage_start_basis=brokerage_basis,
        brokerage_growth=GrowthProfile(
            default_rate=0.0, yearly_overrides=totals, yield_overrides=yields, yield_rate=0.0
        ),
        brok_turnover=turnover,
        living_expenses=living_expenses,
        your_aca_enrolled=False,
        spouse_aca_enrolled=False,
        income_tax_paid_from_outside_cash=True,
    )
    if prior_lt_carryover:
        # Household.prior_year_lt_loss_carryover (#137); plain attribute until the field exists.
        hh.prior_year_lt_loss_carryover = prior_lt_carryover
    plan = ConversionPlan(your_conversions={year: conversion}, bracket_cap=False)
    ytd = (
        YTDSnapshot(tax_year=year, wages_ytd=ytd_wages, ltcg_ytd=ytd_net_long_term)
        if ytd_wages or ytd_net_long_term
        else None
    )
    plan = (
        auto_fill_irmaa_safe(hh, ytd)
        if autofill
        else ConversionPlan(your_conversions={year: conversion}, bracket_cap=False)
    )
    result = run_scenario(hh, plan, "acceptance", end_age=age + _CAPITAL_YEARS, ytd=ytd)
    return [
        CapitalYear(
            year=yr.year,
            realized_gains=yr.realized_gains,
            magi=yr.magi,
            ordinary_gross=yr.combined_gross,
            taxable_income=yr.taxable_income,
            federal_tax=yr.federal_tax_amt + yr.brokerage_gain_tax,
        )
        for yr in result.years
    ]
