"""Domain-language helpers for the acceptance steps. Built on driver.py only.

Interpretation choices (also reported to the owner):
- "single" -> engine "Single"; "married filing jointly" -> engine "MFJ".
- "both under 65" -> ages 60/60: no aged additional deduction and no OBBBA
  senior bonus, so the deduction is the plain standard deduction.
- "turns 75 in 2026" / "is 73 in 2026" -> the owner's RMD age is 75 / 73.
- "spouse 6 years younger" -> beneficiary_age = owner age - 6; the engine
  decides whether the Joint-Life table applies (it only does for >10 years).
- Dollar amounts: "$4,065", "$202.90" -> floats. RMD is compared after
  rounding to the nearest dollar (the publication rounds); money-per-month
  values are compared to the cent.
- Federal tax with/without a conversion or gain: the engine's full YTD
  federal estimate for a household with only wages (+ conversion / LTCG).
- "$X of the gain is taxed at 15%" is recovered from the engine's LTCG tax
  (driver.ltcg_taxed_at_15); the 0% part is the remainder of the gain.
- Part B figures are monthly, per person; "2026 premium" uses the 2026
  engine tier tables; the lookback income year comes from the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tests.acceptance import driver

_STATUS = {"single": "Single", "married filing jointly": "MFJ"}
_UNDER_65 = 60
_CENT = 0.005


def money(text: str) -> float:
    """'$4,065' / '$202.90' / '24.6' -> float."""
    cleaned = re.sub(r"[$,\s]", "", text)
    return float(cleaned)


def status_of(text: str) -> str:
    return _STATUS[text.strip().lower()]


def close_to_cent(actual: float, expected: float) -> bool:
    return abs(actual - expected) < _CENT


@dataclass
class Ira:
    owner_age: int
    beneficiary_age: int | None = None
    balance: float = 0.0
    year: int = 2026


def an_ira_owner(age: int) -> Ira:
    return Ira(owner_age=age)


def with_spouse_beneficiary_younger_by(ira: Ira, years: int) -> None:
    ira.beneficiary_age = ira.owner_age - years


def rmd_period(ira: Ira) -> float:
    return driver.distribution_period(ira.owner_age, ira.beneficiary_age)


def rmd_amount(ira: Ira) -> int:
    return round(driver.rmd_for(ira.balance, ira.owner_age, ira.beneficiary_age))


@dataclass
class Beneficiary:
    status: str
    magi_by_year: dict[int, float] = field(default_factory=dict)


def premium_for(person: Beneficiary, year: int) -> tuple[float, float]:
    magi = driver.magi_that_sets_premium(person.magi_by_year, year)
    return driver.part_b_premium(person.status, magi, year)


def niit_owed(status: str, magi: float, nii: float) -> float:
    return driver.niit_for(status, magi, nii)


@dataclass
class Household:
    status: str
    wages: float
    year: int


def a_household(status: str, wages: float, year: int) -> Household:
    return Household(status=status, wages=wages, year=year)


def _outcome(hh: Household, *, conversion: float = 0.0, ltcg: float = 0.0) -> driver.TaxOutcome:
    return driver.federal_tax_outcome(
        hh.status,
        your_age=_UNDER_65,
        spouse_age=_UNDER_65,
        year=hh.year,
        wages=hh.wages,
        conversion=conversion,
        ltcg=ltcg,
    )


def baseline(hh: Household) -> driver.TaxOutcome:
    return _outcome(hh)


def convert_to_roth(hh: Household, amount: float) -> driver.TaxOutcome:
    return _outcome(hh, conversion=amount)


def realize_ltcg(hh: Household, amount: float) -> driver.TaxOutcome:
    return _outcome(hh, ltcg=amount)


def added_tax(before: driver.TaxOutcome, after: driver.TaxOutcome) -> float:
    return after.total - before.total


def gain_taxed_at_15(after: driver.TaxOutcome) -> float:
    return driver.ltcg_taxed_at_15(after)


def gain_taxed_at_0(after: driver.TaxOutcome, gain: float) -> float:
    return gain - driver.ltcg_taxed_at_15(after)
