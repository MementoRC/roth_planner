"""Room This Year engine (redesign PR 4a): this year's recommended split, why
it was chosen, and a first-RMD-year look-ahead.

Everything here is derived from the exercise optimizer's winning plan; wording
lives in the view (PR 4b) -- the engine returns numbers and reason codes.
NO Streamlit imports (engine purity).

Bracket rooms here account for the withdrawal waterfall's forced living-expense
IRA draws (issue #1): they are measured on the scenario's TOTAL taxable income,
draws included, the same basis "Fill X%" sizes against. So FILLS_BRACKET means
total taxable income lands on the bracket top. (run_scenario's own conversion
cap stays draw-blind on purpose -- see the _base_headroom docstring in
engine/scenario.py -- so a manual plan can still carry income past the top.)
"""

from __future__ import annotations

import copy
import enum
import types
from collections.abc import Mapping
from dataclasses import dataclass, field

from engine.aca import aca_applies
from engine.exercise_optimizer import OptimizedPlan
from engine.headroom import HeadroomResult, compute_headroom
from engine.irmaa import irmaa_payment_year, irmaa_tier, irmaa_tier1_threshold
from engine.niit import niit_threshold
from engine.scenario import run_no_conversion, run_scenario
from engine.scenario_autofill import bracket_fill_conversions
from engine.scenario_types import ConversionPlan, ScenarioResult, YearResult
from engine.tax import indexed_bracket_ceiling
from models.exercise_schedule import ExerciseSchedule, rebalance_schedule
from models.grants import StockGrant, aggregate_by_key
from models.household import Household
from models.ytd_income import YTDSnapshot

# A split "fills" its bracket when the room left after it is within this many
# dollars of zero. Option shares are whole and the optimizer rounds them UP
# (math.ceil), so a fill can overshoot or stop short by under one share's
# spread; conversions themselves land on the bracket top exactly.
FILL_TOLERANCE = 1_000.0


class ReasonCode(enum.Enum):
    GRANT_EXPIRES = "grant_expires"  # a grant exercised this year expires this year or next
    FILLS_BRACKET = "fills_bracket"  # the split lands on its bracket top (± FILL_TOLERANCE)
    EXCEEDS_CEILING = "exceeds_ceiling"  # the split itself carries income past the bracket top
    IRMAA_LOOKBACK = "irmaa_lookback"  # this year's MAGI sets premiums in year + 2
    CEILING_ALREADY_PASSED = "ceiling_already_passed"  # realized income is past the ceiling
    NO_ACTION = "no_action"  # nothing left to do this year


@dataclass(frozen=True)
class Reason:
    code: ReasonCode
    data: Mapping[str, float | int | str | bool] = field(
        default_factory=lambda: types.MappingProxyType({})
    )

    def __post_init__(self) -> None:
        # Frozen all the way down: the payload is a read-only view.
        object.__setattr__(self, "data", types.MappingProxyType(dict(self.data)))


@dataclass(frozen=True)
class GrantSplit:
    """One grant's base-year cell of the split.

    ``year_total_shares`` is an income equivalent: the optimizer back-solves
    shares at the PROJECTED base-year price, so it is not the count of shares
    actually exercised so far (YTD exercises are known only in dollars).
    """

    grant_key: str
    year_total_shares: int
    year_total_income: float
    realized_income: float | None  # None: no per-grant data this session
    remaining_income: float | None


@dataclass(frozen=True)
class PersonConversion:
    person: str  # "you" | "spouse"
    year_total: float  # done + what the scenario converts for the rest of the year
    done: float
    remaining: float  # >= 0


@dataclass(frozen=True)
class IncomeStack:
    """The bar, on the same scenario basis as RoomAfter's bracket rooms and the
    bracket reason codes: TAXABLE income (after deductions) in the base year,
    including the withdrawal waterfall's forced IRA draws.

    roth / options: year totals (done + still to come). locked: everything
    else in that taxable income (wages, RMDs, forced IRA draws, dividends, ...
    minus deductions); it can be negative when deductions exceed that other income
    (part of the roth/options amount is then sheltered). top_of_12 / top_of_22:
    the indexed bracket tops for the scenario's filing status. So
    locked + roth + options + room_after == top whenever the room is positive.
    """

    locked: float
    roth: float
    options: float
    top_of_12: float
    top_of_22: float


@dataclass(frozen=True)
class RoomAfter:
    """Room left once the split is carried out, never negative.

    to_12pct / to_22pct: the scenario's base year with the forced IRA draws
    counted (_bracket_room_after), clamped at 0 -- the same basis as the
    bracket reason codes, so tiles and reasons agree, and ~0 once "Fill X%"
    is carried out. to_irmaa_t1 / to_niit: the scenario's base-year MAGI /
    NIIT MAGI against the tier-1 / NIIT threshold for the scenario's filing
    status (_magi_rooms_after); forced IRA draws are IN here too, as IRMAA and
    NIIT are cliffs on real MAGI.
    """

    to_12pct: float
    to_22pct: float
    to_irmaa_t1: float
    to_niit: float


@dataclass(frozen=True)
class ThisYearSplit:
    year: int
    ceiling_label: str
    exercises: tuple[GrantSplit, ...]
    options_total: float  # max(scheduled, realized)
    options_remaining: float
    # Realized option income the plan's schedule does not cover (e.g. the
    # in-the-money grants could not supply the realized floor). Counted in
    # options_total; surfaced so the view can say so instead of hiding it.
    option_floor_shortfall: float
    conversions: tuple[PersonConversion, ...]
    income_stack: IncomeStack
    room_after: RoomAfter
    reasons: tuple[Reason, ...]


def _realized_by_grant(ytd: YTDSnapshot | None) -> dict[str, float] | None:
    """Per-grant realized spread from the transient attribute set by
    engine/portfolio_sync/exercises.py (keys are grant_id == grant.key())."""
    raw = getattr(ytd, "_option_exercises_by_grant", None) if ytd is not None else None
    if not raw:
        return None
    return {str(k): float(v) for k, v in raw.items()}


def _person(person: str, still_to_convert: float, done: float) -> PersonConversion:
    # ``still_to_convert`` is the scenario's base-year conversion, already
    # zeroed at RMD start age, netted against ``done`` and capped (IRA
    # balance, bracket cap) by run_scenario -- never the raw plan amount.
    return PersonConversion(
        person=person,
        year_total=done + still_to_convert,
        done=done,
        remaining=still_to_convert,
    )


_BRACKET_LEVEL = {"top-of-12": 1, "top-of-22": 2, "top-of-24": 3}


def _bracket_top(hh: Household, base: YearResult, level: int) -> float:
    """Bracket ``level``'s indexed top for the scenario's own filing status."""
    return indexed_bracket_ceiling(
        level, year=hh.base_year, cpi=hh.cpi_assumption, filing_status=base.filing_status
    )


def _bracket_room_after(hh: Household, base: YearResult, level: int) -> float:
    """Room to bracket ``level``'s top after the split, UNCLAMPED (negative =
    past it), read from the scenario's base-year result (which already carries
    the split).

    compute_headroom's rooms are clamped at 0, so they cannot show an
    overshoot; this can. The waterfall's forced IRA draws (funding living
    expenses and tax) are IN ``combined_gross``, so this is the room under
    total taxable ordinary income -- the quantity "Fill X%" sizes against
    (issue #1) -- with the draw-induced taxable SS and the post-draw
    senior-bonus phase-out already priced by the scenario. It is the
    scenario's own cap (_base_headroom) that stays draw-blind, not this room.
    The brackets follow the scenario's own filing status for the year
    (survivor handling included), not the caller's.
    """
    return _bracket_top(hh, base, level) - (base.combined_gross - base.total_deductions)


def draw_aware_bracket_rooms(
    hh: Household,
    ytd: YTDSnapshot | None,
    headroom: HeadroomResult,
    net_inv_income: float = 0.0,
) -> tuple[float, float]:
    """(room to the 12% top, room to the 22% top) before any split, as the
    conversion "Fill X%" lands on for the base year (issue #1).

    ``headroom``'s rooms are draw-blind. In a base year where the waterfall
    forces an IRA draw at zero conversion they overstate the Fill by about the
    draw, so those years read the auto-fill (bracket_fill_conversions) instead;
    every other year keeps headroom's own rooms unchanged.
    """
    year = hh.base_year
    base = run_no_conversion(
        copy.deepcopy(hh), end_age=hh.your_age, ytd=ytd, net_inv_income=net_inv_income
    ).years[0]
    if base.forced_your_ira_draw + base.forced_spouse_ira_draw <= 0.0:
        return headroom.room_to_12pct, headroom.room_to_22pct
    return bracket_fill_conversions(hh, ytd, net_inv_income).get(year, (0.0, 0.0))


def _magi_rooms_after(hh: Household, base: YearResult) -> tuple[float, float]:
    """(IRMAA tier-1 room, NIIT room) after the split, UNCLAMPED, from the
    scenario's base-year MAGI / NIIT MAGI. Forced IRA draws are in, like the
    bracket rooms: these are cliffs on real MAGI."""
    niit = niit_threshold(base.filing_status)
    irmaa = irmaa_tier1_threshold(hh.base_year, base.filing_status, hh.cpi_assumption)
    return irmaa - base.magi, niit - base.niit_magi


def _aca_ceiling_passed(headroom: HeadroomResult, hh: Household) -> bool:
    """aca-safe: realized income already at or past the ACA cliff.

    Fires only when headroom's own ``aca_applies`` gate is true, i.e. someone
    is ENROLLED and pre-Medicare -- unlike the scenario's aca_safe strategy
    gate, which looks at age only. room_to_aca_cliff is only computed (and
    meaningful) then; otherwise it stays at its 0.0 default.
    """
    on_aca = aca_applies(hh.your_age, hh.your_aca_enrolled) or aca_applies(
        hh.spouse_age, hh.spouse_aca_enrolled
    )
    return on_aca and headroom.room_to_aca_cliff <= 0.0


def _exercise_rows(
    hh: Household,
    plan: OptimizedPlan,
    grants: dict[str, StockGrant],
    realized_by_grant: dict[str, float] | None,
) -> list[GrantSplit]:
    year = hh.base_year
    rows: list[GrantSplit] = []
    for key, grant in grants.items():
        shares = plan.schedule.shares(key, year)
        if shares <= 0:
            continue
        cell = ExerciseSchedule(
            shares_by_grant_year={key: {year: shares}},
            price_by_year=dict(plan.schedule.price_by_year),
        )
        total = cell.income_for(year, [grant], hh.projected_stock_price)
        realized = None if realized_by_grant is None else realized_by_grant.get(key, 0.0)
        rows.append(
            GrantSplit(
                grant_key=key,
                year_total_shares=shares,
                year_total_income=total,
                realized_income=realized,
                remaining_income=None if realized is None else max(total - realized, 0.0),
            )
        )
    return rows


def _income_stack(
    hh: Household,
    base: YearResult,
    bracket_room: dict[int, float],
    roth: float,
    options_total: float,
) -> IncomeStack:
    top_of_22 = _bracket_top(hh, base, 2)
    taxable_after = top_of_22 - bracket_room[2]  # total taxable income, draws included
    return IncomeStack(
        locked=taxable_after - roth - options_total,
        roth=roth,
        options=options_total,
        top_of_12=_bracket_top(hh, base, 1),
        top_of_22=top_of_22,
    )


def _reasons(
    *,
    plan: OptimizedPlan,
    year: int,
    exercises: list[GrantSplit],
    grants: dict[str, StockGrant],
    bracket_room: dict[int, float],
    irmaa_room: float,
    still_to_come: float,
    headroom: HeadroomResult,
    hh: Household,
) -> list[Reason]:
    label = plan.ceiling_label
    reasons: list[Reason] = []
    for g in exercises:
        expiry = grants[g.grant_key].expiry_year
        # Unknown per-grant realization: the year total is still to come.
        to_come = g.remaining_income if g.remaining_income is not None else g.year_total_income
        if expiry <= year + 1 and to_come > 0.0:
            reasons.append(
                Reason(ReasonCode.GRANT_EXPIRES, {"grant_key": g.grant_key, "expiry_year": expiry})
            )
    level = _BRACKET_LEVEL.get(label)
    fill_room = None if level is None else bracket_room[level]
    # Before the split: add back what is still to come this year, so income
    # the split did not add (RMDs, inherited distributions, ...) counts as
    # already passed, never as the split's overshoot. Deductions / taxable SS
    # are held at the post-split value (second-order next to a whole-ceiling
    # miss).
    if fill_room is not None:
        passed = fill_room + still_to_come <= 0.0
    elif label == "irmaa-safe":
        passed = irmaa_room + still_to_come <= 0.0
    elif label == "aca-safe":
        passed = _aca_ceiling_passed(headroom, hh)
    else:
        passed = False
    if passed:
        reasons.append(Reason(ReasonCode.CEILING_ALREADY_PASSED, {"ceiling_label": label}))
    if still_to_come <= 0.0:
        reasons.append(Reason(ReasonCode.NO_ACTION))
    elif fill_room is not None and not passed:
        if abs(fill_room) <= FILL_TOLERANCE:
            reasons.append(
                Reason(ReasonCode.FILLS_BRACKET, {"ceiling_label": label, "room_after": fill_room})
            )
        elif fill_room < -FILL_TOLERANCE:
            reasons.append(
                Reason(
                    ReasonCode.EXCEEDS_CEILING, {"ceiling_label": label, "overshoot": -fill_room}
                )
            )
    reasons.append(
        Reason(
            ReasonCode.IRMAA_LOOKBACK,
            {
                "payment_year": irmaa_payment_year(year),
                "counts": year >= headroom.irmaa_first_relevant_year,
                "first_relevant_year": headroom.irmaa_first_relevant_year,
            },
        )
    )
    return reasons


def summarize_this_year(
    hh: Household,
    ytd: YTDSnapshot | None,
    plan: OptimizedPlan,
    filing_status: str | None = None,
    *,
    current_plan: ConversionPlan,
    net_inv_income: float = 0.0,
) -> ThisYearSplit:
    """The base-year slice of ``plan`` (normally ``optimize_exercises(...).best``,
    run with the same ``ytd``), with what is already done netted out, the room
    left once the split is carried out, and why it was chosen.

    The base year is projected as the scenario will convert it: the winner's
    base-year conversion AMOUNTS placed into ``current_plan`` (required: pass
    the user's plan, or an empty ``ConversionPlan()`` when there is none),
    keeping the current plan's ``bracket_target`` / ``magi_strategy``. So
    summary == rmd_lookahead's SPLIT case == what Apply produces. Until 4b's
    Apply also carries the winner's strategy, a top-of-24 or MAGI-strategy
    winner's base-year conversion is capped by the CURRENT plan's settings
    (e.g. the scenario's 22% cap in a waterfall year), not the winner's own.

    Every room and bracket reason is on the scenario's base-year basis, except
    two that still come from ``compute_headroom`` with ``filing_status``
    (None means ``hh.filing_status``) on the YTD-only basis: the aca-safe "already passed"
    check (gated on ACA enrollment) and the IRMAA "counts from" year
    (``irmaa_first_relevant_year``).

    ``net_inv_income`` is the manual NII estimate the optimizer ran with (#591);
    the base-year run prices NIIT on it so the MAGI rooms match.
    """
    year = hh.base_year
    snap = ytd if ytd is not None else YTDSnapshot(tax_year=year)
    # Still needed for irmaa_first_relevant_year and the ACA cliff only.
    headroom = compute_headroom(hh, snap, filing_status or hh.filing_status)
    grants: dict[str, StockGrant] = {g.key(): g for g in aggregate_by_key(hh.grants)}
    exercises = _exercise_rows(hh, plan, grants, _realized_by_grant(ytd))

    # Same aggregated grant list as the per-grant rows, so they sum to this.
    scheduled = plan.schedule.income_for(year, list(grants.values()), hh.projected_stock_price)
    options_total = max(scheduled, snap.nqo_exercise_ytd)
    options_remaining = max(scheduled - snap.nqo_exercise_ytd, 0.0)
    option_floor_shortfall = max(snap.nqo_exercise_ytd - scheduled, 0.0)

    # What the scenario actually converts this year: the winner's raw ask can
    # exceed the IRA balance or the cap the current plan's settings impose.
    as_applied = copy.deepcopy(current_plan)
    as_applied.your_conversions[year] = plan.conversions.your_conversions.get(year, 0.0)
    as_applied.spouse_conversions[year] = plan.conversions.spouse_conversions.get(year, 0.0)
    hh_split = copy.deepcopy(hh)
    hh_split.exercise_schedule = plan.schedule
    base = run_scenario(hh_split, as_applied, ytd=ytd, net_inv_income=net_inv_income).years[0]
    conversions = [_person("you", base.your_conversion, snap.ira_conversions_ytd)]
    if base.filing_status == "MFJ":  # the scenario's status for the year
        conversions.append(
            _person("spouse", base.spouse_conversion, snap.spouse_ira_conversions_ytd)
        )
    conversions_remaining = sum(c.remaining for c in conversions)
    still_to_come = conversions_remaining + options_remaining

    # Bracket rooms after the split, unclamped, on the scenario basis; the
    # reasons and the 12%/22% tiles both read these.
    bracket_room = {lvl: _bracket_room_after(hh, base, lvl) for lvl in (1, 2, 3)}
    irmaa_room, niit_room = _magi_rooms_after(hh, base)
    room_after = RoomAfter(
        to_12pct=max(bracket_room[1], 0.0),
        to_22pct=max(bracket_room[2], 0.0),
        to_irmaa_t1=max(irmaa_room, 0.0),
        to_niit=max(niit_room, 0.0),
    )
    roth = sum(c.year_total for c in conversions)

    return ThisYearSplit(
        year=year,
        ceiling_label=plan.ceiling_label,
        exercises=tuple(exercises),
        options_total=options_total,
        options_remaining=options_remaining,
        option_floor_shortfall=option_floor_shortfall,
        conversions=tuple(conversions),
        income_stack=_income_stack(hh, base, bracket_room, roth, options_total),
        room_after=room_after,
        reasons=tuple(
            _reasons(
                plan=plan,
                year=year,
                exercises=exercises,
                grants=grants,
                bracket_room=bracket_room,
                irmaa_room=irmaa_room,
                still_to_come=still_to_come,
                headroom=headroom,
                hh=hh,
            )
        ),
    )


class LookaheadCase(enum.Enum):
    NONE = "none"  # no conversions, current exercise schedule
    CURRENT = "current"  # the current conversion plan and schedule
    SPLIT = "split"  # current plan with only this year replaced by the split


@dataclass(frozen=True)
class RmdCase:
    case: LookaheadCase
    year: int
    your_rmd: float
    spouse_rmd: float
    total_rmd: float
    taxable_income: float
    marginal_bracket: float
    irmaa_tier: int  # tier this year's MAGI sets for premiums in year + 2


@dataclass(frozen=True)
class RmdLookahead:
    year: int
    cases: tuple[RmdCase, ...]

    def case(self, which: LookaheadCase) -> RmdCase:
        return next(c for c in self.cases if c.case is which)


def first_rmd_year(hh: Household) -> int:
    """Earliest year the scenario books an RMD for either person: the year they
    reach their RMD start age, +1 when that person defers the first RMD to
    April 1 of the next year; the base year when already past it. The spouse
    counts only for an MFJ household."""

    def _first(age: int, start_age: int, defer: bool) -> int:
        first = hh.base_year + (start_age - age) + (1 if defer else 0)
        return max(first, hh.base_year)

    years = [_first(hh.your_age, hh.your_rmd_start_age, hh.your_defer_first_rmd)]
    if hh.filing_status == "MFJ":
        years.append(_first(hh.spouse_age, hh.spouse_rmd_start_age, hh.spouse_defer_first_rmd))
    return min(years)


def apply_split_conversions(current: ConversionPlan, split: ThisYearSplit) -> ConversionPlan:
    """``current`` with only the split's year replaced by its per-person year
    totals -- what the scenario actually converts (IRA- and bracket-capped),
    never the optimizer's raw ask. Later years are left as they are. A person
    with no row in the split (Single household) gets 0 that year.

    This is the base-year total the scenario will convert, not the stored form:
    a UI writer must pass it through ``views.planner.grid_normalized_conversions``
    before storing ``conv_plan_*`` (conversions already done can push
    done + applied above the Jan-1 IRA balance the grid clamps against)."""
    totals = {c.person: c.year_total for c in split.conversions}
    plan = copy.deepcopy(current)
    plan.your_conversions[split.year] = totals.get("you", 0.0)
    plan.spouse_conversions[split.year] = totals.get("spouse", 0.0)
    return plan


def apply_split_schedule(hh: Household, split_plan: OptimizedPlan) -> ExerciseSchedule:
    """The household's current schedule with only the base year's shares
    replaced by the split's, rebalanced per grant (nearest later years first).
    Grants already expired before the base year are left untouched."""
    year = hh.base_year
    # Work on a copy of hh: effective_schedule() can migrate keys on the
    # stored schedule in place.
    schedule = copy.deepcopy(hh).effective_schedule()
    for grant in aggregate_by_key(hh.grants):
        if grant.expiry_year < year:
            continue
        schedule = rebalance_schedule(
            schedule, grant, year, split_plan.schedule.shares(grant.key(), year)
        )
    price = split_plan.schedule.price(year)
    if price is not None:
        schedule.set_price(year, price)
    return schedule


def _rmd_case(which: LookaheadCase, result: ScenarioResult, year: int, cpi: float) -> RmdCase:
    yr = next((y for y in result.years if y.year == year), None)
    if yr is None:
        raise ValueError(f"first RMD year {year} is beyond the projection's end")
    return RmdCase(
        case=which,
        year=year,
        your_rmd=yr.your_rmd,
        spouse_rmd=yr.spouse_rmd,
        total_rmd=yr.your_rmd + yr.spouse_rmd,
        taxable_income=yr.taxable_income,
        marginal_bracket=yr.marginal_bracket,
        irmaa_tier=irmaa_tier(yr.magi, yr.filing_status, year=irmaa_payment_year(year), cpi=cpi),
    )


def rmd_lookahead(
    hh: Household,
    ytd: YTDSnapshot | None,
    current_plan: ConversionPlan,
    split: ThisYearSplit,
    split_plan: OptimizedPlan,
    end_age: int = 95,
    *,
    net_inv_income: float = 0.0,
) -> RmdLookahead:
    """The first RMD year under no conversions, the current plan, and the
    current plan with only this year replaced by the split (the base-year
    totals the scenario will convert: ``split``'s conversion year totals,
    ``split_plan``'s base-year shares; a UI writer still normalizes them via
    ``views.planner.grid_normalized_conversions`` before storing). Every run gets ``ytd`` (the engine applies it to the base year
    only) and a deep copy of ``hh``; nothing passed in is mutated.

    ``split`` must come from ``summarize_this_year(hh, ytd, split_plan,
    current_plan=<this same current_plan>)``; with a different plan the
    summary and the SPLIT case can disagree on this year's conversion cap."""
    year = first_rmd_year(hh)
    cpi = hh.cpi_assumption

    none_result = run_no_conversion(
        copy.deepcopy(hh), end_age=end_age, ytd=ytd, net_inv_income=net_inv_income
    )
    current_result = run_scenario(
        copy.deepcopy(hh),
        copy.deepcopy(current_plan),
        end_age=end_age,
        ytd=ytd,
        net_inv_income=net_inv_income,
    )
    split_hh = copy.deepcopy(hh)
    split_hh.exercise_schedule = apply_split_schedule(hh, split_plan)
    split_result = run_scenario(
        split_hh,
        apply_split_conversions(current_plan, split),
        end_age=end_age,
        ytd=ytd,
        net_inv_income=net_inv_income,
    )

    return RmdLookahead(
        year=year,
        cases=(
            _rmd_case(LookaheadCase.NONE, none_result, year, cpi),
            _rmd_case(LookaheadCase.CURRENT, current_result, year, cpi),
            _rmd_case(LookaheadCase.SPLIT, split_result, year, cpi),
        ),
    )
