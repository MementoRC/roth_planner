"""Income Control room lines (issue #123, slice 1): where each tax/cost line sits
in a year, and how much room a year's income leaves under it.

Pure lookup + subtraction -- NO tax math. Thresholds come from the same helpers
the scenario uses; measures come from run_scenario's YearResult. NO Streamlit
imports (engine purity). Must not import engine.this_year.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.aca import aca_applies, aca_ceiling_magi
from engine.irmaa import irmaa_tier1_threshold
from engine.niit import niit_threshold
from engine.scenario_types import YearResult
from engine.tax import LTCG_THRESHOLDS_MFJ, LTCG_THRESHOLDS_SINGLE, indexed_bracket_ceiling
from engine.tax_indexing import index_tuple
from models.household import Household
from models.ytd_income import YTDSnapshot

# Income at this age is the first the 2-year IRMAA lookback can price into Medicare.
IRMAA_FIRST_INCOME_AGE = 63

LINE_KEYS: tuple[str, ...] = (
    "top_12",
    "top_22",
    "top_24",
    "ltcg_zero",
    "irmaa_t1",
    "aca_cliff",
    "niit",
)


@dataclass(frozen=True)
class RoomLines:
    year: int
    filing_status: str
    top_12: float
    top_22: float
    top_24: float
    ltcg_zero: float  # 0% LTCG taxable-income threshold (indexed)
    irmaa_t1: float | None  # None if no living person is age >= 63 in `year`
    aca_cliff: float | None  # None if no living ACA-enrolled person is < 65 in `year`
    niit: float


def living_flags(hh: Household, yr: YearResult) -> tuple[bool, bool]:
    """``(you_alive, spouse_alive)`` in ``yr``.

    yr.your_age / yr.spouse_age are never zeroed in survivor years (the engine
    zeroes the deceased's slot locally), so who is alive comes from hh.survivor
    plus the per-year filing status. A Single household has only "you".
    """
    if hh.filing_status == "Single":
        return True, False
    surv = hh.survivor
    if surv is not None and yr.filing_status == "Single":
        return (False, True) if surv.who_dies == "you" else (True, False)
    return True, True


def living_persons(hh: Household, yr: YearResult) -> tuple[tuple[int, bool], ...]:
    """``(age, aca_enrolled)`` for each person alive and filing in ``yr``."""
    you_alive, spouse_alive = living_flags(hh, yr)
    persons: list[tuple[int, bool]] = []
    if you_alive:
        persons.append((yr.your_age, hh.your_aca_enrolled))
    if spouse_alive:
        persons.append((yr.spouse_age, hh.spouse_aca_enrolled))
    return tuple(persons)


def room_lines_for_year(hh: Household, yr: YearResult) -> RoomLines:
    """Threshold of every line in ``yr`` (per-year filing status, household CPI)."""
    status = yr.filing_status or hh.filing_status
    cpi = hh.cpi_assumption
    persons = living_persons(hh, yr)

    ltcg_base = LTCG_THRESHOLDS_SINGLE if status == "Single" else LTCG_THRESHOLDS_MFJ
    ltcg_zero = float(index_tuple(ltcg_base, yr.year, cpi, round50=True)[0])

    irmaa_t1: float | None = None
    if any(age >= IRMAA_FIRST_INCOME_AGE for age, _ in persons):
        irmaa_t1 = irmaa_tier1_threshold(yr.year, status, cpi)

    aca_cliff: float | None = None
    if any(aca_applies(age, enrolled) for age, enrolled in persons):
        aca_cliff = aca_ceiling_magi(status, yr.year, cpi)

    return RoomLines(
        year=yr.year,
        filing_status=status,
        top_12=indexed_bracket_ceiling(1, year=yr.year, cpi=cpi, filing_status=status),
        top_22=indexed_bracket_ceiling(2, year=yr.year, cpi=cpi, filing_status=status),
        top_24=indexed_bracket_ceiling(3, year=yr.year, cpi=cpi, filing_status=status),
        ltcg_zero=ltcg_zero,
        irmaa_t1=irmaa_t1,
        aca_cliff=aca_cliff,
        niit=float(niit_threshold(status)),
    )


def _preferential_existing(yr: YearResult, ytd: YTDSnapshot | None) -> float:
    """Preferential income already in the year, before any harvest."""
    actual = 0.0 if ytd is None else ytd.qualified_dividends_ytd + ytd.preferential_capital_gain_ytd
    return yr.brokerage_qual_div + yr.realized_gains + actual


def _unused_deduction(yr: YearResult) -> float:
    """Deduction left over by ordinary income (nonzero only when taxable_income is 0)."""
    return max(yr.total_deductions - yr.combined_gross, 0.0)


def room_after(
    yr: YearResult, lines: RoomLines, ytd: YTDSnapshot | None = None
) -> dict[str, float | None]:
    """Room for additional income under each line: line - measure.

    Negative = crossed; ``None`` = line not applicable that year. The 0% LTCG
    measure is ordinary taxable income + preferential income - deduction left
    unused by ordinary income, with NO floor (#130). Preferential income is the
    forecast qualified dividends, this year's realized gains ``yr.realized_gains``
    (forecast turnover and the forced brokerage draw's gain) and, with ``ytd``
    (passed only for the base year with actuals), the YTD qualified dividends and
    the §1211-capped net long-term gain. The unused deduction shelters
    preferential income first (scenario._pref_stack_tax), so when it exceeds that
    income the room exceeds the threshold. Excluded: short-term gains, which are
    ordinary and already in ``taxable_income``.
    """
    ltcg_measure = yr.taxable_income + _preferential_existing(yr, ytd) - _unused_deduction(yr)
    pairs: dict[str, tuple[float | None, float]] = {
        "top_12": (lines.top_12, yr.taxable_income),
        "top_22": (lines.top_22, yr.taxable_income),
        "top_24": (lines.top_24, yr.taxable_income),
        "ltcg_zero": (lines.ltcg_zero, ltcg_measure),
        "irmaa_t1": (lines.irmaa_t1, yr.magi),
        "aca_cliff": (lines.aca_cliff, yr.aca_magi),
        "niit": (lines.niit, yr.niit_magi),
    }
    rooms: dict[str, float | None] = {}
    for key in LINE_KEYS:
        line, measure = pairs[key]
        rooms[key] = None if line is None else line - measure
    return rooms
