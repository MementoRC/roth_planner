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

from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE, irmaa_for_year, irmaa_tier
from engine.niit import NIIT_THRESHOLD_MFJ, NIIT_THRESHOLD_SINGLE
from engine.tax import (
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
    room_to_12,
    room_to_22,
    senior_bonus_deduction,
    taxable_ss,
)
from engine.tax_indexing import index_value
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

    # YTD-realized NQO spread (subtracted from planned above)
    realized_option_income_ytd: float = 0.0

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
    filing_status: str = "MFJ",
    *,
    year: int | None = None,
    cpi: float | None = None,
) -> HeadroomResult:
    """Compute remaining conversion headroom for the base year.

    Separates locked YTD actuals from planned income (option exercises)
    so the user can see headroom with and without exercising options.
    year/cpi default to hh.base_year / hh.cpi_assumption when not provided.
    """
    _year = year if year is not None else hh.base_year
    _cpi = cpi if cpi is not None else hh.cpi_assumption
    ya = hh.your_age
    sa = hh.spouse_age

    result = HeadroomResult()

    # --- YTD display values ---
    result.ytd_ordinary = ytd.total_ordinary_income
    result.ytd_ltcg = ytd.ltcg_ytd
    result.ytd_total_magi = ytd.magi_ytd
    result.conversions_done = ytd.ira_conversions_ytd

    # --- Planned income (still a choice) ---
    opt = hh.option_income(_year, early_exercise)
    # Total subtract: all NQO exercises hit the same income buckets (ordinary income, MAGI),
    # so total realized is the correct lever-reduction regardless of which grant was exercised.
    # Per-grant attribution is useful for the YTD display table but NOT for headroom math.
    realized = ytd.nqo_exercise_ytd
    result.realized_option_income_ytd = realized
    result.planned_option_income = max(0.0, opt - realized)

    # SS (unlikely at age 61, but handle generically)
    from engine.ira import ss_benefit_at_age, ss_with_cola

    # FIX #4: pass household's actual FRA (not the default 67) so early/late claim
    # adjustments use the correct reference age for each spouse.
    your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
    spouse_ss_base = ss_benefit_at_age(hh.spouse_ss_fra, hh.spouse_ss_start_age, hh.spouse_fra_age)
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

    # --- LOCKED ONLY (YTD actuals — no option exercise) ---
    #
    # Provisional-income base for §86 taxable-SS computation (F18 + F26 merged fix):
    #   magi_ytd already includes: wages, NEC, STCG, conversions, distributions,
    #   LTCG, dividends, interest, tax-exempt/muni interest, NQO exercises.
    #   F26: conversions ARE provisional income (§86(b)(2)) — do NOT subtract them.
    #   F18: LTCG + qualified dividends + muni interest also belong in provisional income.
    #   Combined: locked_other = ytd.magi_ytd (no subtractions needed).
    locked_other = ytd.magi_ytd
    locked_tss = taxable_ss(combined_ss, locked_other, filing_status=filing_status)

    # F8 fix: MAGI adds only taxable SS (≤85%), not gross combined_ss.
    # locked_tss must be computed before locked_magi (reordered from original).
    locked_magi = ytd.magi_ytd + locked_tss
    locked_niit_magi = ytd.niit_magi_ytd + locked_tss
    result.locked_magi = locked_magi

    # Deductions use locked MAGI for phaseout (conservative — planned income may change).
    # M2: Single filers use Single std deduction / senior extra (not MFJ household defaults).
    if filing_status == "Single":
        _std_ded: float = STD_DEDUCTION_SINGLE
        _senior_extra: float = SENIOR_EXTRA_SINGLE
    else:
        _std_ded = hh.std_deduction
        _senior_extra = hh.senior_extra
    # FIX #5: senior_bonus_deduction phaseout must use muni-EXCLUDED MAGI (niit_magi),
    # consistent with engine/tax.py which passes ytd.niit_magi_ytd.
    ded = deductions(ya, sa, _std_ded, _senior_extra, year=_year, cpi=_cpi)
    ded += senior_bonus_deduction(
        ya, sa, locked_niit_magi, year=_year, cpi=_cpi, filing_status=filing_status
    )

    # Ordinary gross for bracket walk: ordinary income only + taxable SS (LTCG excluded
    # from brackets per IRC §1(h)); conversions are ordinary income and stay in.
    locked_gross = ytd.total_ordinary_income + locked_tss

    result.room_to_12pct = room_to_12(
        locked_gross, ded, year=_year, cpi=_cpi, filing_status=filing_status
    )
    result.room_to_22pct = room_to_22(
        locked_gross, ded, year=_year, cpi=_cpi, filing_status=filing_status
    )
    base_irmaa_tiers = IRMAA_TIERS_SINGLE if filing_status == "Single" else IRMAA_TIERS_MFJ
    # FIX #6: IRMAA 2-year lookback — the threshold that applies is for the PAYMENT year
    # (income year + 2), not the income year itself.
    irmaa_t1 = index_value(base_irmaa_tiers[0][0], _year + 2, _cpi)
    niit_threshold = NIIT_THRESHOLD_SINGLE if filing_status == "Single" else NIIT_THRESHOLD_MFJ
    result.room_to_irmaa_t1 = max(irmaa_t1 - locked_magi, 0.0)
    result.room_to_niit = max(niit_threshold - locked_niit_magi, 0.0)

    # === WITH PLANNED (locked + option exercise) ===

    # planned_other adds only the still-to-realize option income (planned_option_income);
    # magi_ytd already contains nqo_exercise_ytd, so adding the full opt would double-count.
    planned_other = ytd.magi_ytd + result.planned_option_income
    planned_tss = taxable_ss(combined_ss, planned_other, filing_status=filing_status)

    planned_magi = ytd.magi_ytd + planned_tss + result.planned_option_income
    planned_niit_magi = ytd.niit_magi_ytd + planned_tss + result.planned_option_income
    result.projected_magi_base = planned_magi

    planned_gross = ytd.total_ordinary_income + result.planned_option_income + planned_tss

    # Recalculate deductions with full planned MAGI.
    # FIX #5 (planned path): same muni-excluded MAGI for senior_bonus_deduction phaseout.
    # M2 (planned path): same Single std deduction as locked path.
    ded_planned = deductions(ya, sa, _std_ded, _senior_extra, year=_year, cpi=_cpi)
    ded_planned += senior_bonus_deduction(
        ya, sa, planned_niit_magi, year=_year, cpi=_cpi, filing_status=filing_status
    )

    result.room_to_12pct_with_planned = room_to_12(
        planned_gross, ded_planned, year=_year, cpi=_cpi, filing_status=filing_status
    )
    result.room_to_22pct_with_planned = room_to_22(
        planned_gross, ded_planned, year=_year, cpi=_cpi, filing_status=filing_status
    )
    result.room_to_irmaa_t1_with_planned = max(irmaa_t1 - planned_magi, 0.0)
    result.room_to_niit_with_planned = max(niit_threshold - planned_niit_magi, 0.0)

    # === IRMAA relevance check (age-aware) ===
    # IRMAA only matters if someone will be on Medicare in the lookback year (income_year + 2)
    # IRMAA 2-yr lookback: index thresholds to the payment year (income_year + 2)
    irmaa_cost, _ = irmaa_for_year(
        planned_magi, ya, sa, filing_status=filing_status, year=_year + 2, cpi=_cpi
    )
    if filing_status == "MFJ":
        result.irmaa_relevant = irmaa_cost > 0 or (ya + 2 >= 65 or sa + 2 >= 65)
    else:
        result.irmaa_relevant = irmaa_cost > 0 or ya + 2 >= 65

    # Find first income year where IRMAA actually matters
    first_medicare_age = 65
    if filing_status == "MFJ":
        years_until_medicare = max(
            min(first_medicare_age - 2 - ya, first_medicare_age - 2 - sa),
            0,
        )
    else:
        years_until_medicare = max(first_medicare_age - 2 - ya, 0)
    result.irmaa_first_relevant_year = _year + years_until_medicare

    # IRMAA tier based on locked MAGI (what's already done)
    # IRMAA 2-yr lookback: index thresholds to the payment year (income_year + 2)
    result.irmaa_tier_current = irmaa_tier(
        locked_magi, filing_status=filing_status, year=_year + 2, cpi=_cpi
    )
    result.irmaa_already_triggered = result.irmaa_tier_current > 0 and result.irmaa_relevant

    # --- ACA cliff ---
    from engine.aca import FPL_1, FPL_2, aca_applies

    anyone_on_aca = aca_applies(ya, hh.your_aca_enrolled) or aca_applies(sa, hh.spouse_aca_enrolled)
    if anyone_on_aca:
        base_fpl = FPL_1 if filing_status == "Single" else FPL_2
        fpl = index_value(base_fpl, _year, _cpi)
        aca_cliff = 4.0 * fpl  # 400% FPL
        # ACA MAGI (IRC §36B(d)(2)(B)(iii)) adds back the FULL Social Security
        # benefit — taxable AND non-taxable — whereas locked_magi (IRMAA MAGI)
        # carries only the taxable portion already in AGI. Using locked_magi here
        # dropped the non-taxable SS, under-counting ACA MAGI and overstating cliff
        # room for SS-claiming ACA-age households (audit C7 / headroom-2).
        aca_magi = ytd.magi_ytd + combined_ss
        result.room_to_aca_cliff = max(aca_cliff - aca_magi, 0.0)

    return result
