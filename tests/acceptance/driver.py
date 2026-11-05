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
from engine.tax import LTCG_RATES_MFJ, estimate_ytd_federal_tax
from models.household import Household
from models.ytd_income import YTDSnapshot

_MONTHS = 12


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


@dataclass(frozen=True)
class TaxOutcome:
    ordinary_tax: float
    ltcg_tax: float
    total: float
    magi: float


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
    return TaxOutcome(est.ordinary_tax, est.ltcg_tax, est.total, ytd.magi_ytd)


def ltcg_taxed_at_15(outcome: TaxOutcome) -> float:
    """Gain amount the engine's LTCG tax implies was taxed at 15% (MFJ).

    The engine exposes only the LTCG tax, not the band split, so the 15% band
    is recovered from engine.tax.LTCG_RATES_MFJ[1]. Valid only when no gain
    reaches the 20% band (true for the specs that use it).
    """
    return outcome.ltcg_tax / LTCG_RATES_MFJ[1]
