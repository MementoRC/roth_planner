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
from engine.tax import estimate_ytd_federal_tax
from models.household import Household
from models.ytd_income import YTDSnapshot

_MONTHS = 12
_MEDICARE_AGE = 66
_MAGI_TOLERANCE = 1.0


def distribution_period(age: int, beneficiary_age: int | None) -> float:
    """engine.ira.rmd_divisor (Uniform Lifetime or Joint-Life table as the engine selects)."""
    return rmd_divisor(age, beneficiary_age)


def rmd_for(balance: float, age: int, beneficiary_age: int | None) -> float:
    """engine.ira.calc_rmd for a lifetime owner (no first-year deferral)."""
    return calc_rmd(balance, age, beneficiary_age=beneficiary_age)


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
