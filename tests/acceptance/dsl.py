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
- "$X of the gain is taxed at 0% / 15%" is read from the engine's own band
  amounts on the YTD estimate (ltcg_at_0 / ltcg_at_15).
- Part B figures are monthly, per person; "2026 premium" uses the 2026
  engine tier tables; the lookback income year comes from the engine.
- When a scenario also states the premium year's own MAGI, the premium comes
  from an engine projection of that year (a single filer aged 66 whose only
  income is wages equal to that MAGI, lookback MAGI filed): the projected MAGI
  must match the stated one, proving it was ignored, not just never read.
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
    spouse_sole_beneficiary: bool = True


def an_ira_owner(age: int) -> Ira:
    return Ira(owner_age=age)


def with_spouse_beneficiary_younger_by(ira: Ira, years: int) -> None:
    ira.beneficiary_age = ira.owner_age - years


def rmd_period(ira: Ira) -> float:
    return driver.distribution_period(
        ira.owner_age, ira.beneficiary_age, spouse_sole_beneficiary=ira.spouse_sole_beneficiary
    )


def rmd_amount(ira: Ira) -> int:
    return round(
        driver.rmd_for(
            ira.balance,
            ira.owner_age,
            ira.beneficiary_age,
            spouse_sole_beneficiary=ira.spouse_sole_beneficiary,
        )
    )


@dataclass
class Beneficiary:
    status: str
    magi_by_year: dict[int, float] = field(default_factory=dict)


def premium_for(person: Beneficiary, year: int) -> tuple[float, float]:
    if year in person.magi_by_year:
        return driver.projected_part_b_premium(person.status, person.magi_by_year, year)
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
    return after.ltcg_at_15


def gain_taxed_at_0(after: driver.TaxOutcome, gain: float) -> float:
    return after.ltcg_at_0


def with_spouse_sharing_the_ira(ira: Ira, years: int) -> None:
    """A younger spouse who is NOT the sole beneficiary.

    The spouse's age is set exactly as for a sole beneficiary; the IRA is marked
    shared, so the engine must decline the Joint-Life table despite the gap.
    """
    with_spouse_beneficiary_younger_by(ira, years)
    ira.spouse_sole_beneficiary = False


@dataclass
class BenefitsCase:
    status: str
    benefits: float
    other_income: float = 0.0
    tax_exempt_interest: float = 0.0
    year: int = 2026


def a_beneficiary_household(status: str, benefits: float) -> BenefitsCase:
    return BenefitsCase(status=status, benefits=benefits)


def taxable_benefits(case: BenefitsCase) -> float:
    return driver.taxable_social_security(
        case.status,
        benefits=case.benefits,
        other_income=case.other_income,
        tax_exempt_interest=case.tax_exempt_interest,
        year=case.year,
    )


_SENIOR_AGE = 66
_SENIOR_COUNT = {"neither": 0, "one": 1, "both": 2}


@dataclass
class SeniorCase:
    status: str
    seniors: int
    magi: float = 0.0
    year: int = 2026


def senior_count(word: str) -> int:
    return _SENIOR_COUNT[word.strip().lower()]


def a_senior_household(status: str, seniors: int) -> SeniorCase:
    return SeniorCase(status=status, seniors=seniors)


def _senior_deduction(case: SeniorCase, seniors: int) -> float:
    ages = [_SENIOR_AGE] * seniors + [_UNDER_65] * (2 - seniors)
    return driver.enhanced_senior_deduction(
        case.status, your_age=ages[0], spouse_age=ages[1], magi=case.magi, year=case.year
    )


def total_senior_deduction(case: SeniorCase) -> float:
    return _senior_deduction(case, case.seniors)


def senior_deduction_each(case: SeniorCase) -> float:
    """What the engine gives one qualifying spouse at this MAGI (zero if none qualify)."""
    return _senior_deduction(case, 1) if case.seniors else 0.0


@dataclass
class Retirees:
    age: int
    benefits: float = 0.0
    ira_distribution: float = 0.0
    year: int = 2026


def retired_couple(age: int) -> Retirees:
    return Retirees(age=age)


def retiree_return(couple: Retirees) -> driver.RetireeReturn:
    return driver.retiree_return(
        age=couple.age,
        year=couple.year,
        benefits=couple.benefits,
        ira_distribution=couple.ira_distribution,
    )


_BASE_YEAR = 2026


@dataclass
class RothConverter:
    """A single filer with a traditional IRA, no Roth IRA and no other income.

    Interpretation choices (also reported to the owner):
    - "converts the whole IRA" / "the rest of the IRA" plans a conversion of
      the full starting IRA balance; the engine converts what is left after it
      has funded that year's tax and expenses, so the converted amount is
      read from the projection, never assumed.
    - Every converted dollar was taxable at conversion (no basis).
    - "draws from the Roth IRA" means the engine's withdrawal waterfall funded
      the year's living expenses from the Roth, with no traditional IRA draw,
      and the Roth draws so far do not exceed the amounts converted (so only
      converted principal, never earnings, has come out).
    """

    age: int
    ira: float
    living_expenses: float = 0.0
    conversions: dict[int, float] = field(default_factory=dict)
    rows: list[driver.RothYear] = field(default_factory=list)


def a_roth_converter(age: int, ira: float) -> RothConverter:
    return RothConverter(age=age, ira=ira)


def converts(case: RothConverter, year: int, amount: float | None = None) -> None:
    """Plan a conversion; ``None`` means the whole (remaining) IRA."""
    case.conversions[year] = case.ira if amount is None else amount


def project_through(case: RothConverter, last_year: int) -> None:
    case.rows = driver.roth_conversion_projection(
        age=case.age,
        base_year=_BASE_YEAR,
        ira_balance=case.ira,
        conversions=case.conversions,
        living_expenses=case.living_expenses,
        years=last_year - _BASE_YEAR,
    )


def roth_year(case: RothConverter, year: int) -> driver.RothYear:
    for row in case.rows:
        if row.year == year:
            return row
    raise AssertionError(f"{year} is not in the projection {[r.year for r in case.rows]}")


def roth_draws_within_converted(case: RothConverter, year: int) -> bool:
    drawn = sum(r.roth_draw for r in case.rows if r.year <= year)
    converted = sum(r.converted for r in case.rows if r.year <= year)
    return drawn <= converted
