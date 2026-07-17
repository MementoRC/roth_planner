"""Pure-Python exercise auto-optimizer: solve for the ExerciseSchedule that
minimizes modeled lifetime all-in cost. NO Streamlit imports (engine purity)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

from engine.aca import aca_ceiling_magi
from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE
from engine.scenario import run_no_conversion, run_scenario
from engine.scenario_autofill import (
    auto_fill_12,
    auto_fill_22,
    auto_fill_24,
    auto_fill_aca,
    auto_fill_irmaa_safe,
)
from engine.scenario_types import ConversionPlan
from engine.tax import room_to_12, room_to_22, room_to_24
from engine.tax_indexing import index_value
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household
from models.ytd_income import YTDSnapshot

_BRACKET_ROOM_FNS = {
    "top-of-12": room_to_12,
    "top-of-22": room_to_22,
    "top-of-24": room_to_24,
}
_MAGI_STRATEGIES = ("irmaa-safe", "aca-safe")


@dataclass
class OptimizedPlan:
    ceiling_label: str
    schedule: ExerciseSchedule
    conversions: ConversionPlan
    lifetime_all_in: float
    over_ceiling_years: list[int] = field(default_factory=list)


@dataclass
class OptimizerResult:
    best: OptimizedPlan
    candidates: list[OptimizedPlan]
    baseline_cost: float


def _base_projection(
    hh: Household, end_age: int = 95
) -> tuple[dict[int, float], dict[int, float], dict[int, float], dict[int, str]]:
    """Conversion-free base projection. Returns per-year dicts:
    (base_ordinary, magi_wedge, total_deductions, filing_status).
    base_ordinary nets option income out of ordinary gross (schedule-independent
    baseline); magi_wedge = magi - combined_gross (LTCG/dividends/muni/taxable-SS
    delta above ordinary gross), used to put MAGI ceilings on the ordinary basis.
    """
    result = run_no_conversion(copy.deepcopy(hh), end_age=end_age)
    base_ordinary: dict[int, float] = {}
    magi_wedge: dict[int, float] = {}
    total_deductions: dict[int, float] = {}
    filing_status: dict[int, str] = {}
    for yr in result.years:
        base_ordinary[yr.year] = yr.combined_gross - yr.option_income
        magi_wedge[yr.year] = yr.magi - yr.combined_gross
        total_deductions[yr.year] = yr.total_deductions
        filing_status[yr.year] = yr.filing_status or hh.filing_status
    return base_ordinary, magi_wedge, total_deductions, filing_status


def _ceiling_income_by_year(
    hh: Household,
    strategy: str,
    total_deductions: dict[int, float],
    magi_wedge: dict[int, float],
    filing_status: dict[int, str],
) -> dict[int, float]:
    """Per-year ORDINARY-income ceiling for a strategy. Bracket strategies:
    deductions + indexed bracket top (== room_to_X at gross 0). MAGI strategies:
    the MAGI ceiling converted to the ordinary basis by subtracting magi_wedge."""
    cpi = hh.cpi_assumption
    ceilings: dict[int, float] = {}
    if strategy in _BRACKET_ROOM_FNS:
        room_fn = _BRACKET_ROOM_FNS[strategy]
        for year, ded in total_deductions.items():
            ceilings[year] = room_fn(
                0.0, ded, year=year, cpi=cpi, filing_status=filing_status[year]
            )
        return ceilings
    if strategy not in _MAGI_STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")
    for year, wedge in magi_wedge.items():
        fs = filing_status[year]
        if strategy == "irmaa-safe":
            tiers = IRMAA_TIERS_SINGLE if fs == "Single" else IRMAA_TIERS_MFJ
            magi_ceiling = index_value(tiers[0][0], year + 2, cpi)  # +2yr IRMAA lookback
            ordinary_ceiling = magi_ceiling - wedge
            # Mirror auto_fill_irmaa_safe: also cap at the 22% bracket ordinary ceiling.
            bracket_cap = room_to_22(
                0.0, total_deductions[year], year=year, cpi=cpi, filing_status=fs
            )
            ceilings[year] = min(ordinary_ceiling, bracket_cap)
        else:  # aca-safe
            ceilings[year] = aca_ceiling_magi(fs, year, cpi) - wedge
    return ceilings


def _build_candidate_schedule(
    grants: list[StockGrant],
    base_year: int,
    ceiling_income_by_year: dict[int, float],
    base_ex_option_by_year: dict[int, float],
    price_for_year: Callable[[int], float],
) -> tuple[ExerciseSchedule, list[int]]:
    """Spread each grant's exercises across [base_year, expiry] filling to each
    year's ordinary-income room, then FORCE all remaining shares into the grant's
    expiry year (hold-to-expiration deadline). Returns the schedule and the list
    of years whose committed option income was pushed past the ceiling (only the
    forced expiry lumps can do this). ``committed`` is per-year; grants compete
    soonest-expiry-first for each year's room. ``price_for_year(year)`` supplies
    that year's TXN price (e.g. ``hh.projected_txn_price``), so later exercise
    years are valued at their own grown price rather than a single flat price.
    """
    schedule = ExerciseSchedule()
    over_ceiling_years: list[int] = []
    if not grants:
        return schedule, over_ceiling_years

    remaining = {g.key(): g.shares for g in grants}
    committed: dict[int, float] = {}
    last_year = max(g.expiry_year for g in grants)

    for year in range(base_year, last_year + 1):
        committed.setdefault(year, 0.0)
        for grant in sorted(grants, key=lambda g: g.expiry_year):
            rem = remaining[grant.key()]
            if rem <= 0 or year < base_year or year > grant.expiry_year:
                continue
            price = price_for_year(year)
            per_share = grant.per_share_spread(price)

            if year == grant.expiry_year:
                # Hold-to-expiration deadline: force ALL remaining shares now.
                schedule.set_shares(grant.key(), year, rem)
                schedule.set_price(year, price)
                committed[year] += per_share * rem
                remaining[grant.key()] = 0
                ceiling_room = max(
                    ceiling_income_by_year.get(year, 0.0)
                    - base_ex_option_by_year.get(year, 0.0),
                    0.0,
                )
                if per_share > 0 and committed[year] > ceiling_room and year not in over_ceiling_years:
                    over_ceiling_years.append(year)
                continue

            if per_share <= 0:
                # Underwater / at-the-money: no bracket benefit to early exercise;
                # defer to the forced expiry lump (0 income).
                continue

            room_dollars = max(
                ceiling_income_by_year.get(year, 0.0)
                - base_ex_option_by_year.get(year, 0.0)
                - committed[year],
                0.0,
            )
            n = min(rem, int(room_dollars // per_share))
            if n > 0:
                schedule.set_shares(grant.key(), year, n)
                schedule.set_price(year, price)
                committed[year] += per_share * n
                remaining[grant.key()] -= n

    return schedule, over_ceiling_years


def _score_candidate(
    hh: Household,
    schedule: ExerciseSchedule,
    ceiling_label: str,
    autofill_fn: Callable[[Household, YTDSnapshot | None], ConversionPlan],
    over_ceiling_years: list[int],
    ytd: YTDSnapshot | None,
    end_age: int,
    *,
    magi_ceiling_fn: Callable[[int, str], float] | None = None,
) -> OptimizedPlan:
    """Score a candidate schedule on a DEEPCOPY of hh (caller's hh untouched):
    set the schedule, auto-fill conversions around it, run the scenario, and
    sum lifetime all-in cost.

    When ``magi_ceiling_fn`` is provided, ``over_ceiling_years`` is recomputed
    from the SCORED result's true per-year MAGI (rather than trusting the
    caller-supplied list), so MAGI-strategy candidates report over-ceiling
    years that reflect the actual auto-filled/scenario-run MAGI.
    """
    hh_copy = copy.deepcopy(hh)
    hh_copy.exercise_schedule = schedule
    plan = autofill_fn(hh_copy, ytd)
    result = run_scenario(hh_copy, plan, end_age=end_age, ytd=ytd)
    lifetime = sum(yr.all_in_cost for yr in result.years)
    if magi_ceiling_fn is not None:
        over_ceiling_years = [
            yr.year
            for yr in result.years
            if yr.magi > magi_ceiling_fn(yr.year, yr.filing_status or hh.filing_status)
        ]
    return OptimizedPlan(ceiling_label, schedule, plan, lifetime, over_ceiling_years)


def _magi_ceiling_for(strategy: str, year: int, filing_status: str, cpi: float) -> float:
    """True-MAGI ceiling for a MAGI strategy (for over-ceiling flagging)."""
    if strategy == "irmaa-safe":
        tiers = IRMAA_TIERS_SINGLE if filing_status == "Single" else IRMAA_TIERS_MFJ
        return index_value(tiers[0][0], year + 2, cpi)  # +2yr IRMAA lookback
    return aca_ceiling_magi(filing_status, year, cpi)  # aca-safe


_AutofillFn = Callable[[Household, YTDSnapshot | None], ConversionPlan]

# (label, strategy-key, autofill_fn)
DEFAULT_CEILINGS: list[tuple[str, str, _AutofillFn]] = [
    ("top-of-12", "top-of-12", auto_fill_12),
    ("top-of-22", "top-of-22", auto_fill_22),
    ("top-of-24", "top-of-24", auto_fill_24),
    ("irmaa-safe", "irmaa-safe", auto_fill_irmaa_safe),
    ("aca-safe", "aca-safe", auto_fill_aca),
]


def optimize_exercises(
    hh: Household,
    current_plan: ConversionPlan | None = None,
    ytd: YTDSnapshot | None = None,
    end_age: int = 95,
    ceilings: list[tuple[str, str, _AutofillFn]] | None = None,
) -> OptimizerResult:
    """Solve for the exercise schedule minimizing modeled lifetime all-in cost.

    Sweeps each ceiling strategy, scores option-schedule + auto-filled
    conversions with ``run_scenario``, includes the current plan as a
    baseline candidate, and returns the argmin (never worse than status quo,
    since the baseline is itself a candidate).
    """
    if ceilings is None:
        ceilings = DEFAULT_CEILINGS
    base_ordinary, magi_wedge, total_deductions, filing_status = _base_projection(
        hh, end_age=end_age
    )
    cpi = hh.cpi_assumption

    candidates: list[OptimizedPlan] = []
    for label, strategy, autofill_fn in ceilings:
        ceiling_by_year = _ceiling_income_by_year(
            hh, strategy, total_deductions, magi_wedge, filing_status
        )
        schedule, over = _build_candidate_schedule(
            hh.grants, hh.base_year, ceiling_by_year, base_ordinary, hh.projected_txn_price
        )
        magi_fn = None
        if strategy in _MAGI_STRATEGIES:

            def magi_fn(year: int, fs: str, _s: str = strategy) -> float:
                return _magi_ceiling_for(_s, year, fs, cpi)

        plan = _score_candidate(
            hh, schedule, label, autofill_fn, over, ytd, end_age, magi_ceiling_fn=magi_fn
        )
        candidates.append(plan)

    # Baseline candidate: the household's CURRENT schedule + current conversions
    # (not auto-filled), so the winner is never worse than status quo.
    baseline_schedule = hh.effective_schedule()
    baseline_conv = current_plan if current_plan is not None else ConversionPlan()
    hh_copy = copy.deepcopy(hh)
    hh_copy.exercise_schedule = baseline_schedule
    baseline_result = run_scenario(hh_copy, baseline_conv, end_age=end_age, ytd=ytd)
    baseline_cost = sum(yr.all_in_cost for yr in baseline_result.years)
    baseline_plan = OptimizedPlan("current", baseline_schedule, baseline_conv, baseline_cost, [])
    candidates.append(baseline_plan)

    best = min(candidates, key=lambda c: c.lifetime_all_in)
    return OptimizerResult(best=best, candidates=candidates, baseline_cost=baseline_cost)
