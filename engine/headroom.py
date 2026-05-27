"""Conversion headroom calculator — how much room remains for Roth conversions.

Separates "locked" YTD income (already realized — can't undo) from "planned"
income (option exercises — still a choice) to give accurate headroom:

- Bracket room (12%/22%): based on ordinary income only (LTCG excluded)
- IRMAA room: based on full MAGI (LTCG included), but only if on Medicare
- NIIT room: based on full MAGI vs $250K threshold
- ACA cliff: if applicable
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.irmaa import IRMAA_TIERS_MFJ, irmaa_for_year, irmaa_tier
from engine.niit import NIIT_THRESHOLD_MFJ
from engine.tax import deductions, room_to_12, room_to_22, senior_bonus_deduction, taxable_ss
from models.household import Household
from models.ytd_income import YTDSnapshot


@dataclass
class HeadroomResult:
    """Conversion headroom against various thresholds."""

    # Full-year projected MAGI with zero additional conversion
    projected_magi_base: float = 0.0

    # Locked MAGI (YTD actuals only — no planned income)
    locked_magi: float = 0.0

    # Planned income (option exercises — still a choice)
    planned_option_income: float = 0.0

    # Ordinary bracket room (unaffected by LTCG)
    # Computed from locked income only (planned income excluded)
    room_to_12pct: float = 0.0
    room_to_22pct: float = 0.0

    # Same but including planned option income
    room_to_12pct_with_planned: float = 0.0
    room_to_22pct_with_planned: float = 0.0

    # MAGI-based thresholds (consumed by LTCG)
    # Computed from locked income only
    room_to_irmaa_t1: float = 0.0
    room_to_niit: float = 0.0
    room_to_aca_cliff: float = 0.0

    # Same but including planned option income
    room_to_irmaa_t1_with_planned: float = 0.0
    room_to_niit_with_planned: float = 0.0

    # IRMAA status
    irmaa_already_triggered: bool = False
    irmaa_tier_current: int = 0
    irmaa_relevant: bool = False  # True only if someone is on Medicare in lookback year
    irmaa_first_relevant_year: int = 0  # first income year that affects Medicare premiums

    # Display components
    ytd_ordinary: float = 0.0
    ytd_ltcg: float = 0.0
    ytd_total_magi: float = 0.0

    # Conversions already done
    conversions_done: float = 0.0


def compute_headroom(
    hh: Household,
    ytd: YTDSnapshot,
    early_exercise: bool = True,
) -> HeadroomResult:
    """Compute remaining conversion headroom for the base year.

    Separates locked YTD actuals from planned income (option exercises)
    so the user can see headroom with and without exercising options.
    """
    year = hh.base_year
    ya = hh.your_age
    sa = hh.spouse_age

    result = HeadroomResult()

    # --- YTD display values ---
    result.ytd_ordinary = ytd.total_ordinary_income
    result.ytd_ltcg = ytd.ltcg_ytd
    result.ytd_total_magi = ytd.magi_ytd
    result.conversions_done = ytd.ira_conversions_ytd

    # --- Planned income (still a choice) ---
    opt = hh.option_income(year, early_exercise)
    result.planned_option_income = opt

    # SS (unlikely at age 61, but handle generically)
    from engine.ira import ss_benefit_at_age, ss_with_cola

    your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.ss_start_age)
    spouse_ss_base = ss_benefit_at_age(hh.spouse_ss_fra, hh.ss_start_age)
    your_ss = (
        ss_with_cola(your_ss_base, ya - hh.ss_start_age, hh.ss_cola)
        if ya >= hh.ss_start_age
        else 0.0
    )
    spouse_ss = (
        ss_with_cola(spouse_ss_base, sa - hh.ss_start_age, hh.ss_cola)
        if sa >= hh.ss_start_age
        else 0.0
    )
    combined_ss = your_ss + spouse_ss

    # --- Deductions (same for both scenarios) ---
    # Use locked MAGI for deduction phaseout (conservative — planned income may change)
    locked_magi = ytd.magi_ytd + combined_ss
    result.locked_magi = locked_magi
    ded = deductions(ya, sa, hh.std_deduction, hh.senior_extra)
    ded += senior_bonus_deduction(ya, sa, locked_magi)

    # === LOCKED ONLY (YTD actuals — no option exercise) ===

    # Ordinary gross: YTD ordinary income + taxable SS (no LTCG, no options)
    locked_other = ytd.total_ordinary_income - ytd.ira_conversions_ytd
    locked_tss = taxable_ss(combined_ss, locked_other)
    locked_gross = ytd.total_ordinary_income + locked_tss

    result.room_to_12pct = room_to_12(locked_gross, ded)
    result.room_to_22pct = room_to_22(locked_gross, ded)
    result.room_to_irmaa_t1 = max(IRMAA_TIERS_MFJ[0][0] - locked_magi, 0.0)
    result.room_to_niit = max(NIIT_THRESHOLD_MFJ - locked_magi, 0.0)

    # === WITH PLANNED (locked + option exercise) ===

    planned_magi = locked_magi + opt
    result.projected_magi_base = planned_magi

    planned_other = locked_other + opt
    planned_tss = taxable_ss(combined_ss, planned_other)
    planned_gross = ytd.total_ordinary_income + opt + planned_tss

    # Recalculate deductions with full planned MAGI
    ded_planned = deductions(ya, sa, hh.std_deduction, hh.senior_extra)
    ded_planned += senior_bonus_deduction(ya, sa, planned_magi)

    result.room_to_12pct_with_planned = room_to_12(planned_gross, ded_planned)
    result.room_to_22pct_with_planned = room_to_22(planned_gross, ded_planned)
    result.room_to_irmaa_t1_with_planned = max(IRMAA_TIERS_MFJ[0][0] - planned_magi, 0.0)
    result.room_to_niit_with_planned = max(NIIT_THRESHOLD_MFJ - planned_magi, 0.0)

    # === IRMAA relevance check (age-aware) ===
    # IRMAA only matters if someone will be on Medicare in the lookback year (income_year + 2)
    irmaa_cost, _ = irmaa_for_year(planned_magi, ya, sa)
    result.irmaa_relevant = irmaa_cost > 0 or (ya + 2 >= 65 or sa + 2 >= 65)

    # Find first income year where IRMAA actually matters
    first_medicare_age = 65
    years_until_medicare = max(first_medicare_age - 2 - ya, 0)
    result.irmaa_first_relevant_year = year + years_until_medicare

    # IRMAA tier based on locked MAGI (what's already done)
    result.irmaa_tier_current = irmaa_tier(locked_magi)
    result.irmaa_already_triggered = result.irmaa_tier_current > 0 and result.irmaa_relevant

    # --- ACA cliff ---
    from engine.aca import FPL_2, aca_applies

    anyone_on_aca = aca_applies(ya, hh.your_aca_enrolled) or aca_applies(
        sa, hh.spouse_aca_enrolled
    )
    if anyone_on_aca:
        aca_cliff = 4.0 * FPL_2  # 400% FPL
        result.room_to_aca_cliff = max(aca_cliff - locked_magi, 0.0)

    return result
