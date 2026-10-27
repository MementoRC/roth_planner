"""Auto-fill helpers for the scenario engine.

Generates ConversionPlan instances that fill to a given bracket ceiling or
IRMAA threshold. Callers import directly from engine.scenario_autofill.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from engine.aca import aca_ceiling_magi, is_pre_medicare_age, present_household_ages
from engine.ira import (
    calc_rmd,
    inherited_ira_drain,
    inherited_ira_years_remaining,
)
from engine.irmaa import irmaa_tier1_threshold
from engine.scenario import ProjectionState, project_year_with_draw, waterfall_accounts
from engine.scenario_compute import compute_brokerage_dividends, social_security_benefits
from engine.scenario_types import ConversionPlan, YearResult
from engine.tax import (
    BRACKETS_MFJ,
    BRACKETS_SINGLE,
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    bisect_conversion_for_ceiling,
    deductions,
    indexed_bracket_ceiling,
    room_to_bracket,
    senior_bonus_deduction,
    taxable_ss,
)
from engine.tax_indexing import index_value as _iv
from engine.withdrawal_waterfall import WaterfallDraw, solve_waterfall
from models.household import Household
from models.ytd_income import YTDSnapshot

RoomFn = Callable[[float, float, float, float, float, int, float, str], float]
# One projection year at a given waterfall draw (None = no draw), the year's
# conversion sized for that draw: (conversion, year result, state after it).
YearProbe = Callable[[WaterfallDraw | None], tuple[float, YearResult, ProjectionState]]

# Issue #598: bounds for solving a year's waterfall need jointly with the
# conversion it funds. A dollar matches the projection's waterfall tolerance.
_NEED_SOLVE_MAX_PASSES = 12
_NEED_SOLVE_TOLERANCE = 1.0


@dataclass(frozen=True)
class _RoomInputs:
    """One year's draw-blind income and deduction inputs to a room fn.

    ``ordinary_gross`` is the SS-exclusive ordinary bracket base (option income,
    RMDs, inherited drains, forecast ordinary dividends); ``ytd_ordinary`` is the
    base-year ordinary YTD add-back (None outside the base year); ``other_fixed``
    is the SS-exclusive, conversion-exclusive MAGI base. See _room_at.
    """

    ordinary_gross: float
    ytd_ordinary: float | None
    other_fixed: float
    combined_ss: float
    ded_no_senior: float
    phaseout_muni: float
    ya_eff: int
    sa_eff: int
    year: int
    cpi: float
    filing_status: str


def _room_at(
    inp: _RoomInputs, extra_ordinary: float, room_fn: RoomFn, extra_gain: float = 0.0
) -> float:
    """Conversion room for one year, given ``extra_ordinary`` income on top of
    the fixed base.

    ``extra_ordinary`` is the waterfall's forced IRA draw (issue #598): it is
    ordinary income exactly like the conversion, so it raises the bracket base,
    MAGI and SS provisional income alike. ``extra_gain`` is the gain the
    waterfall's forced brokerage sale realizes: preferential, so it stays out
    of the ordinary bracket base, but it is MAGI and provisional income like
    the forecast realized gains already in ``other_fixed`` -- leaving it out
    let an IRMAA-/ACA-safe fill land over its MAGI ceiling by the gain. At
    0.0/0.0 this is the historical draw-blind room, to the bit.
    """
    fs = inp.filing_status
    other_fixed = inp.other_fixed + extra_ordinary + extra_gain
    # Taxable SS — computed first so base_magi uses only the includable
    # fraction (IRC §86: max 85% of SS enters AGI/MAGI, not gross SS).
    tss = taxable_ss(inp.combined_ss, other_fixed, filing_status=fs)
    # MAGI without conversion (full MAGI — includes LTCG for IRMAA).
    # Uses taxable SS (tss) not gross combined_ss per IRC §86 + §1395r(i)(4).
    base_magi = other_fixed + tss
    fixed_gross = inp.ordinary_gross + extra_ordinary + tss
    if inp.ytd_ordinary is not None:
        fixed_gross += inp.ytd_ordinary
    ded = inp.ded_no_senior + senior_bonus_deduction(
        inp.ya_eff,
        inp.sa_eff,
        base_magi - inp.phaseout_muni,
        year=inp.year,
        cpi=inp.cpi,
        filing_status=fs,
    )

    # Room — delegated to caller's room_fn (per-year filing status flips to Single
    # in survivor years so the ceiling/tier is resolved correctly each year).
    room = room_fn(fixed_gross, ded, base_magi, inp.combined_ss, other_fixed, inp.year, inp.cpi, fs)
    # C18 (audit-0805 W5): the OBBBA senior-bonus deduction phases out with
    # MAGI, and MAGI includes the very conversion being sized here -- `ded`
    # above was priced at the PRE-conversion base_magi, so it stayed fixed
    # regardless of how large the resulting room/conversion turned out to
    # be, understating the phaseout for a large conversion. run_scenario,
    # by contrast, evaluates this deduction at POST-conversion MAGI (see
    # engine/scenario.py's yr.magi, which folds in yr.your_conversion /
    # yr.spouse_conversion, feeding senior_bonus_deduction). Iterate:
    # re-price the deduction at the MAGI implied by the previous room
    # estimate and re-solve room. senior_bonus_deduction's phaseout slope
    # is bounded at 0.06 ($0.06 per $1 of MAGI), so this fixed point is a
    # contraction and converges in a couple of passes.
    # audit-0823 AF-1: `base_magi + room` was itself an under-estimate of
    # post-conversion MAGI. base_magi carries taxable SS priced at the
    # PRE-conversion base, and MAGI includes taxable SS (IRC §86 +
    # §1395r(i)(4)), so the conversion raises MAGI by the converted dollars
    # PLUS the extra benefit it drags into taxability -- the same §86
    # feedback C14 fixed for the IRMAA ceiling and
    # _bracket_room_with_ss_torpedo now fixes for the bracket ceilings.
    # Under-estimating MAGI here over-estimates the surviving senior bonus,
    # inflating `ded` and hence the room. Measured on an MFJ pair aged 70
    # with $52,080 of benefits: estimated MAGI $207,707 leaves a per-person
    # bonus of 2 x (6,000 - 57,707 x 0.06) = $5,075.16, while the true MAGI
    # of $251,975 phases BOTH out entirely (the phase-out is per-person --
    # $6,000 each, exhausted at $250,000) -- and that $5,075.16 was exactly
    # the resulting bracket overshoot.
    for _ in range(5):
        _magi_est = (
            other_fixed + room + taxable_ss(inp.combined_ss, other_fixed + room, filing_status=fs)
        )
        _senior_est = senior_bonus_deduction(
            inp.ya_eff,
            inp.sa_eff,
            _magi_est - inp.phaseout_muni,
            year=inp.year,
            cpi=inp.cpi,
            filing_status=fs,
        )
        _new_room = room_fn(
            fixed_gross,
            inp.ded_no_senior + _senior_est,
            base_magi,
            inp.combined_ss,
            other_fixed,
            inp.year,
            inp.cpi,
            fs,
        )
        if abs(_new_room - room) < 1.0:
            room = _new_room
            break
        room = _new_room
    return room


def _allocate_room(
    hh: Household, room: float, ya: int, sa: int, your_ira: float, spouse_ira: float
) -> tuple[float | None, float | None]:
    """Split ``room`` into (your, spouse) conversions; None = no plan cell.

    Symmetric allocation: older pre-RMD person first (drains the IRA closest to
    RMD). On age tie, larger IRA first. Both criteria are symmetric under
    me↔spouse swap.
    """
    left = room

    def _take(eligible: bool, balance: float) -> float | None:
        nonlocal left
        if not eligible or left <= 0:
            return None
        amount = min(left, balance)
        left -= amount
        return amount

    you_ok = ya < hh.your_rmd_start_age
    spouse_ok = sa < hh.spouse_rmd_start_age
    if (ya > sa) or (ya == sa and your_ira >= spouse_ira):
        yc = _take(you_ok, your_ira)
        return yc, _take(spouse_ok, spouse_ira)
    sc = _take(spouse_ok, spouse_ira)
    return _take(you_ok, your_ira), sc


def _write_cells(
    plan: ConversionPlan,
    year: int,
    yc: float | None,
    sc: float | None,
    ytd_year: YTDSnapshot | None,
) -> None:
    """Store this year's conversions in ``plan`` (replacing any earlier probe).

    audit-1008 F-22 (decision D5): a base-year plan cell is the YEAR TOTAL.
    The room was sized net of the YTD conversions (the income bases already
    carry them), and run_scenario (scenario_compute.compute_conversions)
    subtracts the YTD conversions from the stored figure again, so store done +
    remaining for the scenario to convert exactly the room sized here.
    """
    for amount, done, convs in (
        (yc, ytd_year.ira_conversions_ytd if ytd_year else 0.0, plan.your_conversions),
        (sc, ytd_year.spouse_ira_conversions_ytd if ytd_year else 0.0, plan.spouse_conversions),
    ):
        convs.pop(year, None)
        if amount is not None:
            convs[year] = amount
        if done > 0:
            convs[year] = convs.get(year, 0.0) + done


def _conversion_for_draw(inp: _RoomInputs, draw: WaterfallDraw | None, room_fn: RoomFn) -> float:
    """This year's total conversion when the waterfall takes ``draw`` (None =
    no draw): the room left once the draw's ordinary income (IRA legs) and
    realized gain (brokerage leg) are on top of the fixed base. Never negative
    -- a base already over the target converts nothing.

    With a forced IRA draw the conversion stops half the solve tolerance short
    of the room, the middle of the accepted window: the draw is solved to
    within that tolerance, so the year lands at or just under the target
    rather than a few cents over it.
    """
    if draw is None:
        return max(_room_at(inp, 0.0, room_fn), 0.0)
    ira_draw = draw.your_ira_draw + draw.spouse_ira_draw
    room = _room_at(inp, ira_draw, room_fn, draw.realized_gain)
    if ira_draw > 0.0:
        room -= 0.5 * _NEED_SOLVE_TOLERANCE
    return max(room, 0.0)


def _no_marginal_tax(_extra_ordinary: float) -> float:
    return 0.0


def _solve_year(
    probe: YearProbe, brokerage_basis: float
) -> tuple[float, YearResult, ProjectionState]:
    """Solve one year's conversion jointly with the waterfall draw it provokes.

    Issue #598: the projection's withdrawal waterfall forces a draw when
    nothing else funds living expenses plus the year's tax, and a taxable IRA
    draw is ordinary income. "Fill to X" means the year's TOTAL income --
    conversion + forced draw + fixed income -- lands on X, so the conversion
    is ``room(draw)`` (see _conversion_for_draw) and the draw is whatever
    funds the year's need at that conversion.

    The unknown is the year's cash need ``M`` (income_needed, net of the
    early-withdrawal penalty the waterfall grosses up for itself). For a
    candidate ``M`` the waterfall's own account order -- brokerage, then the
    IRAs split by allocate_ira_draw with penalty gross-up, then Roth -- comes
    from solve_waterfall itself (its marginal tax supplied by the projection
    instead of re-probed), and ONE projection year at that draw and its
    conversion measures the need it actually leaves. The root is the draw
    run_scenario's waterfall solves for at the same conversion, on the same
    single tax stack. Along the fill line the conversion shrinks dollar for
    dollar as the IRA draw grows, so total ordinary income -- and the tax --
    barely moves with ``M`` and a secant step lands in one or two
    evaluations. That costs ~2-3 projection years per shortfall year, against
    the ~20 a full waterfall solve per probe took (a coverage-traced auto-fill
    took seconds).

    Starts from the no-draw year at the draw-blind room, which is ALSO the
    answer when that year has no shortfall (exactly as project_year_step
    finishes such a year). Returns (conversion, year, state after the year).

    The probes do not apply run_scenario's conversion caps (_base_headroom's
    bracket room, the irmaa_safe/aca_safe MAGI shrink): the room fns size the
    conversion at or under those same ceilings with the draw (and its sale
    gain) counted, so the caps are not expected to bind on the final run.
    """
    conv, yr, state = probe(None)
    need = yr.income_needed
    if need <= 0.0:
        return conv, yr, state
    accounts = waterfall_accounts(yr, brokerage_basis)
    tried: list[tuple[float, float, YearResult, ProjectionState]] = []
    prev: tuple[float, float] | None = None
    for _ in range(_NEED_SOLVE_MAX_PASSES):
        draw = solve_waterfall(need, accounts, _no_marginal_tax, yr.your_age, yr.spouse_age)
        conv, p_yr, p_state = probe(draw)
        gap = p_yr.income_needed - draw.early_withdrawal_penalty - need
        if abs(gap) < _NEED_SOLVE_TOLERANCE:
            return conv, p_yr, p_state
        tried.append((need, conv, p_yr, p_state))
        step = need + gap
        if prev is not None and gap != prev[1]:
            step = need - gap * (need - prev[0]) / (gap - prev[1])
        prev = (need, gap)
        need = max(step, 0.0)
    # Unsettled (e.g. an ACA-cliff jump): keep the conservative, larger-need
    # side, like solve_waterfall -- a larger draw leaves a smaller conversion.
    _need, conv, p_yr, p_state = max(tried, key=lambda t: t[0])
    return conv, p_yr, p_state


def _auto_fill_core(
    hh: Household,
    ytd: YTDSnapshot | None,
    room_fn: RoomFn,
    bracket_target: int | None = None,
    magi_strategy: str | None = None,
    net_inv_income: float = 0.0,
) -> ConversionPlan:
    """Shared body of auto_fill_12 / auto_fill_22 / auto_fill_irmaa_safe.

    The only difference between those three is how ``room`` is computed each
    year. This core does everything else identically; the room calculation is
    delegated to
    ``room_fn(fixed_gross, ded, base_magi, combined_ss, other_fixed, year, cpi,
    filing_status) -> float``. The ``filing_status`` argument is the PER-YEAR
    status (it flips to "Single" in survivor years), so each room fn must
    resolve its brackets/tiers from it rather than from the household's
    static ``hh.filing_status``.

    ``base_magi`` is always computed and passed (cheap; identical expression in
    all three originals). The 12% and 22% variants ignore it; the IRMAA-safe
    variant uses it to enforce the joint-MAGI ceiling.

    ``combined_ss`` and ``other_fixed`` (audit C14/C15, 2026-08-05 W5) are the
    pre-conversion taxable-SS inputs -- ``other_fixed`` is the SS-exclusive,
    conversion-exclusive income base (``other_fixed + tss == base_magi``).
    They let a room fn recompute taxable SS at a CANDIDATE conversion amount
    (the IRMAA-safe variant needs this to fold in the SS-torpedo effect; the
    ACA variant needs ``combined_ss`` alone, since ACA MAGI adds back the FULL
    benefit regardless of taxability). The 12%/22%/24% variants ignore both.

    Issue #598: each year is also projected (engine.scenario's single-year
    tax stack) so the room is sized against TOTAL income -- conversion plus
    any draw the waterfall forces to fund living expenses and tax (see
    _solve_year) -- and the forced draws are carried into the next year's
    balances. The projection runs with the caller's ``net_inv_income``, so
    NIIT in the year's cash need matches the final run_scenario.
    ``bracket_target`` and ``magi_strategy`` are set on the plan up front so
    every probe sees the plan the final projection will run.
    """
    plan = ConversionPlan(bracket_target=bracket_target, magi_strategy=magi_strategy)
    proj = ProjectionState.initial(hh)
    your_ira = hh.your_ira
    spouse_ira = hh.spouse_ira
    brokerage = hh.brokerage_start
    _cpi = hh.cpi_assumption
    # C16 (audit-0805 W5, mirrors engine/scenario.py's ira-rmd-1 seeding and
    # engine/asset_location.py's identical audit-0802 F7 fix): seed the prior-
    # year balance with the owner's IRA when defer_first_rmd is elected.
    # Without this, prev_*_ira stays 0.0 at iteration 0, and calc_rmd's
    # prior_year_balance > 0 guard silently suppresses the deferred prior-year
    # RMD term the first time age == rmd_start_age + 1 is reached.
    prev_your_ira = hh.your_ira if hh.your_defer_first_rmd else 0.0
    prev_spouse_ira = hh.spouse_ira if hh.spouse_defer_first_rmd else 0.0
    # Mutable copies of inherited IRA balances (SECURE Act 10-year drains), mirroring
    # engine/scenario.py:76. Their annual distributions are ordinary income and must
    # consume bracket room, else auto-fill over-converts for households with inherited IRAs.
    inherited_balances: list[float] = [iira.balance for iira in hh.inherited_iras]

    # Survivor scenario: filing-status transition + one-time IRA rollover at
    # death_year + 1, mirroring engine/scenario.py:71-98. Without this, auto-fill
    # stays MFJ forever, sums both spouses' SS, and keeps offering the deceased's
    # IRA for conversion (audit C3 / autofill-1).
    surv = hh.survivor
    _rollover_done = False

    # Spouse squeeze window: the spouse may still convert for several years after
    # your RMD starts (while sa < spouse_rmd_start_age). Hardcoding +6 truncates
    # the loop for age-gap households where the real tail is larger. Derive the
    # actual tail from the RMD-age gap, with 6 as a minimum for same-age pairs
    # (audit 0705 / headroom-scenario-4).
    _your_window = hh.your_rmd_start_age - hh.your_age  # years until your RMD
    _spouse_window = hh.spouse_rmd_start_age - hh.spouse_age  # years until spouse RMD
    _squeeze_tail = max(_spouse_window - _your_window, 6)
    for yr_idx in range(
        hh.your_rmd_start_age - 1 - hh.your_age + 1 + _squeeze_tail
    ):  # _squeeze_tail covers actual spouse tail (>= 6 for same-age backward compat)
        year = hh.base_year + yr_idx
        ya = hh.your_age + yr_idx
        sa = hh.spouse_age + yr_idx

        # === Survivor: filing status + one-time IRA rollover (mirror scenario.py:86-98) ===
        survivor_active = surv is not None and year >= surv.death_year + 1
        current_filing_status = "Single" if survivor_active else hh.filing_status
        if survivor_active and not _rollover_done:
            assert surv is not None  # narrowing: survivor_active implies surv is not None
            if surv.who_dies == "you":
                spouse_ira += your_ira
                your_ira = 0.0
            else:
                your_ira += spouse_ira
                spouse_ira = 0.0
            _rollover_done = True

        cur_your_begin = your_ira
        cur_spouse_begin = spouse_ira
        ytd_year: YTDSnapshot | None = ytd if year == hh.base_year else None

        # Option income, bounded below by NQO exercises already realized this year
        # (audit-1008 F-21): mirrors run_scenario's option_income_bounded =
        # max(scheduled, nqo_exercise_ytd) (audit-0805 C12), so realized exercises
        # above the schedule still consume bracket and MAGI room.
        nqo_ytd = ytd_year.nqo_exercise_ytd if ytd_year is not None else 0.0
        opt = max(hh.option_income(year), nqo_ytd)

        # SS: the one shared home of the widow(er) rules, so auto-fill sizes room
        # against the same survivor income the projection produces (issue #597).
        your_ss, spouse_ss = social_security_benefits(
            hh,
            ya,
            sa,
            survivor_active=survivor_active,
            who_dies=surv.who_dies if surv is not None else None,
            death_year=surv.death_year if surv is not None else None,
        )
        combined_ss = your_ss + spouse_ss

        # RMD
        # M3 (audit-0720): beneficiary is the OTHER spouse, only passed when the
        # household elects the sole-beneficiary toggle AND that spouse is alive.
        _bene_gate = hh.spouse_is_sole_beneficiary and not survivor_active
        rmd = calc_rmd(
            your_ira,
            ya,
            hh.your_rmd_start_age,
            first_year_deferred=hh.your_defer_first_rmd,
            prior_year_balance=prev_your_ira,
            beneficiary_age=sa if _bene_gate else None,
        )
        taxable_rmd = rmd  # no QCD in auto-fill (QCDs reduce income but not conversion room)
        spouse_taxable_rmd = calc_rmd(
            spouse_ira,
            sa,
            hh.spouse_rmd_start_age,
            first_year_deferred=hh.spouse_defer_first_rmd,
            prior_year_balance=prev_spouse_ira,
            beneficiary_age=ya if _bene_gate else None,
        )  # no spouse QCD in auto-fill

        # Inherited IRA drains (SECURE Act 10-year rule) are ordinary income and
        # therefore consume bracket room — mirror engine/scenario.py:204-224 so the
        # auto-fill plan does not over-convert for households with inherited IRAs.
        inherited_distribution = 0.0
        for idx, iira in enumerate(hh.inherited_iras):
            if year < iira.inherited_year:
                continue  # not yet inherited
            years_remaining = inherited_ira_years_remaining(iira.inherited_year, year)
            if years_remaining <= 0:
                continue  # fully drained
            drain = inherited_ira_drain(inherited_balances[idx], years_remaining)
            inherited_distribution += drain
            inherited_balances[idx] = max(inherited_balances[idx] - drain, 0.0) * (
                1 + iira.growth_rate
            )

        # === Base-year RMD net-of-YTD reconciliation (mirror scenario.py:186-206) ===
        # ytd_year.ira_distributions_ytd ("non-conversion IRA withdrawals") already
        # includes any RMD taken so far this year, and is re-added downstream via
        # magi_ytd (into other_fixed / base_magi) and explicitly into fixed_gross. The
        # forecast taxable RMD from calc_rmd() has no YTD awareness, so without this clamp
        # the already-taken portion of the RMD is double-counted in the base year. Reduce
        # the taxable RMD (yours first, then spouse) by the pooled YTD distributions so
        # each income aggregate nets to max(required RMD, actual distributions taken). The
        # gross RMD used for IRA-balance roll-forward (rmd / spouse_rmd) is never touched,
        # so balances are unaffected; calc_rmd() returns 0 before the start age, so this is
        # a no-op in pre-RMD years (audit 0702 / autofill-rmd-clamp).
        if ytd_year is not None and ytd_year.ira_distributions_ytd > 0:
            _dist_remaining = ytd_year.ira_distributions_ytd
            _r = min(taxable_rmd, _dist_remaining)
            taxable_rmd -= _r
            _dist_remaining -= _r
            _sr = min(spouse_taxable_rmd, _dist_remaining)
            spouse_taxable_rmd -= _sr

        # Shared ordinary-income core (opt + your gated RMD + spouse RMD + inherited),
        # reused below by other_fixed, base_magi, and fixed_gross so the three can never
        # drift apart.
        ordinary_core = (
            opt
            + (taxable_rmd if ya >= hh.your_rmd_start_age else 0)
            + spouse_taxable_rmd
            + inherited_distribution
        )

        # === Forecast brokerage income (forecast years only) — audit 0702/autofill-brokerage ===
        # In the base year, brokerage dividends and realized gains are already carried
        # by ytd_year.magi_ytd (into other_fixed / base_magi) and the itemized YTD add-
        # back in fixed_gross, so no forecast estimate is added then (mirrors the
        # ytd-is-None suppression at engine/scenario.py:265-268). For forecast years,
        # mirror run_scenario routing: ordinary dividends are ordinary income (enter
        # fixed_gross, MAGI, and SS provisional); qualified dividends and realized LTCG
        # are MAGI/provisional ONLY (preferential rate, excluded from the ordinary
        # bracket base) — see engine/scenario.py:360-361 and :629.
        brok_ordinary = 0.0
        brok_magi_extra = 0.0
        if ytd_year is None:
            brok_rate = hh.brokerage_rate(year)
            brok_appreciation_rate = (
                hh.brokerage_growth.appreciation_for(year)
                if hh.brokerage_growth is not None
                else brok_rate
            )
            _qual_div, _ord_div = compute_brokerage_dividends(
                year, hh.base_year, brokerage, hh.brokerage_growth, None
            )
            _realized_gains = brokerage * brok_appreciation_rate * hh.brok_turnover
            brok_ordinary = _ord_div
            brok_magi_extra = _ord_div + _qual_div + _realized_gains

        # Taxable SS — computed first so base_magi uses only the includable
        # fraction (IRC §86: max 85% of SS enters AGI/MAGI, not gross SS).
        # Per IRC §86(b)(2), provisional income is MAGI (AGI + tax-exempt interest),
        # not just ordinary income. For the base year, ytd_year.magi_ytd captures
        # all §86-modified-AGI components (LTCG, qualified dividends, muni interest,
        # wages, etc.) and correctly excludes SS, making it the right provisional-
        # income proxy. Forecast years include brokerage income via brok_magi_extra.
        # C-7 mirror: nqo_exercise_ytd is already in ordinary_core via the bounded opt;
        # ytd.magi_ytd carries it again → subtract once to prevent MAGI/SS double-count.
        # opt stays in fixed_gross (bracket base) so NQO counts as ordinary income there
        # (the ytd ordinary add-back below excludes it). Mirrors run_scenario's
        # option_income_for_magi = option_income_bounded - nqo_ytd.
        other_fixed = ordinary_core - nqo_ytd + brok_magi_extra
        if ytd_year is not None:
            other_fixed += ytd_year.magi_ytd

        # Fixed gross (ordinary income — no LTCG) is this ordinary base + taxable SS
        # (added in _room_at), but the base-year YTD add-back is the ordinary
        # components (not the full MAGI used for base_magi). audit-1008 F-23: use the
        # model's canonical ordinary aggregate, which is exactly run_scenario's
        # combined_gross add-back (§1222-netted/§1211-capped short-term gains, crypto
        # income, minus HSA / deductible-IRA adjustments) except for NQO, which opt
        # already carries.
        ytd_ordinary = (
            ytd_year.total_ordinary_income - ytd_year.nqo_exercise_ytd
            if ytd_year is not None
            else None
        )

        # Deductions — resolve std/senior from the PER-YEAR filing status so survivor
        # years (and single-from-the-start households) use single values.
        _af_std: float
        _af_senior: float
        if current_filing_status == "Single":
            _af_std, _af_senior = STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE
        else:
            _af_std, _af_senior = hh.std_deduction, hh.senior_extra
        # Survivor: zero the deceased's age so only the survivor counts toward the
        # senior extra and OBBBA senior bonus (mirror scenario.py:371-372).
        if survivor_active and surv is not None:
            ya_eff = 0 if surv.who_dies == "you" else ya
            sa_eff = 0 if surv.who_dies == "spouse" else sa
        else:
            ya_eff, sa_eff = ya, sa
        _ded_no_senior = deductions(
            ya_eff,
            sa_eff,
            _af_std,
            _af_senior,
            filing_status=current_filing_status,
            year=year,
            cpi=_cpi,
        )
        # OBBBA senior-bonus phase-out is measured on AGI (muni-excluded), matching
        # scenario.py:366/377 and estimate_ytd_federal_tax. base_magi carries muni
        # interest via ytd magi_ytd, so strip it here (audit C3 / autofill-2).
        _phaseout_muni = ytd_year.tax_exempt_interest_ytd if ytd_year is not None else 0.0
        inputs = _RoomInputs(
            ordinary_gross=ordinary_core + brok_ordinary,
            ytd_ordinary=ytd_ordinary,
            other_fixed=other_fixed,
            combined_ss=combined_ss,
            ded_no_senior=_ded_no_senior,
            phaseout_muni=_phaseout_muni,
            ya_eff=ya_eff,
            sa_eff=sa_eff,
            year=year,
            cpi=_cpi,
            filing_status=current_filing_status,
        )

        # Issue #598: size against the waterfall's forced draw too. Each probe
        # writes this year's cells into `plan` and projects the year on an
        # isolated copy of the projection state; the accepted probe's state
        # becomes the real one.
        def _probe(
            draw: WaterfallDraw | None,
            _inputs: _RoomInputs = inputs,
            _ya: int = ya,
            _sa: int = sa,
            _your_ira: float = your_ira,
            _spouse_ira: float = spouse_ira,
            _year: int = year,
            _yr_idx: int = yr_idx,
            _ytd_year: YTDSnapshot | None = ytd_year,
            _proj: ProjectionState = proj,
        ) -> tuple[float, YearResult, ProjectionState]:
            total = _conversion_for_draw(_inputs, draw, room_fn)
            cells = _allocate_room(hh, total, _ya, _sa, _your_ira, _spouse_ira)
            _write_cells(plan, _year, *cells, _ytd_year)
            probe_state = _proj.copy()
            outcome = project_year_with_draw(
                _yr_idx, hh, plan, ytd, net_inv_income, probe_state, draw
            )
            probe_state.advance(outcome)
            return total, outcome.yr, probe_state

        room, year_result, proj = _solve_year(_probe, proj.brokerage_basis)
        your_cell, spouse_cell = _allocate_room(hh, room, ya, sa, your_ira, spouse_ira)
        _write_cells(plan, year, your_cell, spouse_cell, ytd_year)
        # yc / sc are the still-to-come amounts (net of YTD) for the roll-forward.
        yc = your_cell or 0.0
        sc = spouse_cell or 0.0

        # Update IRAs for next year — net of the waterfall's forced draws, which
        # the projection takes from the same balances (issue #598).
        your_withdrawal = yc + rmd + year_result.forced_your_ira_draw
        your_ira = max(your_ira - your_withdrawal, 0) * (1 + hh.your_ira_rate(year))

        # C17 (audit-0805 W5): pass beneficiary_age here too, matching the
        # income-side spouse_taxable_rmd call above. Without it, this balance-
        # side recompute uses the Uniform Lifetime divisor while the income
        # side uses the (larger) Joint & Last Survivor divisor whenever the
        # sole-beneficiary election applies, understating the spouse RMD used
        # to roll the IRA balance forward and overstating conversion room in
        # every subsequent year.
        spouse_rmd = calc_rmd(
            spouse_ira,
            sa,
            hh.spouse_rmd_start_age,
            first_year_deferred=hh.spouse_defer_first_rmd,
            prior_year_balance=prev_spouse_ira,
            beneficiary_age=ya if _bene_gate else None,
        )
        spouse_ira = max(spouse_ira - sc - spouse_rmd - year_result.forced_spouse_ira_draw, 0) * (
            1 + hh.spouse_ira_rate(year)
        )

        prev_your_ira = cur_your_begin
        prev_spouse_ira = cur_spouse_begin

        # Roll the taxable brokerage forward by total return (appreciation + reinvested
        # yield), net of the waterfall's forced brokerage sale (issue #598). Simplified
        # vs engine/scenario.py:660-666, which also nets LTCG tax out and flows excess
        # RMD in — those are plan-dependent second-order effects, so autofill uses the
        # total-return proxy to size conversion room.
        brokerage = max(brokerage - year_result.forced_brokerage_draw, 0.0) * (
            1 + hh.brokerage_rate(year)
        )

    return plan


def _bracket_room_with_ss_torpedo(
    fixed_gross: float,
    ded: float,
    combined_ss: float,
    other_fixed: float,
    bracket_index: int,
    yr: int,
    cpi: float,
    filing_status: str,
) -> float:
    """Conversion room to an indexed bracket ceiling, measured on the taxable
    income that results AFTER the conversion lands.

    audit-0823 AF-1. The closed form these room fns used --
    ``room_to_bracket = max(ded + ceiling - fixed_gross, 0)``
    (engine/tax.py:284-291) -- assumes taxable ordinary income grows exactly
    $1 per $1 converted. That is false while provisional income sits in the
    IRC §86(b) 50%/85% partial-taxability band: ``fixed_gross`` carries taxable
    SS priced at the PRE-conversion base (see _auto_fill_core, which computes
    ``tss`` at ``other_fixed`` and folds it into ``fixed_gross``), so each
    converted dollar can raise taxable income by up to $1.85 as it drags more
    of the benefit into taxability. Converting the closed-form room therefore
    lands ABOVE the ceiling it was sizing to.

    This is the same defect, the same band and the same remedy as C14
    (audit-0805 W5) already applied to the IRMAA ceiling in
    :func:`_irmaa_tier1_magi_room` directly below -- and _auto_fill_core has
    been threading ``combined_ss`` and ``other_fixed`` into EVERY room fn all
    along precisely so the bracket variants could do this too. Its docstring
    said outright that the 12%/22%/24% variants ignore both; this is what
    stops them ignoring it.

    ``naive_room`` is a valid upper bound for the search: taxable SS is
    non-decreasing in provisional income, so taxable income at ``naive_room``
    is at least ``fixed_gross + naive_room - ded == ceiling``, putting the true
    root at or below it. That is the same monotonicity
    bisect_conversion_for_ceiling documents as its precondition.
    """
    ceiling = indexed_bracket_ceiling(bracket_index, year=yr, cpi=cpi, filing_status=filing_status)
    naive_room = room_to_bracket(fixed_gross, ded, ceiling)
    if naive_room <= 0.0:
        return 0.0

    # The taxable-SS figure already baked into fixed_gross, so it can be swapped
    # out for the re-priced one at each candidate conversion instead of being
    # double-counted.
    tss_base = taxable_ss(combined_ss, other_fixed, filing_status=filing_status)

    def _taxable_income_at(conv: float) -> float:
        tss_c = taxable_ss(combined_ss, other_fixed + conv, filing_status=filing_status)
        return fixed_gross - tss_base + tss_c + conv - ded

    return bisect_conversion_for_ceiling(_taxable_income_at, ceiling, naive_room)


def auto_fill_12(
    hh: Household,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that fills to the 12% bracket ceiling each year.
    Runs iteratively since each year's conversion affects the next year's IRA balance.
    Pass the ``ytd`` / ``net_inv_income`` the final run_scenario uses.
    """
    return _auto_fill_core(
        hh,
        ytd,
        room_fn=lambda fg, ded, _bm, css, of, yr, cpi, fs: _bracket_room_with_ss_torpedo(
            fg, ded, css, of, 1, yr, cpi, fs
        ),
        net_inv_income=net_inv_income,
    )


def auto_fill_22(
    hh: Household,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that fills to the 22% bracket ceiling each year.
    More aggressive than fill_12 — converts more but at higher marginal rates.
    Pass the ``ytd`` / ``net_inv_income`` the final run_scenario uses.
    """
    return _auto_fill_core(
        hh,
        ytd,
        room_fn=lambda fg, ded, _bm, css, of, yr, cpi, fs: _bracket_room_with_ss_torpedo(
            fg, ded, css, of, 2, yr, cpi, fs
        ),
        net_inv_income=net_inv_income,
    )


def bracket_fill_conversions(
    hh: Household,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> dict[int, tuple[float, float]]:
    """Year -> (Fill-to-12%, Fill-to-22%) conversion, as auto_fill_12 /
    auto_fill_22 land on it: total ordinary income INCLUDING the waterfall's
    forced IRA draw (issue #1).

    The single source for every displayed "room to the 12% / 22% top" that is
    meant to equal what Fill converts (Room This Year tiles, Sweet Spot fills),
    so none of them re-derives the draw-aware room. Base-year amounts are net
    of conversions already done (``ytd``), like the rooms they stand in for.
    Years the plans hold no cell for (past the conversion window, or no IRA
    left to convert) are absent: Fill converts nothing there.
    """
    plans = (auto_fill_12(hh, ytd, net_inv_income), auto_fill_22(hh, ytd, net_inv_income))
    done = (ytd.ira_conversions_ytd + ytd.spouse_ira_conversions_ytd) if ytd is not None else 0.0

    def _remaining(plan: ConversionPlan, year: int) -> float:
        total = plan.your_conversions.get(year, 0.0) + plan.spouse_conversions.get(year, 0.0)
        return max(total - (done if year == hh.base_year else 0.0), 0.0)

    years = {y for p in plans for y in (*p.your_conversions, *p.spouse_conversions)}
    return {y: (_remaining(plans[0], y), _remaining(plans[1], y)) for y in sorted(years)}


def _irmaa_tier1_magi_room(
    base_magi: float,
    combined_ss: float,
    other_fixed: float,
    yr: int,
    cpi: float,
    filing_status: str,
) -> float:
    """Conversion room from ``base_magi`` up to the indexed IRMAA tier-1 MAGI ceiling.

    Factored out to a single definition because TWO room fns need this exact
    ceiling and must not drift on how it is indexed or bisected:
      - ``auto_fill_irmaa_safe``'s ``_irmaa_room``, as its primary ceiling
        (which then ADDITIONALLY caps at 22%-bracket room -- that cap belongs
        to the irmaa_safe strategy, NOT to "the tier-1 ceiling", so it stays
        at that call site rather than moving in here);
      - ``auto_fill_aca``'s ``_aca_room``, as its POST-MEDICARE fallback
        ceiling, once the 400%-FPL cliff no longer applies.

    This is an IRMAA-basis MAGI ceiling, so it is measured against
    ``base_magi``, which carries only the TAXABLE portion of Social Security
    (IRC §86 + §1395r(i)(4)) -- NOT against the ACA-basis
    ``other_fixed + combined_ss`` figure ``_aca_room`` uses for the FPL cliff
    (IRC §36B(d)(2)(B)(iii) adds back the full benefit). A caller switching
    from the ACA ceiling to this one must switch MAGI bases with it.
    """
    # tier-1 MAGI ceiling — resolve tiers from the PER-YEAR filing status so a
    # survivor year uses the (lower) single tier-1 threshold, not the MFJ one.
    # IRMAA 2-year lookback — index the tier-1 ceiling to the payment year,
    # matching sweet_spot_compute and all_in_at_conversion. Income year `yr`
    # under-indexed the ceiling by 2 CPI-years.
    irmaa_threshold = irmaa_tier1_threshold(yr, filing_status, cpi)
    # C14 (audit-0805 W5): the naive `threshold - base_magi` subtraction
    # assumes taxable SS is conversion-invariant. base_magi was built
    # BEFORE this room's own conversion is added, so once provisional
    # income (other_fixed + conv) enters the 50%/85% partial-taxability
    # band, converting the naive room amount pushes MORE SS into
    # taxability than base_magi accounted for, landing actual MAGI past
    # the threshold. Bisect against the real (non-linear) post-conversion
    # MAGI instead, mirroring sweet_spot_compute.irmaa_safe_max /
    # bracket_boundary_conversion (same bug family, audit C81/C23) via the
    # shared engine.tax.bisect_conversion_for_ceiling primitive. The naive
    # value remains a valid (non-decreasing-in-conv) upper bound for the
    # search.
    naive_room = max(irmaa_threshold - base_magi, 0.0)
    if naive_room <= 0.0:
        return 0.0

    def _magi_at(conv: float) -> float:
        tss_c = taxable_ss(combined_ss, other_fixed + conv, filing_status=filing_status)
        return other_fixed + conv + tss_c

    return bisect_conversion_for_ceiling(_magi_at, irmaa_threshold, naive_room)


def auto_fill_irmaa_safe(
    hh: Household,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that maximizes conversion without triggering IRMAA.
    Caps MAGI at the first IRMAA tier threshold ($218K for 2026).
    """

    def _irmaa_room(
        fixed_gross: float,
        ded: float,
        base_magi: float,
        combined_ss: float,
        other_fixed: float,
        yr: int,
        cpi: float,
        filing_status: str,
    ) -> float:
        # Room to the IRMAA tier-1 MAGI ceiling, then capped at 22% bracket room.
        #
        # audit-0823 AF-1: the 22% cap leg was the one un-bisected term left in
        # this strategy -- the IRMAA leg above has bisected since C14, so a
        # closed-form cap here could still overshoot the 22% bracket whenever
        # the cap is what binds.
        #
        # LATENT, NOT LIVE. The cap binds only when
        #     ded + bracket_22_ceiling(yr) < irmaa_tier1_threshold(yr + 2)
        # and at statutory 2026 values that is 32,200 + 211,400 = 243,600
        # against 218,000 -- false, so the (already-bisected) IRMAA leg binds
        # first and this cap is inert. It flips only because the IRMAA tier is
        # indexed at yr+2 (the 2-year lookback) while the bracket is indexed at
        # yr, so a high enough user-set CPI outruns the bracket. Measured: at
        # cpi=0.0 this strategy runs ~$32.7K UNDER the 22% ceiling; at cpi=0.08
        # it overshoots. Fixed as a trap that would otherwise wait for a CPI
        # assumption to arm it -- not as observed breakage at default settings.
        return min(
            _irmaa_tier1_magi_room(base_magi, combined_ss, other_fixed, yr, cpi, filing_status),
            _bracket_room_with_ss_torpedo(
                fixed_gross, ded, combined_ss, other_fixed, 2, yr, cpi, filing_status
            ),
        )

    return _auto_fill_core(
        hh, ytd, room_fn=_irmaa_room, magi_strategy="irmaa_safe", net_inv_income=net_inv_income
    )


def auto_fill_24(
    hh: Household,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> ConversionPlan:
    """Fill to the 24% bracket ceiling each year.

    audit-0823 AF-1: bisected for consistency with the 12%/22% variants.
    Issue #465: this plan now declares `bracket_target=3` so run_scenario's
    conversion cap (`_base_headroom` in engine/scenario.py) resolves against
    `room_24` instead of unconditionally clipping every bracket-fill plan to
    `room_22` -- previously a 24%-targeted plan was clipped back to the 22%
    bracket before it could overshoot its own ceiling (measured at a planned
    $439,050 applied as $258,900, == 211,400 + 47,500).
    """
    return _auto_fill_core(
        hh,
        ytd,
        room_fn=lambda fg, ded, _bm, css, of, yr, cpi, fs: _bracket_room_with_ss_torpedo(
            fg, ded, css, of, 3, yr, cpi, fs
        ),
        bracket_target=3,
        net_inv_income=net_inv_income,
    )


def auto_fill_aca(
    hh: Household,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> ConversionPlan:
    """Fill only up to the ACA 400%-FPL MAGI cliff each year (conversions stop
    at that MAGI). Mirrors auto_fill_irmaa_safe's MAGI-ceiling-minus-base_magi
    room, but with the ACA ceiling and no lookback (ACA uses same-year MAGI).

    Once BOTH spouses reach Medicare age the FPL cliff no longer applies and
    the per-year room falls back to the IRMAA tier-1 ceiling -- see _aca_room
    for why that deliberately differs from engine/scenario.py's
    _strategy_magi_ceiling, which lifts the ceiling ENTIRELY in those same
    years."""

    def _aca_room(
        fixed_gross: float,
        ded: float,
        base_magi: float,
        combined_ss: float,
        other_fixed: float,
        yr: int,
        cpi: float,
        filing_status: str,
    ) -> float:
        # The 400%-FPL cliff only binds while at least one spouse is still
        # under Medicare age (engine.aca.is_pre_medicare_age: age < 65),
        # matching the gate in engine/scenario.py's _strategy_magi_ceiling.
        # This is an AGE-ONLY gate, deliberately independent of ACA
        # marketplace enrollment (engine.aca.aca_applies also requires
        # `enrolled`): gating on enrollment silently unbounds this room fn
        # for any household whose enrollment flag defaults to False
        # (Household.your_aca_enrolled / spouse_aca_enrolled both default
        # False), defeating the "aca_safe" strategy the user explicitly
        # selected.
        #
        # Once both spouses are 65+ the ACA ceiling is gone, and this room fn
        # falls back to the IRMAA tier-1 MAGI ceiling. That is a DELIBERATE
        # ASYMMETRY with _strategy_magi_ceiling, which returns None (lifts
        # entirely) in these same years. Do NOT "restore the mirror" by
        # changing either side to match the other:
        #   - _strategy_magi_ceiling defines a CONSTRAINT, reported on via
        #     ScenarioYear.magi_ceiling_converged. Post-65 there is genuinely
        #     no ACA constraint left, so it must lift; substituting IRMAA
        #     there would silently redefine aca_safe as irmaa_safe and start
        #     reporting IRMAA breaches under an ACA label.
        #   - this room fn is a HEURISTIC that must name some dollar figure to
        #     convert. With no ceiling at all its room is unbounded, so post-65
        #     it proposes converting the entire remaining balance every year --
        #     on a 60/54 $10M+$10M household that took lifetime aca_safe
        #     conversions from ~$3.3M to ~$39.0M, which is a degenerate plan,
        #     not a strategy. IRMAA tier-1 is the correct successor bound:
        #     past 65 the household is on Medicare, so the tier-1 surcharge
        #     cliff is the MAGI threshold that actually costs it money from
        #     then on.
        # The two stay consistent in the sense that matters: this fn is never
        # LOOSER than the constraint, so a plan it produces can never be
        # reported as breaching a ceiling.
        #
        # The MAGI BASIS switches with the ceiling -- IRMAA MAGI carries only
        # the taxable portion of SS (base_magi), whereas the FPL cliff below is
        # measured against full-SS ACA MAGI (other_fixed + combined_ss). See
        # _irmaa_tier1_magi_room's docstring.
        ya, sa = present_household_ages(hh, yr)
        if not (is_pre_medicare_age(ya) or is_pre_medicare_age(sa)):
            return _irmaa_tier1_magi_room(
                base_magi, combined_ss, other_fixed, yr, cpi, filing_status
            )
        ceiling = aca_ceiling_magi(filing_status, yr, cpi)
        # C15 (audit-0805 W5): ACA MAGI (IRC §36B(d)(2)(B)(iii)) adds back the
        # FULL Social Security benefit (taxable + non-taxable), whereas
        # base_magi is IRMAA-basis MAGI and carries only the taxable portion.
        # Using base_magi here dropped the non-taxable SS, understating ACA
        # MAGI and overstating cliff room. other_fixed excludes SS entirely,
        # so other_fixed + combined_ss is EXACTLY ACA MAGI (the taxable/non-
        # taxable SS split cancels out identically -- see headroom.py's
        # aca_magi for the same identity, audit C7/headroom-2), independent of
        # the conversion amount, so no bisection is needed here (unlike C14).
        aca_magi = other_fixed + combined_ss
        return max(ceiling - aca_magi, 0.0)

    return _auto_fill_core(
        hh, ytd, room_fn=_aca_room, magi_strategy="aca_safe", net_inv_income=net_inv_income
    )


def add_bracket_fill_withdrawals(
    hh: Household,
    base_plan: ConversionPlan,
    target_bracket: float = 0.22,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> ConversionPlan:
    """
    Add voluntary excess withdrawals post-RMD to fill the target bracket.

    Takes an existing plan and adds extra_withdrawals for years where
    RMD + SS don't fill the bracket, withdrawing more to top it off.
    This depletes the IRA faster, reducing future RMD pressure.
    The after-tax proceeds flow to brokerage (not Roth).

    Args:
        hh: Household parameters
        base_plan: Existing conversion plan to augment
        target_bracket: Fill up to this bracket (default 22%)
        ytd: Base-year income realised so far. Pass the same snapshot the
            final projection runs with, or the base year's room ignores that
            income and the fill overshoots the target bracket (issue #592).
        net_inv_income: Manual NII estimate; the internal base run prices
            NIIT on it like the final projection does (issue #8).
    """
    # Run the base scenario first to get IRA balances and bracket room
    from engine.scenario import run_scenario

    result = run_scenario(hh, base_plan, "temp", end_age=95, ytd=ytd, net_inv_income=net_inv_income)
    _cpi_fill = hh.cpi_assumption

    # Base (unindexed) bracket ceiling for the target rate, resolved PER YEAR from that
    # year's filing status: a survivor year (yr.filing_status == "Single") uses the Single
    # ceiling (~half the MFJ ceiling), not the household's original MFJ status (U2).
    def _base_ceiling(filing_status: str) -> float:
        brackets = BRACKETS_SINGLE if filing_status == "Single" else BRACKETS_MFJ
        ceiling = 0.0
        for ceil, rate in brackets:
            if rate <= target_bracket:
                ceiling = ceil
            else:
                break
        return ceiling

    plan = ConversionPlan(
        your_conversions=dict(base_plan.your_conversions),
        spouse_conversions=dict(base_plan.spouse_conversions),
        qcds=dict(base_plan.qcds),
        spouse_qcds=dict(base_plan.spouse_qcds),
        magi_strategy=base_plan.magi_strategy,
        bracket_target=base_plan.bracket_target,
    )

    for yr in result.years:
        if yr.your_age < hh.your_rmd_start_age:
            continue  # only post-RMD

        bracket_ceiling = _iv(_base_ceiling(yr.filing_status), yr.year, _cpi_fill)
        # Room to fill the target bracket.
        #
        # audit-0823 AF-2. `yr` came from a run_scenario over the BASE plan, so
        # BOTH inputs to the closed form
        #     room = max(yr.total_deductions + ceiling - yr.combined_gross, 0)
        # were measured before the withdrawal this loop is about to size, and
        # BOTH move once it lands:
        #
        #  leg 1 -- yr.total_deductions embeds the OBBBA senior-bonus deduction
        #    priced at the PRE-withdrawal MAGI (engine/scenario.py:572-596).
        #    The bonus phases out at $0.06 per $1 of MAGI above the threshold,
        #    so filling the bracket shrinks the very deduction the room was
        #    computed with. Measured overshoot: exactly $12,000 on an
        #    otherwise-clean MFJ fixture -- the entire couple's bonus.
        #
        #  leg 2 -- yr.combined_gross embeds yr.taxable_ss_amt, priced from a
        #    provisional-income base that includes only the BASE plan's
        #    extra_withdrawal (engine/scenario_compute.py:371-388, :427) --
        #    which is ZERO in these years, because this function is what adds
        #    it. The withdrawal raises provisional income, dragging more of the
        #    benefit into taxability under IRC §86, so combined_gross rises by
        #    MORE than the withdrawal. Measured overshoot: up to $29,113.68.
        #
        # Both are the same closed-form-overshoot family as AF-1 above and as
        # C14/C18 before it. Solve for the withdrawal whose POST-withdrawal
        # taxable income lands on the ceiling, re-pricing both terms at each
        # candidate.
        naive_room = max(yr.total_deductions + bracket_ceiling - yr.combined_gross, 0)
        if naive_room <= 0:
            continue

        def _taxable_income_at(draw: float, _yr: YearResult = yr) -> float:
            # leg 2: swap the stale taxable-SS figure for one re-priced at the
            # post-withdrawal provisional base.
            tss_at = taxable_ss(
                _yr.combined_ss,
                _yr.ss_provisional_base + draw,
                filing_status=_yr.filing_status,
            )
            gross_at = _yr.combined_gross - _yr.taxable_ss_amt + tss_at + draw
            # leg 1: the senior bonus falls as MAGI rises. MAGI moves with the
            # withdrawal AND with the extra taxable SS it dragged in.
            magi_at = _yr.magi_phaseout_basis - _yr.taxable_ss_amt + tss_at + draw
            ded_at = _yr.deductions_before_senior_bonus + senior_bonus_deduction(
                _yr.senior_bonus_ya_eff,
                _yr.senior_bonus_sa_eff,
                magi_at,
                year=_yr.year,
                cpi=_cpi_fill,
                filing_status=_yr.filing_status,
            )
            return gross_at - ded_at

        # naive_room is a valid upper bound: taxable SS is non-decreasing in
        # provisional income and the senior bonus is non-increasing in MAGI, so
        # _taxable_income_at is non-decreasing in `draw` and at naive_room is at
        # least (combined_gross + naive_room - total_deductions) == ceiling.
        # That monotonicity is exactly bisect_conversion_for_ceiling's stated
        # precondition, so a bisection converges without needing the fixed-point
        # iteration _auto_fill_core uses -- and unlike that iteration it is
        # exact rather than contraction-dependent.
        room = bisect_conversion_for_ceiling(_taxable_income_at, bracket_ceiling, naive_room)
        if room <= 0:
            continue

        # Allocate withdrawal: your IRA first, then spouse IRA for remainder.
        # Mirror the "older first, larger on tie" rule from _auto_fill_core:
        # in post-RMD years you are at or past your RMD age, so "you first"
        # is the natural primary source.
        your_available = max(yr.your_ira_begin - yr.your_rmd - yr.your_conversion, 0)
        your_extra = min(room, your_available)
        if your_extra > 1000:  # only if meaningful
            plan.extra_withdrawals[yr.year] = your_extra
            room -= your_extra

        # Offer spouse IRA for any remaining room (spouse still has balance)
        if room > 1000 and yr.spouse_ira_begin > yr.spouse_rmd:
            spouse_available = max(yr.spouse_ira_begin - yr.spouse_rmd - yr.spouse_conversion, 0)
            spouse_extra = min(room, spouse_available)
            if spouse_extra > 1000:
                plan.spouse_extra_withdrawals[yr.year] = spouse_extra

    return plan
