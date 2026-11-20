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
from engine.scenario import run_no_conversion, run_scenario
from engine.scenario_types import ConversionPlan
from engine.tax import estimate_ytd_federal_tax, senior_bonus_deduction, taxable_ss
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
) -> HarvestView:
    """The ``year`` row's 0% LTCG harvest from engine.income_control.build_income_control.

    Two spouses of ``age`` filing as ``status`` with a traditional IRA, no ACA coverage and no
    living expenses; the brokerage account has no growth, dividends or turnover.
    ``social_security`` is the annual benefit, entered as the monthly benefit at full retirement
    age; the engine's own combined_ss is checked against it. An IRA distribution is a
    year-to-date actual and a conversion is planned. The detail line and notes are the Income
    Control view's own text; no tax figure is computed here.
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
        brokerage_growth=GrowthProfile(default_rate=0.0, yield_rate=0.0),
        brok_turnover=0.0,
        living_expenses=0.0,
        your_aca_enrolled=False,
        spouse_aca_enrolled=False,
        income_tax_paid_from_outside_cash=tax_paid_from_cash,
    )
    plan = ConversionPlan(your_conversions={year: conversion}, bracket_cap=False)
    ytd: YTDSnapshot | None = None
    if ira_distribution:
        ytd = YTDSnapshot(tax_year=year, ira_distributions_ytd=ira_distribution)
    rows = build_income_control(hh, plan, plan_label="acceptance", ytd=ytd).years
    row = next(r for r in rows if r.year == year)
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
    )
