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
- Income Control harvest: a couple "both 70" claims Social Security at full retirement
  age, so the engine is given the monthly benefit. "If it were untaxed" is the engine's own
  harvest for the same couple with no Social Security (at these incomes the senior bonus
  and the MAGI lines are unaffected). Harvest dollars match within $1, the engine's search
  tolerance. The brokerage balance is $1,000,000; "well below" is a $500,000 basis, and a
  stated unrealized gain is the balance less that gain as basis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

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
    """A single filer with a traditional IRA, an optional Roth IRA of regular
    contributions and no other income.

    Interpretation choices (also reported to the owner):
    - "converts the whole IRA" / "the rest of the IRA" plans a conversion of
      the full starting IRA balance; the engine converts what is left after it
      has funded that year's tax and expenses, so the converted amount is
      read from the projection, never assumed.
    - Every converted dollar was taxable at conversion (no basis).
    - "draws from the Roth IRA" means the engine's withdrawal waterfall funded
      the year's living expenses from the Roth, with no traditional IRA draw,
      and the Roth draws so far do not exceed the regular contributions plus
      the amounts converted (so only principal, never earnings, has come out).
    - "a $X Roth IRA of regular contributions" is the household's starting
      Roth balance; all of it is contributions (no earnings, no conversions).
    - "beyond everything they converted" means the Roth draws so far EXCEED
      the regular contributions plus the amounts converted, so the Roth's own
      growth (earnings) has started to come out. How much of a draw is
      earnings is read from the engine, never derived here.
    - "gross income" is the engine's YearResult.combined_gross for the year.
    """

    age: int
    ira: float
    roth: float = 0.0
    living_expenses: float = 0.0
    conversions: dict[int, float] = field(default_factory=dict)
    rows: list[driver.RothYear] = field(default_factory=list)


def a_roth_converter(age: int, ira: float, roth: float = 0.0) -> RothConverter:
    return RothConverter(age=age, ira=ira, roth=roth)


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
        roth_balance=case.roth,
    )


def roth_year(case: RothConverter, year: int) -> driver.RothYear:
    for row in case.rows:
        if row.year == year:
            return row
    raise AssertionError(f"{year} is not in the projection {[r.year for r in case.rows]}")


def roth_draws_within_converted(case: RothConverter, year: int) -> bool:
    drawn = sum(r.roth_draw for r in case.rows if r.year <= year)
    converted = sum(r.converted for r in case.rows if r.year <= year)
    return drawn <= case.roth + converted


def roth_draws_beyond_converted(case: RothConverter, year: int) -> bool:
    return not roth_draws_within_converted(case, year)


def irmaa_lookback_magi(row: driver.RothYear) -> float:
    """The engine's year-2 MAGI used for the year's IRMAA.

    Engine requirement 5: Expose YearResult.irmaa_lookback_magi (MAGI from
    year-2 used for that year's IRMAA).
    """
    if row.irmaa_lookback_magi is None:
        raise AssertionError(
            "engine must expose YearResult.irmaa_lookback_magi "
            f"(the MAGI from year-2 used for the {row.year} IRMAA)"
        )
    return row.irmaa_lookback_magi


def roth_earnings_drawn(row: driver.RothYear) -> float:
    """The engine's earnings part of the year's Roth draw."""
    if row.roth_earnings_drawn is None:
        raise AssertionError(
            "engine must expose YearResult.forced_your_roth_earnings_draw "
            f"(the earnings part of the {row.year} Roth draw)"
        )
    return row.roth_earnings_drawn


@dataclass
class IncomeControlCase:
    age: int
    ira: float
    wages_ytd: float = 0.0
    converted_ytd: float = 0.0
    planned_conversion: float = 0.0
    result: driver.StackOutcome | None = None


def an_income_control_filer(age: int, ira: float) -> IncomeControlCase:
    return IncomeControlCase(age=age, ira=ira)


def build_income_control_stack(case: IncomeControlCase, year: int) -> None:
    case.result = driver.income_control_stack(
        age=case.age,
        base_year=year,
        ira_balance=case.ira,
        wages_ytd=case.wages_ytd,
        converted_ytd=case.converted_ytd,
        planned_conversion=case.planned_conversion,
    )


_HARVEST_IRA = 1_000_000.0
_BROKERAGE_BALANCE = 1_000_000.0
_WELL_BELOW_BASIS = 500_000.0
_DOLLAR = 1.0
_STOPS_AT = {
    "the top of the 0% LTCG bracket": "0% LTCG",
    "the unrealized gain in the brokerage account": "embedded gain",
    "the point where more Social Security becomes taxable": "Social Security tax",
}


@dataclass
class HarvestCase:
    status: str = "MFJ"
    age: int = _UNDER_65
    year: int = 2026
    conversion: float = 0.0
    ira_distribution: float = 0.0
    social_security: float = 0.0
    brokerage_basis: float | None = None
    tax_paid_from_cash: bool = False
    brokerage_return: float = 0.0
    turnover: float = 0.0
    standard_deduction: float | None = None
    ytd_net_long_term: float = 0.0
    living_expenses: float = 0.0


def a_couple_under_65() -> HarvestCase:
    return HarvestCase()


def a_couple_receiving_social_security(age: int, benefits: float) -> HarvestCase:
    return HarvestCase(age=age, social_security=benefits)


def with_unrealized_gain(case: HarvestCase, gain: float) -> None:
    case.brokerage_basis = _BROKERAGE_BALANCE - gain


def with_basis_well_below_balance(case: HarvestCase) -> None:
    case.brokerage_basis = _WELL_BELOW_BASIS


def with_no_cost_basis(case: HarvestCase) -> None:
    case.brokerage_basis = None


def with_tax_paid_from_cash(case: HarvestCase) -> None:
    case.tax_paid_from_cash = True


def with_tax_paid_from_brokerage(case: HarvestCase) -> None:
    case.tax_paid_from_cash = False


def view_harvest(case: HarvestCase) -> driver.HarvestView:
    return driver.income_control_harvest(
        status=case.status,
        age=case.age,
        year=case.year,
        ira_balance=_HARVEST_IRA,
        conversion=case.conversion,
        ira_distribution=case.ira_distribution,
        social_security=case.social_security,
        brokerage_balance=_BROKERAGE_BALANCE,
        brokerage_basis=case.brokerage_basis,
        tax_paid_from_cash=case.tax_paid_from_cash,
        brokerage_return=case.brokerage_return,
        turnover=case.turnover,
        standard_deduction=case.standard_deduction,
        ytd_net_long_term=case.ytd_net_long_term,
        living_expenses=case.living_expenses,
    )


def harvest_without_social_security(case: HarvestCase) -> driver.HarvestView:
    return view_harvest(replace(case, social_security=0.0))


def within_a_dollar(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= _DOLLAR


def has_room(room: float) -> bool:
    """True iff ``room`` is a positive amount, past the $1 search tolerance."""
    return room > _DOLLAR


def stops_at(view: driver.HarvestView, phrase: str) -> bool:
    return view.binding == _STOPS_AT[phrase.strip()]


_AMOUNT_IN_HARVEST_LINE = re.compile(r"up to (\$[\d,]+(?:\.\d+)?)")
_TAX_TOLERANCE = 0.01  # "no extra tax" means at most a cent, as in tests/test_room_lines_harvest.py
_NON_TAX_BINDINGS = ("embedded gain", "ACA cliff", "IRMAA tier 1", "NIIT")


def percent(text: str) -> float:
    """'5%' -> 0.05."""
    return float(text.strip().rstrip("%")) / 100


def shown_harvest(view: driver.HarvestView) -> float:
    """The dollar amount the Income Control detail line shows."""
    found = _AMOUNT_IN_HARVEST_LINE.search(view.detail)
    assert found, view.detail
    return money(found.group(1))


def with_a_year_to_date_net_capital_loss(case: HarvestCase, loss: float) -> None:
    """A net long-term loss realized so far this year, entered as the snapshot's net LTCG."""
    case.ytd_net_long_term = -loss


def with_growth_and_turnover(case: HarvestCase, growth: float, turnover: float) -> None:
    case.brokerage_return = growth
    case.turnover = turnover


def with_itemized_deductions(case: HarvestCase, amount: float) -> None:
    """Large itemized deductions, entered as the engine's standard-deduction input."""
    case.standard_deduction = amount


def harvest_without_conversion(case: HarvestCase) -> driver.HarvestView:
    return view_harvest(replace(case, conversion=0.0))


def extra_tax_from_realizing(case: HarvestCase, view: driver.HarvestView, gain: float) -> float:
    """Federal tax added for the year by realizing ``gain`` more long-term gain.

    The gain enters as the base-year ``ltcg_ytd`` actual, as tests/test_room_lines_harvest.py
    does: the scenario has no one-off realized-gain input.
    """
    realized = replace(case, ytd_net_long_term=case.ytd_net_long_term + gain)
    return view_harvest(realized).federal_tax - view.federal_tax


def adds_no_tax(extra: float) -> bool:
    return extra <= _TAX_TOLERANCE


def binds_on_a_non_tax_limit(view: driver.HarvestView) -> bool:
    return view.binding in _NON_TAX_BINDINGS


def stops_where_magi_reaches(view: driver.HarvestView, threshold: float) -> bool:
    """True iff the engine's pre-harvest phase-out MAGI plus the harvest lands on ``threshold``."""
    return within_a_dollar(view.magi_phaseout_basis + view.amount, threshold)


def is_described_as_stopping_at(view: driver.HarvestView, phrase: str) -> bool:
    """True iff the harvest's binding label or its detail line names ``phrase``."""
    return stops_at(view, phrase) or phrase.strip() in view.detail


@dataclass
class ShortfallCase:
    age: int
    year: int = _BASE_YEAR
    living_expenses: float = 0.0
    brokerage_balance: float = 0.0
    brokerage_basis: float = 0.0


def a_couple_with_no_income(age: int) -> ShortfallCase:
    return ShortfallCase(age=age)


def _funded(case: ShortfallCase, *, explicit_sale: bool) -> driver.FundedYear:
    return driver.brokerage_funded_year(
        age=case.age,
        year=case.year,
        living_expenses=case.living_expenses,
        brokerage_balance=case.brokerage_balance,
        brokerage_basis=case.brokerage_basis,
        explicit_sale=explicit_sale,
    )


def funded_by_expense_debit(case: ShortfallCase) -> driver.FundedYear:
    return _funded(case, explicit_sale=False)


def funded_by_explicit_sale(case: ShortfallCase) -> driver.FundedYear:
    return _funded(case, explicit_sale=True)


@dataclass
class CapitalCase:
    turnover: float
    year: int = _BASE_YEAR
    conversion: float = 0.0
    returns: dict[int, float] = field(default_factory=dict)
    qualified_dividends: dict[int, float] = field(default_factory=dict)
    ytd_wages: float = 0.0
    living_expenses: float = 0.0
    brokerage_basis: float | None = None
    ytd_net_long_term: float = 0.0


def a_couple_under_65_with_turnover(turnover: float) -> CapitalCase:
    return CapitalCase(turnover=turnover)


def with_return_in(case: CapitalCase, year: int, rate: float) -> None:
    case.returns[year] = rate


def with_qualified_dividends_in(case: CapitalCase, year: int, amount: float) -> None:
    case.qualified_dividends[year] = amount


def with_year_to_date_actuals(case: CapitalCase, wages: float, net_loss: float) -> None:
    """Base-year actuals as the snapshot's wages and net long-term gain (a loss is negative)."""
    case.ytd_wages = wages
    case.ytd_net_long_term = -net_loss


def capital_years(case: CapitalCase) -> dict[int, driver.CapitalYear]:
    years = driver.capital_gain_years(
        age=_UNDER_65,
        year=case.year,
        conversion=case.conversion,
        turnover=case.turnover,
        returns=case.returns,
        qualified_dividends=case.qualified_dividends,
        ytd_wages=case.ytd_wages,
        ytd_net_long_term=case.ytd_net_long_term,
        living_expenses=case.living_expenses,
        brokerage_basis=case.brokerage_basis,
    )
    return {yr.year: yr for yr in years}


def capital_years_without_the_loss(case: CapitalCase, year: int) -> dict[int, driver.CapitalYear]:
    return capital_years(replace(case, returns={**case.returns, year: 0.0}))
