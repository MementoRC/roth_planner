"""Income Control room lines (issue #123): where each tax/cost line sits in a
year, how much room a year's income leaves under it (#130 measure), and the
advisory 0% LTCG harvest sized into that room (slice 3).

Thresholds come from the same helpers the scenario uses; measures come from
run_scenario's YearResult. The harvest search adds NO tax model of its own: it
reuses engine.yr_reprice.reprice, scenario._pref_stack_tax and the federal
bracket functions. NO Streamlit imports (engine purity). Must not import
engine.this_year.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from engine.aca import aca_applies, aca_ceiling_magi
from engine.irmaa import irmaa_tier1_threshold
from engine.niit import niit_threshold
from engine.scenario import _pref_stack_tax
from engine.scenario_types import YearResult
from engine.tax import (
    LTCG_RATES_MFJ,
    LTCG_RATES_SINGLE,
    LTCG_THRESHOLDS_MFJ,
    LTCG_THRESHOLDS_SINGLE,
    federal_tax,
    federal_tax_single,
    indexed_bracket_ceiling,
)
from engine.tax_indexing import index_tuple
from engine.yr_reprice import reprice
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


_BIND_LTCG_ZERO = "0% LTCG"
_BIND_SS_TAX = "Social Security tax"
HARVEST_BINDINGS: tuple[str, ...] = (
    _BIND_LTCG_ZERO,
    _BIND_SS_TAX,
    "ACA cliff",
    "IRMAA tier 1",
    "NIIT",
    "embedded gain",
)
_EMBEDDED_GAIN = "embedded gain"
# Room lines that bound a harvest 1:1, with their binding label.
_HARVEST_ROOMS: dict[str, str] = {
    "ltcg_zero": _BIND_LTCG_ZERO,
    "aca_cliff": "ACA cliff",
    "irmaa_t1": "IRMAA tier 1",
    "niit": "NIIT",
}


@dataclass(frozen=True)
class HarvestSizing:
    """Advisory 0% LTCG harvest for one year, sized alone (not additive across years)."""

    amount: float  # >= 0; largest H at $0 extra federal tax and no cliff crossed
    binding: str  # one of HARVEST_BINDINGS
    room_uncapped: float  # same search WITHOUT the embedded-gain cap (>= 0); == amount unless binding is 'embedded gain'
    basis_known: bool  # False when brokerage_start_basis is None (amount == room_uncapped)


def _upper_bound(rooms: dict[str, float | None], embedded_gain: float | None) -> tuple[float, str]:
    """1:1 upper bound on the harvest and the term that set it.

    A harvested dollar adds at least $1 to every measure (taxable SS and a
    shrinking senior bonus only add), so H never exceeds the smallest applicable
    room or the embedded gain. Ties follow HARVEST_BINDINGS; a crossed line
    (negative room) floors the bound at 0 and is named. If a line is already
    crossed, amount is 0 and binding names the most-crossed line.
    """
    terms: list[tuple[float, str]] = []
    for key, label in _HARVEST_ROOMS.items():
        room = rooms.get(key)
        if room is not None:
            terms.append((room, label))
    if embedded_gain is not None:
        terms.append((embedded_gain, _EMBEDDED_GAIN))
    if not terms:
        # ltcg_zero and niit are always set by room_lines, so this is unreachable in practice.
        raise ValueError("no harvest constraints")
    value, label = min(terms, key=lambda term: (term[0], HARVEST_BINDINGS.index(term[1])))
    return max(0.0, value), label


_TAX_TOLERANCE = 0.005  # extra federal tax at or below half a cent is $0
_HARVEST_TOLERANCE = 1.0  # stop bisecting when hi - lo <= $1
_HARVEST_MAX_ITERATIONS = 40  # closes any upper bound under ~$1T


@dataclass(frozen=True)
class _HarvestCase:
    """One year's inputs to the feasibility test, priced once."""

    yr: YearResult
    lines: RoomLines
    cpi: float
    pref_existing: float
    thresholds: tuple[float, float]
    rates: tuple[float, float, float]
    ordinary_tax_at_zero: float


def _ordinary_tax(status: str, taxable: float, year: int, cpi: float) -> float:
    """Federal ordinary tax, dispatched like scenario_compute.compute_federal_tax."""
    if status == "Single":
        return federal_tax_single(taxable, year=year, cpi=cpi)
    return federal_tax(taxable, year=year, cpi=cpi)


def _harvest_case(
    yr: YearResult, lines: RoomLines, cpi: float, ytd: YTDSnapshot | None
) -> _HarvestCase:
    """Thresholds and rates as scenario.py:1136-1141 (Single for survivor years)."""
    single = lines.filing_status == "Single"
    if yr.filing_status != lines.filing_status:
        yr = replace(yr, filing_status=lines.filing_status)  # reprice reads yr.filing_status
    base = LTCG_THRESHOLDS_SINGLE if single else LTCG_THRESHOLDS_MFJ
    thresholds = index_tuple(base, yr.year, cpi, round50=True)
    taxable_at_zero = reprice(yr, 0.0, 0.0, cpi).taxable_income
    return _HarvestCase(
        yr=yr,
        lines=lines,
        cpi=cpi,
        pref_existing=_preferential_existing(yr, ytd),
        thresholds=(float(thresholds[0]), float(thresholds[1])),
        rates=LTCG_RATES_SINGLE if single else LTCG_RATES_MFJ,
        ordinary_tax_at_zero=_ordinary_tax(lines.filing_status, taxable_at_zero, yr.year, cpi),
    )


def _tax_violation(case: _HarvestCase, h: float) -> tuple[str | None, float]:
    """(tax label or None, extra taxable SS) at harvest ``h``.

    The float is the change in taxable Social Security, reused for the MAGI checks.

    tax(h) - tax(0) = extra ordinary tax + preferential tax on h stacked above the
    existing preferential income. "0% LTCG" when the preferential stack is taxed,
    else "Social Security tax" (extra ORDINARY tax from newly taxable benefits or
    a shrunken senior bonus).
    """
    yr = case.yr
    rp = reprice(yr, 0.0, h, case.cpi)
    gross = yr.combined_gross - yr.taxable_ss_amt + rp.taxable_ss
    unused = max(rp.total_deductions - gross, 0.0)
    pref_tax = _pref_stack_tax(
        rp.taxable_income, unused, case.pref_existing, h, case.thresholds, case.rates
    )
    ordinary_extra = (
        _ordinary_tax(case.lines.filing_status, rp.taxable_income, yr.year, case.cpi)
        - case.ordinary_tax_at_zero
    )
    dtss = rp.taxable_ss - yr.taxable_ss_amt
    if pref_tax + ordinary_extra <= _TAX_TOLERANCE:
        return None, dtss
    return (_BIND_LTCG_ZERO if pref_tax > 0.0 else _BIND_SS_TAX), dtss


def _violations(case: _HarvestCase, h: float) -> list[str]:
    """Constraints violated at harvest ``h``, in HARVEST_BINDINGS order.

    ACA MAGI adds non-taxable SS back, so the taxable-SS change cancels there
    (scenario_compute.py:715); IRMAA and NIIT MAGI move by dtss + h.
    """
    tax_label, dtss = _tax_violation(case, h)
    yr, lines = case.yr, case.lines
    out = [] if tax_label is None else [tax_label]
    if lines.aca_cliff is not None and yr.aca_magi + h > lines.aca_cliff:
        out.append("ACA cliff")
    if lines.irmaa_t1 is not None and yr.magi + dtss + h > lines.irmaa_t1:
        out.append("IRMAA tier 1")
    if yr.niit_magi + dtss + h > lines.niit:
        out.append("NIIT")
    return out


def _bisect(case: _HarvestCase, upper: float) -> tuple[float, float]:
    """(last feasible H, smallest infeasible H probed); ``upper`` is infeasible.

    Every constraint is monotone in H (taxable SS non-decreasing, senior bonus
    non-increasing, taxes and MAGI non-decreasing), so the feasible set is a
    prefix of [0, upper] -- the same argument as scenario_autofill's bracket fill.
    """
    lo, hi = 0.0, upper
    for _ in range(_HARVEST_MAX_ITERATIONS):
        if hi - lo <= _HARVEST_TOLERANCE:
            break
        mid = (lo + hi) / 2
        if _violations(case, mid):
            hi = mid
        else:
            lo = mid
    return lo, hi


def _search(
    case: _HarvestCase, rooms: dict[str, float | None], embedded_gain: float | None
) -> tuple[float, str]:
    """(amount, binding): the upper bound if feasible, else the bisection result
    labelled by the first constraint violated at the smallest infeasible probe."""
    upper, label = _upper_bound(rooms, embedded_gain)
    if upper <= 0.0 or not _violations(case, upper):
        return upper, label
    lo, hi = _bisect(case, upper)
    return lo, _violations(case, hi)[0]


def harvest_sizing(
    yr: YearResult,
    lines: RoomLines,
    rooms: dict[str, float | None],
    embedded_gain: float | None,
    cpi: float,
    ytd: YTDSnapshot | None = None,
) -> HarvestSizing:
    """Largest harvest H of unrealized long-term gain that costs $0 extra federal
    tax and crosses no applicable cliff, measured after the year's planned
    conversion and exercises. ADVISORY: nothing here feeds the projection.

    Starts from the 1:1 upper bound (``_upper_bound``). If that is feasible it is
    the answer; otherwise bisects [0, upper] re-pricing taxable SS and the OBBBA
    senior bonus (``reprice``), ordinary and preferential tax, and the IRMAA/NIIT
    MAGI at each candidate, and returns the last feasible H, so it never
    over-states. ``rooms`` is ``room_after(yr, lines, ytd)``. ``embedded_gain``
    None = basis unknown: no cap, ``basis_known`` False.
    """
    case = _harvest_case(yr, lines, cpi, ytd)
    amount, binding = _search(case, rooms, embedded_gain)
    if embedded_gain is None:
        return HarvestSizing(amount, binding, amount, basis_known=False)
    uncapped = _search(case, rooms, None)[0] if binding == _EMBEDDED_GAIN else amount
    return HarvestSizing(amount, binding, uncapped, basis_known=True)
