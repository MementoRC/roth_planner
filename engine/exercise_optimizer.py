"""Pure-Python exercise auto-optimizer: solve for the ExerciseSchedule that
minimizes the ranking score -- modeled lifetime TOTAL tax+cost (see
``_lifetime_total_cost``; audit-0721 C9: NOT the conversion-marginal
``yr.all_in_cost``) plus the tax still owed on pre-tax balances left at the end
of the projection (audit-1008 F-07), optionally discounted to the base year.
NO Streamlit imports (engine purity)."""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from engine.aca import aca_ceiling_magi
from engine.irmaa import irmaa_tier1_threshold
from engine.scenario import run_no_conversion, run_scenario
from engine.scenario_autofill import (
    auto_fill_12,
    auto_fill_22,
    auto_fill_24,
    auto_fill_aca,
    auto_fill_irmaa_safe,
)
from engine.scenario_types import ConversionPlan, YearResult
from engine.tax import room_to_12, room_to_22, room_to_24
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant, aggregate_by_key
from models.household import Household
from models.ytd_income import YTDSnapshot

_BRACKET_ROOM_FNS = {
    "top-of-12": room_to_12,
    "top-of-22": room_to_22,
    "top-of-24": room_to_24,
}
_MAGI_STRATEGIES = ("irmaa-safe", "aca-safe")

# audit-1008 F-07: tax rate applied to traditional / inherited IRA money still
# unspent at the end of the projection. That money is taxed as ordinary income
# whenever it comes out -- by the survivor or by heirs (who usually must empty
# an inherited IRA within 10 years) -- so a plan that leaves a large pre-tax
# balance has deferred tax, not avoided it. 24% is a mid-bracket assumption for
# the eventual recipient; callers can pass their own rate.
DEFAULT_TERMINAL_IRA_TAX_RATE = 0.24


@dataclass
class OptimizedPlan:
    ceiling_label: str
    schedule: ExerciseSchedule
    conversions: ConversionPlan
    # audit-0721 C9: this is the LIFETIME TOTAL tax+cost (see _lifetime_total_cost),
    # NOT sum(yr.all_in_cost). all_in_cost is deliberately conversion-marginal-only
    # (excludes the base ordinary federal tax on RMDs/SS/option income), so a
    # schedule that dumps option income into a zero-conversion RMD-phase year would
    # look artificially cheap if this field only summed all_in_cost. The field name
    # is kept for API/UI stability; the VALUE now includes total federal tax.
    lifetime_all_in: float
    over_ceiling_years: list[int] = field(default_factory=list)
    # audit-1008 F-07: tax owed on pre-tax (traditional + inherited IRA)
    # balances left at the end of the projection, at the terminal rate.
    terminal_deferred_tax: float = 0.0
    # The value candidates are ranked on: each year's cost plus the terminal
    # deferred tax, discounted to the base year at the optimizer's discount
    # rate. With a 0 discount rate it equals lifetime_all_in +
    # terminal_deferred_tax.
    score: float = 0.0


@dataclass
class OptimizerResult:
    best: OptimizedPlan
    candidates: list[OptimizedPlan]
    baseline_cost: float
    # The baseline candidate's ranking score (see OptimizedPlan.score).
    baseline_score: float = 0.0


def _base_projection(
    hh: Household,
    end_age: int = 95,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> tuple[dict[int, float], dict[int, float], dict[int, float], dict[int, str]]:
    """Conversion-free base projection. Returns per-year dicts:
    (base_ordinary, magi_wedge, total_deductions, filing_status).
    base_ordinary nets option income out of ordinary gross (schedule-independent
    baseline); magi_wedge = magi - combined_gross (LTCG/dividends/muni/taxable-SS
    delta above ordinary gross), used to put MAGI ceilings on the ordinary basis.

    The projection always runs the default hold-to-expiry schedule, never the
    household's stored one (issue #546): option cash funds living expenses, so
    a stored schedule moves the waterfall's forced IRA draws -- and with them
    base_ordinary -- in every year it touches. Applying a recommended schedule
    then freed room that the next run filled with yet more options.

    ``ytd`` is threaded through to ``run_no_conversion`` so realized base-year
    YTD income (e.g. wages already earned) reduces the base-year ordinary-income
    ceiling room, matching ``_score_candidate``'s ytd-aware scoring (audit-0720 M5).
    In the base year, realized NQO income (ytd.nqo_exercise_ytd) is excluded from
    base_ordinary: it is part of the year-total option income the candidate
    schedule plans (see _build_candidate_schedule's option_income_floor).

    ``net_inv_income`` is the manual "Additional NII" estimate, passed to the
    projection exactly as the planning pages pass it (issue #591).
    """
    hh_base = copy.deepcopy(hh)
    hh_base.exercise_schedule = None  # effective_schedule() -> default_at_expiry
    result = run_no_conversion(hh_base, end_age=end_age, ytd=ytd, net_inv_income=net_inv_income)
    realized_nqo = ytd.nqo_exercise_ytd if ytd is not None else 0.0
    base_ordinary: dict[int, float] = {}
    magi_wedge: dict[int, float] = {}
    total_deductions: dict[int, float] = {}
    filing_status: dict[int, str] = {}
    for yr in result.years:
        # Redesign PR 4a: in the base year combined_gross carries
        # max(scheduled, realized) option income (scenario.py ~431). Subtract
        # that same bounded amount, or realized option income stays in
        # base_ordinary AND is counted again when the candidate is scored --
        # the candidate then stops short of its ceiling by the realized amount.
        option_counted = (
            max(yr.option_income, realized_nqo) if yr.year == hh.base_year else yr.option_income
        )
        base_ordinary[yr.year] = yr.combined_gross - option_counted
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
            magi_ceiling = irmaa_tier1_threshold(year, fs, cpi)
            ordinary_ceiling = magi_ceiling - wedge
            # Mirror auto_fill_irmaa_safe: also cap at the 22% bracket ordinary ceiling.
            bracket_cap = room_to_22(
                0.0, total_deductions[year], year=year, cpi=cpi, filing_status=fs
            )
            ceilings[year] = min(ordinary_ceiling, bracket_cap)
        else:  # aca-safe
            ceilings[year] = aca_ceiling_magi(fs, year, cpi) - wedge
    return ceilings


def _top_up_to_floor(
    grants: list[StockGrant],
    year: int,
    price: float,
    floor: float,
    floor_by_grant: Mapping[str, float],
    schedule: ExerciseSchedule,
    remaining: dict[str, int],
    committed: dict[int, float],
) -> None:
    """Raise ``year``'s scheduled option income to at least ``floor`` -- income
    already realized this year, which a year-total schedule must contain.

    Grants named in ``floor_by_grant`` (grant key -> realized spread $) are
    topped up to their own realized amount first; any shortfall left goes to
    the soonest-expiring in-the-money grants. Share counts round UP, so the
    floor is met (over by less than one share's spread).
    """

    def _add(grant: StockGrant, dollars: float) -> None:
        key = grant.key()
        per_share = grant.per_share_spread(price)
        if dollars <= 0 or per_share <= 0 or remaining[key] <= 0 or year > grant.expiry_year:
            return
        n = min(remaining[key], math.ceil(dollars / per_share))
        schedule.set_shares(key, year, schedule.shares(key, year) + n)
        schedule.set_price(year, price)
        committed[year] += per_share * n
        remaining[key] -= n

    for grant in grants:
        realized = floor_by_grant.get(grant.key(), 0.0)
        already = grant.per_share_spread(price) * schedule.shares(grant.key(), year)
        _add(grant, realized - already)
    for grant in sorted(grants, key=lambda g: g.expiry_year):
        _add(grant, floor - committed[year])


def _build_candidate_schedule(
    grants: list[StockGrant],
    base_year: int,
    ceiling_income_by_year: dict[int, float],
    base_ex_option_by_year: dict[int, float],
    price_for_year: Callable[[int], float],
    *,
    option_income_floor: float = 0.0,
    floor_by_grant: Mapping[str, float] | None = None,
) -> tuple[ExerciseSchedule, list[int]]:
    """Spread each grant's exercises across [base_year, expiry] filling to each
    year's ordinary-income room, then FORCE all remaining shares into the grant's
    expiry year (hold-to-expiration deadline). Returns the schedule and the list
    of years whose committed option income was pushed past the ceiling (only the
    forced expiry lumps can do this). ``committed`` is per-year; grants compete
    soonest-expiry-first for each year's room. ``price_for_year(year)`` supplies
    that year's stock price (e.g. ``hh.projected_stock_price``), so later exercise
    years are valued at their own grown price rather than a single flat price.

    ``option_income_floor`` / ``floor_by_grant``: option income already realized
    in base_year (ytd.nqo_exercise_ytd, and per grant key when known); the base
    year is topped up to it after the room fill (see _top_up_to_floor). Floor
    shares are not flagged over-ceiling: that income is already realized.
    """
    schedule = ExerciseSchedule()
    over_ceiling_years: list[int] = []
    if not grants:
        return schedule, over_ceiling_years

    # audit-0721 C12: aggregate FIRST so colliding grants (same key(), empty
    # grant_id) are one summed lot -- a plain dict comprehension keyed by
    # grant.key() would keep only the LAST colliding grant's shares.
    grants = aggregate_by_key(grants)
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
                    ceiling_income_by_year.get(year, 0.0) - base_ex_option_by_year.get(year, 0.0),
                    0.0,
                )
                if (
                    per_share > 0
                    and committed[year] > ceiling_room
                    and year not in over_ceiling_years
                ):
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

        if year == base_year and option_income_floor > 0.0:
            _top_up_to_floor(
                grants,
                year,
                price_for_year(year),
                option_income_floor,
                floor_by_grant or {},
                schedule,
                remaining,
                committed,
            )

    return schedule, over_ceiling_years


def _lifetime_total_cost(years: list[YearResult]) -> float:
    """Lifetime TOTAL tax+cost across years -- the objective the exercise
    optimizer ranks candidates on (audit-0721 C9).

    ``yr.all_in_cost`` (conversion_tax + irmaa_cost + aca_loss + niit_cost +
    conversion_ltcg_cost) is deliberately conversion-MARGINAL-only: it excludes
    the base ordinary federal tax on RMDs/SS/option income, by design, so that
    other consumers (views, scenario comparator) can isolate "the cost of
    converting" from the household's underlying tax bill. That's the wrong
    signal for CHOOSING AN EXERCISE SCHEDULE: exercise timing changes WHERE
    option income lands, which changes ``federal_tax_amt`` directly -- even in
    a year where the auto-filled conversion is zero (e.g. an RMD-phase year).
    Using all_in_cost alone would make a candidate that concentrates a large
    option-income spread into a high-bracket, zero-conversion RMD year look
    artificially cheap.

    ``yr.federal_tax_amt`` already contains the full ordinary tax on taxable
    income INCLUDING any conversion (conversion_tax is only the marginal delta
    of that same tax, already embedded in federal_tax_amt) -- so this is
    equivalent to ``all_in_cost + (federal_tax_amt - conversion_tax)`` without
    double-counting conversion_tax.

    audit-1008 F-07: the 10% early-withdrawal penalty the waterfall charges when
    a plan forces pre-59½ IRA/Roth draws is a real cost of that plan too.
    """
    return sum(_year_cost(yr) for yr in years)


def _year_cost(yr: YearResult) -> float:
    """One year's tax + cost (see ``_lifetime_total_cost``)."""
    return (
        yr.federal_tax_amt
        + yr.irmaa_cost
        + yr.aca_loss
        + yr.niit_cost
        + yr.conversion_ltcg_cost
        + yr.forced_early_withdrawal_penalty
    )


def _terminal_deferred_tax(years: list[YearResult], terminal_tax_rate: float) -> float:
    """Tax still owed on the pre-tax balances left at the end of the projection
    (audit-1008 F-07): traditional plus inherited IRA closing balances of the
    final year, at ``terminal_tax_rate``. Roth and brokerage balances carry no
    deferred ordinary-income tax."""
    if not years:
        return 0.0
    final = years[-1]
    pre_tax = (
        final.your_ira_end
        + final.spouse_ira_end
        + final.your_inherited_balance_end
        + final.spouse_inherited_balance_end
    )
    return max(pre_tax, 0.0) * terminal_tax_rate


def _ranking_score(
    years: list[YearResult],
    base_year: int,
    discount_rate: float,
    terminal_tax: float,
) -> float:
    """The optimizer's objective (audit-1008 F-07, user decision D2).

    Each year's cost (``_year_cost``) plus the terminal deferred tax, discounted
    to the base year: a cost in year Y is divided by (1 + r)^(Y - base_year),
    and the terminal tax -- paid after the last projected year -- by
    (1 + r)^(last_year + 1 - base_year). With r = 0 this is just
    ``_lifetime_total_cost(years) + terminal_tax``.
    """
    if not years:
        return terminal_tax
    growth = 1.0 + discount_rate
    total = sum(_year_cost(yr) / growth ** (yr.year - base_year) for yr in years)
    return total + terminal_tax / growth ** (years[-1].year + 1 - base_year)


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
    discount_rate: float = 0.0,
    terminal_tax_rate: float = DEFAULT_TERMINAL_IRA_TAX_RATE,
    qcds: Mapping[int, float] | None = None,
    spouse_qcds: Mapping[int, float] | None = None,
    net_inv_income: float = 0.0,
) -> OptimizedPlan:
    """Score a candidate schedule on a DEEPCOPY of hh (caller's hh untouched):
    set the schedule, auto-fill conversions around it, run the scenario, and
    compute its lifetime cost, terminal deferred tax and ranking score.

    When ``magi_ceiling_fn`` is provided, ``over_ceiling_years`` is recomputed
    from the SCORED result's true per-year MAGI (rather than trusting the
    caller-supplied list), so MAGI-strategy candidates report over-ceiling
    years that reflect the actual auto-filled/scenario-run MAGI.

    audit-1008 F-60: ``qcds`` / ``spouse_qcds`` are the current plan's QCDs.
    QCDs are a giving decision, not a conversion strategy, and Apply keeps the
    plan's QCDs -- so every candidate is scored with them, like the baseline.

    Issue #591: ``net_inv_income`` (the manual NII estimate) prices each
    candidate's NIIT the same way the planning pages' scenarios do.
    """
    hh_copy = copy.deepcopy(hh)
    hh_copy.exercise_schedule = schedule
    plan = autofill_fn(hh_copy, ytd)
    if qcds or spouse_qcds:
        plan = replace(plan, qcds=dict(qcds or {}), spouse_qcds=dict(spouse_qcds or {}))
    result = run_scenario(hh_copy, plan, end_age=end_age, ytd=ytd, net_inv_income=net_inv_income)
    lifetime = _lifetime_total_cost(result.years)
    if magi_ceiling_fn is not None:
        over_ceiling_years = [
            yr.year
            for yr in result.years
            if yr.magi > magi_ceiling_fn(yr.year, yr.filing_status or hh.filing_status)
        ]
    terminal = _terminal_deferred_tax(result.years, terminal_tax_rate)
    return OptimizedPlan(
        ceiling_label,
        schedule,
        plan,
        lifetime,
        over_ceiling_years,
        terminal_deferred_tax=terminal,
        score=_ranking_score(result.years, hh.base_year, discount_rate, terminal),
    )


def _magi_ceiling_for(strategy: str, year: int, filing_status: str, cpi: float) -> float:
    """True-MAGI ceiling for a MAGI strategy (for over-ceiling flagging)."""
    if strategy == "irmaa-safe":
        return irmaa_tier1_threshold(year, filing_status, cpi)
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
    *,
    discount_rate: float = 0.0,
    terminal_tax_rate: float = DEFAULT_TERMINAL_IRA_TAX_RATE,
    net_inv_income: float = 0.0,
) -> OptimizerResult:
    """Solve for the exercise schedule minimizing the ranking score.

    Sweeps each ceiling strategy, scores option-schedule + auto-filled
    conversions with ``run_scenario``, includes the current plan as a
    baseline candidate, and returns the argmin (never worse than status quo,
    since the baseline is itself a candidate). Each year's cost uses
    ``_lifetime_total_cost``'s terms (audit-0721 C9) so the total federal tax
    on option income -- which varies with exercise timing even when the
    auto-filled conversion is zero -- is part of the objective.

    audit-1008 F-07 (user decision D2): the score also counts the tax still
    owed on traditional/inherited IRA money left at the end
    (``terminal_tax_rate``), so a plan cannot look cheap just by deferring
    tax past the horizon. ``discount_rate`` (default 0) discounts each year's
    cost to the base year; at 0 the score is the plain nominal sum.

    Issue #591: ``net_inv_income`` is the manual "Additional NII" estimate
    (Setup › Assumptions). Every internal run -- base projection, candidates
    and baseline -- gets it, so NIIT is priced as on the planning pages.
    """
    if discount_rate <= -1.0:
        raise ValueError(f"discount_rate must be greater than -1, got {discount_rate}")
    if ceilings is None:
        ceilings = DEFAULT_CEILINGS
    base_ordinary, magi_wedge, total_deductions, filing_status = _base_projection(
        hh, end_age=end_age, ytd=ytd, net_inv_income=net_inv_income
    )
    cpi = hh.cpi_assumption
    # Redesign PR 4a: option income already realized this year is a floor on
    # the base year's year-total schedule. Per-grant attribution comes from
    # the transient ytd._option_exercises_by_grant (grant_id -> spread $) set by
    # engine/portfolio_sync/exercises.py; it is not a dataclass field.
    option_floor = ytd.nqo_exercise_ytd if ytd is not None else 0.0
    realized_by_id = getattr(ytd, "_option_exercises_by_grant", None) or {}
    floor_by_grant = {
        g.key(): float(realized_by_id[g.grant_id])
        for g in aggregate_by_key(hh.grants)
        if g.grant_id and g.grant_id in realized_by_id
    }

    # Issue #543: the user's saved per-year price overrides win over the
    # projection, so candidates are priced like the baseline (effective_schedule).
    overrides = dict(hh.exercise_schedule.price_by_year) if hh.exercise_schedule else {}

    def price_for_year(year: int) -> float:
        return overrides.get(year, hh.projected_stock_price(year))

    candidates: list[OptimizedPlan] = []
    for label, strategy, autofill_fn in ceilings:
        ceiling_by_year = _ceiling_income_by_year(
            hh, strategy, total_deductions, magi_wedge, filing_status
        )
        schedule, over = _build_candidate_schedule(
            hh.grants,
            hh.base_year,
            ceiling_by_year,
            base_ordinary,
            price_for_year,
            option_income_floor=option_floor,
            floor_by_grant=floor_by_grant,
        )
        magi_fn = None
        if strategy in _MAGI_STRATEGIES:

            def magi_fn(year: int, fs: str, _s: str = strategy) -> float:
                return _magi_ceiling_for(_s, year, fs, cpi)

        plan = _score_candidate(
            hh,
            schedule,
            label,
            autofill_fn,
            over,
            ytd,
            end_age,
            magi_ceiling_fn=magi_fn,
            discount_rate=discount_rate,
            terminal_tax_rate=terminal_tax_rate,
            qcds=current_plan.qcds if current_plan is not None else None,
            spouse_qcds=current_plan.spouse_qcds if current_plan is not None else None,
            net_inv_income=net_inv_income,
        )
        candidates.append(plan)

    # Baseline candidate: the household's CURRENT schedule + current conversions
    # (not auto-filled), so the winner is never worse than status quo.
    baseline_schedule = hh.effective_schedule()
    baseline_conv = current_plan if current_plan is not None else ConversionPlan()
    hh_copy = copy.deepcopy(hh)
    hh_copy.exercise_schedule = baseline_schedule
    baseline_result = run_scenario(
        hh_copy, baseline_conv, end_age=end_age, ytd=ytd, net_inv_income=net_inv_income
    )
    baseline_cost = _lifetime_total_cost(baseline_result.years)
    baseline_terminal = _terminal_deferred_tax(baseline_result.years, terminal_tax_rate)
    baseline_score = _ranking_score(
        baseline_result.years, hh.base_year, discount_rate, baseline_terminal
    )
    baseline_plan = OptimizedPlan(
        "current",
        baseline_schedule,
        baseline_conv,
        baseline_cost,
        [],
        terminal_deferred_tax=baseline_terminal,
        score=baseline_score,
    )
    candidates.append(baseline_plan)

    best = min(candidates, key=lambda c: c.score)
    return OptimizerResult(
        best=best,
        candidates=candidates,
        baseline_cost=baseline_cost,
        baseline_score=baseline_score,
    )
