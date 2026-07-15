"""Auto-fill helpers for the scenario engine.

Generates ConversionPlan instances that fill to a given bracket ceiling or
IRMAA threshold. Callers import directly from engine.scenario_autofill.
"""

from __future__ import annotations

from collections.abc import Callable

from engine.aca import aca_ceiling_magi
from engine.ira import calc_rmd, inherited_ira_drain, ss_benefit_at_age, ss_with_cola
from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE
from engine.scenario_compute import compute_brokerage_dividends
from engine.scenario_types import ConversionPlan
from engine.tax import (
    BRACKETS_MFJ,
    BRACKETS_SINGLE,
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
    room_to_12,
    room_to_22,
    room_to_24,
    senior_bonus_deduction,
    taxable_ss,
)
from engine.tax_indexing import index_value as _iv
from models.household import Household
from models.ytd_income import YTDSnapshot


def _auto_fill_core(
    hh: Household,
    ytd: YTDSnapshot | None,
    room_fn: Callable[[float, float, float, int, float, str], float],
) -> ConversionPlan:
    """Shared body of auto_fill_12 / auto_fill_22 / auto_fill_irmaa_safe.

    The only difference between those three is how ``room`` is computed each
    year. This core does everything else identically; the room calculation is
    delegated to
    ``room_fn(fixed_gross, ded, base_magi, year, cpi, filing_status) -> float``.
    The ``filing_status`` argument is the PER-YEAR status (it flips to "Single"
    in survivor years), so each room fn must resolve its brackets/tiers from it
    rather than from the household's static ``hh.filing_status``.

    ``base_magi`` is always computed and passed (cheap; identical expression in
    all three originals). The 12% and 22% variants ignore it; the IRMAA-safe
    variant uses it to enforce the joint-MAGI ceiling.
    """
    plan = ConversionPlan()
    your_ira = hh.your_ira
    spouse_ira = hh.spouse_ira
    brokerage = hh.brokerage_start
    _cpi = hh.cpi_assumption
    prev_your_ira = 0.0
    prev_spouse_ira = 0.0
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

        # Option income
        opt = hh.option_income(year)

        # SS
        your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
        spouse_ss_base = ss_benefit_at_age(
            hh.spouse_ss_fra, hh.spouse_ss_start_age, hh.spouse_fra_age
        )
        your_ss = (
            ss_with_cola(your_ss_base, ya - hh.your_ss_start_age, hh.ss_cola)
            if ya >= hh.your_ss_start_age
            else 0.0
        )
        spouse_ss = (
            ss_with_cola(spouse_ss_base, sa - hh.spouse_ss_start_age, hh.ss_cola)
            if sa >= hh.spouse_ss_start_age
            else 0.0
        )
        # Survivor SS step-up (mirror compute_social_security in scenario_compute.py):
        # from death_year + 1 the survivor keeps the LARGER of the two COLA-grown
        # benefits; the smaller stops. Pre-survivor years keep the sum.
        if survivor_active and surv is not None:
            survivor_combined = max(your_ss, spouse_ss)
            if surv.who_dies == "you":
                your_ss, spouse_ss = 0.0, survivor_combined
            else:
                your_ss, spouse_ss = survivor_combined, 0.0
        combined_ss = your_ss + spouse_ss

        # RMD
        rmd = calc_rmd(
            your_ira,
            ya,
            hh.your_rmd_start_age,
            first_year_deferred=hh.your_defer_first_rmd,
            prior_year_balance=prev_your_ira,
        )
        taxable_rmd = rmd  # no QCD in auto-fill (QCDs reduce income but not conversion room)
        spouse_taxable_rmd = calc_rmd(
            spouse_ira,
            sa,
            hh.spouse_rmd_start_age,
            first_year_deferred=hh.spouse_defer_first_rmd,
            prior_year_balance=prev_spouse_ira,
        )  # no spouse QCD in auto-fill

        # Inherited IRA drains (SECURE Act 10-year rule) are ordinary income and
        # therefore consume bracket room — mirror engine/scenario.py:204-224 so the
        # auto-fill plan does not over-convert for households with inherited IRAs.
        inherited_distribution = 0.0
        for idx, iira in enumerate(hh.inherited_iras):
            if year < iira.inherited_year:
                continue  # not yet inherited
            years_remaining = 10 - (year - iira.inherited_year)
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
        # C-7 mirror: nqo_exercise_ytd is already in ordinary_core via opt; ytd.magi_ytd
        # carries it again → subtract once to prevent MAGI/SS double-count. opt remains
        # unchanged so fixed_gross (bracket base) correctly includes NQO as ordinary income
        # (it is not in the ytd ordinary add-back list below). Mirrors scenario.py:324-326.
        nqo_ytd = ytd_year.nqo_exercise_ytd if ytd_year is not None else 0.0
        other_fixed = ordinary_core - nqo_ytd + brok_magi_extra
        if ytd_year is not None:
            other_fixed += ytd_year.magi_ytd
        tss = taxable_ss(combined_ss, other_fixed, filing_status=current_filing_status)

        # MAGI without conversion (full MAGI — includes LTCG for IRMAA).
        # Uses taxable SS (tss) not gross combined_ss per IRC §86 + §1395r(i)(4).
        # Equals other_fixed + tss since other_fixed already carries ytd magi_ytd.
        # Passed to room_fn so the IRMAA-safe variant can enforce its ceiling.
        base_magi = other_fixed + tss

        # Fixed gross (ordinary income — no LTCG). Same ordinary core + taxable SS,
        # but the base-year YTD add-back is the itemized ordinary components (not the
        # full MAGI used for base_magi).
        fixed_gross = ordinary_core + brok_ordinary + tss
        if ytd_year is not None:
            fixed_gross += (
                ytd_year.wages_ytd
                + ytd_year.nec_income_ytd
                + ytd_year.stcg_ytd
                + ytd_year.ordinary_dividends_ytd
                + ytd_year.interest_ytd
                + ytd_year.ira_conversions_ytd
                + ytd_year.spouse_ira_conversions_ytd
                + ytd_year.ira_distributions_ytd
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
        ded = deductions(ya_eff, sa_eff, _af_std, _af_senior, filing_status=current_filing_status, year=year, cpi=_cpi)
        # OBBBA senior-bonus phase-out is measured on AGI (muni-excluded), matching
        # scenario.py:366/377 and estimate_ytd_federal_tax. base_magi carries muni
        # interest via ytd magi_ytd, so strip it here (audit C3 / autofill-2).
        _phaseout_muni = ytd_year.tax_exempt_interest_ytd if ytd_year is not None else 0.0
        ded += senior_bonus_deduction(
            ya_eff,
            sa_eff,
            base_magi - _phaseout_muni,
            year=year,
            cpi=_cpi,
            filing_status=current_filing_status,
        )

        # Room — delegated to caller's room_fn (per-year filing status flips to Single
        # in survivor years so the ceiling/tier is resolved correctly each year).
        room = room_fn(fixed_gross, ded, base_magi, year, _cpi, current_filing_status)

        # Allocate room
        # Symmetric allocation: older pre-RMD person first (drains the IRA closest to RMD).
        # On age tie, larger IRA first. Both criteria are symmetric under me↔spouse swap.
        you_first = (ya > sa) or (ya == sa and your_ira >= spouse_ira)

        if you_first:
            if ya < hh.your_rmd_start_age and room > 0:
                yc = min(room, your_ira)
                plan.your_conversions[year] = yc
                room -= yc
            else:
                yc = 0

            if sa < hh.spouse_rmd_start_age and room > 0:
                sc = min(room, spouse_ira)
                plan.spouse_conversions[year] = sc
                room -= sc
            else:
                sc = 0
        else:
            if sa < hh.spouse_rmd_start_age and room > 0:
                sc = min(room, spouse_ira)
                plan.spouse_conversions[year] = sc
                room -= sc
            else:
                sc = 0

            if ya < hh.your_rmd_start_age and room > 0:
                yc = min(room, your_ira)
                plan.your_conversions[year] = yc
                room -= yc
            else:
                yc = 0

        # Update IRAs for next year
        your_withdrawal = yc + rmd
        your_ira = max(your_ira - your_withdrawal, 0) * (1 + hh.your_ira_rate(year))

        spouse_rmd = calc_rmd(
            spouse_ira,
            sa,
            hh.spouse_rmd_start_age,
            first_year_deferred=hh.spouse_defer_first_rmd,
            prior_year_balance=prev_spouse_ira,
        )
        spouse_ira = max(spouse_ira - sc - spouse_rmd, 0) * (1 + hh.spouse_ira_rate(year))

        prev_your_ira = cur_your_begin
        prev_spouse_ira = cur_spouse_begin

        # Roll the taxable brokerage forward by total return (appreciation + reinvested
        # yield). Simplified vs engine/scenario.py:660-666, which also nets LTCG tax out
        # and flows excess RMD in — those are plan-dependent second-order effects, so
        # autofill uses the total-return proxy to size conversion room.
        brokerage = brokerage * (1 + hh.brokerage_rate(year))

    return plan


def auto_fill_12(
    hh: Household,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that fills to the 12% bracket ceiling each year.
    Runs iteratively since each year's conversion affects the next year's IRA balance.
    """
    return _auto_fill_core(
        hh,
        ytd,
        room_fn=lambda fg, ded, _bm, yr, cpi, fs: room_to_12(
            fg, ded, year=yr, cpi=cpi, filing_status=fs
        ),
    )


def auto_fill_22(
    hh: Household,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that fills to the 22% bracket ceiling each year.
    More aggressive than fill_12 — converts more but at higher marginal rates.
    """
    return _auto_fill_core(
        hh,
        ytd,
        room_fn=lambda fg, ded, _bm, yr, cpi, fs: room_to_22(
            fg, ded, year=yr, cpi=cpi, filing_status=fs
        ),
    )


def auto_fill_irmaa_safe(
    hh: Household,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that maximizes conversion without triggering IRMAA.
    Caps MAGI at the first IRMAA tier threshold ($218K for 2026).
    """
    def _irmaa_room(
        fixed_gross: float, ded: float, base_magi: float, yr: int, cpi: float, filing_status: str
    ) -> float:
        # tier-1 MAGI ceiling — resolve tiers from the PER-YEAR filing status so a
        # survivor year uses the (lower) single tier-1 threshold, not the MFJ one.
        _irmaa_tiers = IRMAA_TIERS_SINGLE if filing_status == "Single" else IRMAA_TIERS_MFJ
        irmaa_base_threshold = _irmaa_tiers[0][0]
        # Room to IRMAA threshold (indexed), capped at 22% bracket room
        # IRMAA 2-year lookback — index the tier-1 ceiling to the payment year
        # (yr + 2), matching sweet_spot_compute._index_irmaa_tiers(yr + 2) and
        # all_in_at_conversion. Income year `yr` under-indexed the ceiling by 2 CPI-years.
        irmaa_threshold = _iv(irmaa_base_threshold, yr + 2, cpi)
        irmaa_room = max(irmaa_threshold - base_magi, 0.0)
        return min(
            irmaa_room,
            room_to_22(fixed_gross, ded, year=yr, cpi=cpi, filing_status=filing_status),
        )

    return _auto_fill_core(hh, ytd, room_fn=_irmaa_room)


def auto_fill_24(
    hh: Household,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """Fill to the 24% bracket ceiling each year."""
    return _auto_fill_core(
        hh,
        ytd,
        room_fn=lambda fg, ded, _bm, yr, cpi, fs: room_to_24(
            fg, ded, year=yr, cpi=cpi, filing_status=fs
        ),
    )


def auto_fill_aca(
    hh: Household,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """Fill only up to the ACA 400%-FPL MAGI cliff each year (conversions stop
    at that MAGI). Mirrors auto_fill_irmaa_safe's MAGI-ceiling-minus-base_magi
    room, but with the ACA ceiling and no lookback (ACA uses same-year MAGI)."""
    def _aca_room(
        fixed_gross: float, ded: float, base_magi: float, yr: int, cpi: float, filing_status: str
    ) -> float:
        ceiling = aca_ceiling_magi(filing_status, yr, cpi)
        return max(ceiling - base_magi, 0.0)

    return _auto_fill_core(hh, ytd, room_fn=_aca_room)


def add_bracket_fill_withdrawals(
    hh: Household,
    base_plan: ConversionPlan,
    target_bracket: float = 0.22,
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
    """
    # Run the base scenario first to get IRA balances and bracket room
    from engine.scenario import run_scenario

    result = run_scenario(hh, base_plan, "temp", end_age=95)
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
    )

    for yr in result.years:
        if yr.your_age < hh.your_rmd_start_age:
            continue  # only post-RMD

        bracket_ceiling = _iv(_base_ceiling(yr.filing_status), yr.year, _cpi_fill)
        # Room to fill the target bracket
        room = max(yr.total_deductions + bracket_ceiling - yr.combined_gross, 0)
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
