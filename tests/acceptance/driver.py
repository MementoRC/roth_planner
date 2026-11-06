"""Driver: the ONLY acceptance module that touches engine/ and models/.

Thin adapters over PUBLIC engine functions. No tax math lives here: every
number returned is produced by an engine call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from engine.ira import calc_rmd, rmd_divisor
from engine.irmaa import (
    BASE_PART_B,
    IRMAA_TIERS_MFJ,
    IRMAA_TIERS_SINGLE,
    irmaa_payment_year,
    irmaa_tier,
)
from engine.niit import niit
from engine.scenario import run_no_conversion
from engine.tax import estimate_ytd_federal_tax, senior_bonus_deduction, taxable_ss
from models.household import Household
from models.ytd_income import YTDSnapshot

_MONTHS = 12
_MEDICARE_AGE = 66
_MAGI_TOLERANCE = 1.0
_NO_INFLATION = 0.0


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
