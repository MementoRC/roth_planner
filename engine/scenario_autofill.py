"""Auto-fill helpers for the scenario engine.

Generates ConversionPlan instances that fill to a given bracket ceiling or
IRMAA threshold. Imported back into engine.scenario via a bottom re-export
so that all existing `from engine.scenario import auto_fill_*` callers are
unaffected.
"""

from __future__ import annotations

from collections.abc import Callable

from engine.ira import calc_rmd, ss_benefit_at_age, ss_with_cola
from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE
from engine.scenario_types import ConversionPlan
from engine.tax import (
    BRACKETS_MFJ,
    BRACKETS_SINGLE,
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
    room_to_12,
    room_to_22,
    room_to_bracket,
    senior_bonus_deduction,
    taxable_ss,
)
from engine.tax_indexing import index_value as _iv
from models.household import Household
from models.ytd_income import YTDSnapshot


def _room_to_12_fs(
    current_gross: float, total_deductions: float, *, year: int, cpi: float, filing_status: str
) -> float:
    """room_to_12 honoring filing status (single 12% ceiling for "Single")."""
    if filing_status == "Single":
        return room_to_bracket(
            current_gross, total_deductions, _iv(BRACKETS_SINGLE[1][0], year, cpi)
        )
    return room_to_12(current_gross, total_deductions, year=year, cpi=cpi)


def _room_to_22_fs(
    current_gross: float, total_deductions: float, *, year: int, cpi: float, filing_status: str
) -> float:
    """room_to_22 honoring filing status (single 22% ceiling for "Single")."""
    if filing_status == "Single":
        return room_to_bracket(
            current_gross, total_deductions, _iv(BRACKETS_SINGLE[2][0], year, cpi)
        )
    return room_to_22(current_gross, total_deductions, year=year, cpi=cpi)


def _auto_fill_core(
    hh: Household,
    early_exercise: bool,
    ytd: YTDSnapshot | None,
    room_fn: Callable[[float, float, float, int, float], float],
) -> ConversionPlan:
    """Shared body of auto_fill_12 / auto_fill_22 / auto_fill_irmaa_safe.

    The only difference between those three is how ``room`` is computed each
    year. This core does everything else identically; the room calculation is
    delegated to ``room_fn(fixed_gross, ded, base_magi, year, cpi) -> float``.

    ``base_magi`` is always computed and passed (cheap; identical expression in
    all three originals). The 12% and 22% variants ignore it; the IRMAA-safe
    variant uses it to enforce the joint-MAGI ceiling.
    """
    plan = ConversionPlan()
    your_ira = hh.your_ira
    spouse_ira = hh.spouse_ira
    _cpi = hh.cpi_assumption

    for yr_idx in range(
        hh.your_rmd_start_age - 1 - hh.your_age + 1 + 6
    ):  # +6 for spouse squeeze years
        year = hh.base_year + yr_idx
        ya = hh.your_age + yr_idx
        sa = hh.spouse_age + yr_idx
        ytd_year: YTDSnapshot | None = ytd if year == hh.base_year else None

        if ya > 80:
            break

        # Option income
        opt = hh.option_income(year, early_exercise)

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
        combined_ss = your_ss + spouse_ss

        # RMD
        rmd = calc_rmd(your_ira, ya, hh.your_rmd_start_age)
        taxable_rmd = rmd  # no QCD in auto-fill (QCDs reduce income but not conversion room)
        spouse_taxable_rmd = calc_rmd(
            spouse_ira, sa, hh.spouse_rmd_start_age
        )  # no spouse QCD in auto-fill

        # Taxable SS — computed first so base_magi uses only the includable
        # fraction (IRC §86: max 85% of SS enters AGI/MAGI, not gross SS).
        other_fixed = opt + (taxable_rmd if ya >= hh.your_rmd_start_age else 0) + spouse_taxable_rmd
        # YTD ordinary income affects SS taxation.
        # Mirrors run_scenario's combined_gross YTD block: wages, NEC, STCG,
        # ordinary dividends, conversions done, and IRA distributions all stack
        # into ordinary income. ordinary_dividends_ytd was previously omitted here
        # (math audit 2026-06-12 Priority 3), overstating bracket room by that amount.
        if ytd_year is not None:
            other_fixed += (
                ytd_year.wages_ytd
                + ytd_year.nec_income_ytd
                + ytd_year.stcg_ytd
                + ytd_year.ordinary_dividends_ytd
                + ytd_year.interest_ytd
                + ytd_year.ira_conversions_ytd
                + ytd_year.ira_distributions_ytd
            )
        tss = taxable_ss(combined_ss, other_fixed, filing_status=hh.filing_status)

        # MAGI without conversion (full MAGI — includes LTCG for IRMAA).
        # Uses taxable SS (tss) not gross combined_ss per IRC §86 + §1395r(i)(4).
        # Passed to room_fn so the IRMAA-safe variant can enforce its ceiling.
        base_magi = (
            opt
            + tss
            + (taxable_rmd if ya >= hh.your_rmd_start_age else 0)
            + spouse_taxable_rmd
        )
        if ytd_year is not None:
            base_magi += ytd_year.magi_ytd

        # Fixed gross (ordinary income — no LTCG)
        fixed_gross = (
            opt + (taxable_rmd if ya >= hh.your_rmd_start_age else 0) + spouse_taxable_rmd + tss
        )
        if ytd_year is not None:
            fixed_gross += (
                ytd_year.wages_ytd
                + ytd_year.nec_income_ytd
                + ytd_year.stcg_ytd
                + ytd_year.ordinary_dividends_ytd
                + ytd_year.interest_ytd
                + ytd_year.ira_conversions_ytd
                + ytd_year.ira_distributions_ytd
            )

        # Deductions — single-from-the-start households use single std/senior values
        _af_std: float
        _af_senior: float
        if hh.filing_status == "Single":
            _af_std, _af_senior = STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE
        else:
            _af_std, _af_senior = hh.std_deduction, hh.senior_extra
        ded = deductions(ya, sa, _af_std, _af_senior, year=year, cpi=_cpi)
        ded += senior_bonus_deduction(
            ya, sa, base_magi, year=year, cpi=_cpi, filing_status=hh.filing_status
        )

        # Room — delegated to caller's room_fn
        room = room_fn(fixed_gross, ded, base_magi, year, _cpi)

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

        spouse_rmd = calc_rmd(spouse_ira, sa, hh.spouse_rmd_start_age)
        spouse_ira = max(spouse_ira - sc - spouse_rmd, 0) * (1 + hh.spouse_ira_rate(year))

    return plan


def auto_fill_12(
    hh: Household,
    early_exercise: bool = True,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that fills to the 12% bracket ceiling each year.
    Runs iteratively since each year's conversion affects the next year's IRA balance.
    """
    return _auto_fill_core(
        hh,
        early_exercise,
        ytd,
        room_fn=lambda fg, ded, _bm, yr, cpi: _room_to_12_fs(
            fg, ded, year=yr, cpi=cpi, filing_status=hh.filing_status
        ),
    )


def auto_fill_22(
    hh: Household,
    early_exercise: bool = True,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that fills to the 22% bracket ceiling each year.
    More aggressive than fill_12 — converts more but at higher marginal rates.
    """
    return _auto_fill_core(
        hh,
        early_exercise,
        ytd,
        room_fn=lambda fg, ded, _bm, yr, cpi: _room_to_22_fs(
            fg, ded, year=yr, cpi=cpi, filing_status=hh.filing_status
        ),
    )


def auto_fill_irmaa_safe(
    hh: Household,
    early_exercise: bool = True,
    ytd: YTDSnapshot | None = None,
) -> ConversionPlan:
    """
    Generate a ConversionPlan that maximizes conversion without triggering IRMAA.
    Caps MAGI at the first IRMAA tier threshold ($218K for 2026).
    """
    # tier-1 MAGI ceiling (2026 base) — single tiers for a single-from-the-start household
    _irmaa_tiers = IRMAA_TIERS_SINGLE if hh.filing_status == "Single" else IRMAA_TIERS_MFJ
    irmaa_base_threshold = _irmaa_tiers[0][0]

    def _irmaa_room(fixed_gross: float, ded: float, base_magi: float, yr: int, cpi: float) -> float:
        # Room to IRMAA threshold (indexed), capped at 22% bracket room
        irmaa_threshold = _iv(irmaa_base_threshold, yr, cpi)
        irmaa_room = max(irmaa_threshold - base_magi, 0.0)
        return min(
            irmaa_room,
            _room_to_22_fs(fixed_gross, ded, year=yr, cpi=cpi, filing_status=hh.filing_status),
        )

    return _auto_fill_core(hh, early_exercise, ytd, room_fn=_irmaa_room)


def add_bracket_fill_withdrawals(
    hh: Household,
    base_plan: ConversionPlan,
    target_bracket: float = 0.22,
    early_exercise: bool = True,
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

    result = run_scenario(hh, base_plan, "temp", end_age=95, early_exercise=early_exercise)
    _cpi_fill = hh.cpi_assumption

    # Find the base (2026) bracket ceiling for the target rate (single brackets
    # for a single-from-the-start household).
    base_brackets = BRACKETS_SINGLE if hh.filing_status == "Single" else BRACKETS_MFJ
    base_bracket_ceiling = 0.0
    for ceil, rate in base_brackets:
        if rate <= target_bracket:
            base_bracket_ceiling = ceil
        else:
            break

    plan = ConversionPlan(
        your_conversions=dict(base_plan.your_conversions),
        spouse_conversions=dict(base_plan.spouse_conversions),
        qcds=dict(base_plan.qcds),
        spouse_qcds=dict(base_plan.spouse_qcds),
    )

    for yr in result.years:
        if yr.your_age < hh.your_rmd_start_age:
            continue  # only post-RMD

        bracket_ceiling = _iv(base_bracket_ceiling, yr.year, _cpi_fill)
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
