"""Pure compute helpers for the scenario engine.

Functions return plain values or tuples; no Streamlit, no carry-forward
state. All stateful / ordering-sensitive blocks stay in run_scenario().
"""

from __future__ import annotations

from engine.aca import aca_applies, aca_excess_aptc_repayment, aca_subsidy_loss
from engine.ira import calc_rmd, ss_benefit_at_age, ss_with_cola
from engine.tax import (
    BRACKETS_SINGLE,
    federal_tax,
    federal_tax_single,
    marginal_rate,
    marginal_rate_single,
    room_to_12,
    room_to_22,
    room_to_bracket,
    taxable_ss,
)
from engine.tax_indexing import index_value as _index_value
from models.household import GrowthProfile, Household
from models.ytd_income import YTDSnapshot

# QCD eligibility begins at age 70½ (IRC §408(d)(8)(B)) — independent of the RMD
# beginning age. Whole-age arithmetic: 70½ rounds to 70.
QCD_MIN_AGE = 70

# ---------------------------------------------------------------------------
# Block 1 — Phase classification
# ---------------------------------------------------------------------------


def compute_phase(
    ya: int,
    sa: int,
    year: int,
    hh: Household,
    early_exercise: bool,
) -> str:
    """Return the phase label for the current year/age combination.

    Phases: "options", "clean", "ss_conv", "squeeze", "rmd".
    """
    # Option income models the 2026–2028 TXN NQO grant window.
    if year <= hh.base_year + 2 and hh.option_income(year, early_exercise) > 0:
        return "options"
    rmd_yours = hh.your_rmd_start_age
    rmd_spouse = hh.spouse_rmd_start_age
    if ya < rmd_yours and ya < 70:
        return "clean"
    if ya < rmd_yours and ya >= 70:
        return "ss_conv"
    if ya >= rmd_yours:
        return "squeeze" if sa < rmd_spouse else "rmd"
    return "clean"


# ---------------------------------------------------------------------------
# Block 2 — Brokerage dividend forecast
# ---------------------------------------------------------------------------


def compute_brokerage_dividends(
    year: int,
    base_year: int,
    brokerage: float,
    brokerage_growth: GrowthProfile | None,
    ytd: YTDSnapshot | None,
) -> tuple[float, float]:
    """Return (qualified_div, ordinary_div) for the current year.

    Skips the forecast in the base year when YTD actuals are provided.
    """
    use_forecast_divs = ytd is None or year != base_year
    if use_forecast_divs and brokerage_growth is not None:
        qual_div = brokerage_growth.qualified_div_for(year, brokerage)
        ord_div = brokerage_growth.ordinary_div_for(year, brokerage)
    else:
        qual_div = 0.0
        ord_div = 0.0
    return qual_div, ord_div


# ---------------------------------------------------------------------------
# Block 3 — Conversion net-of-YTD clamp
# ---------------------------------------------------------------------------


def compute_conversions(
    year: int,
    ya: int,
    sa: int,
    your_planned: float,
    spouse_planned: float,
    ytd_year: YTDSnapshot | None,
    your_rmd_start_age: int,
    spouse_rmd_start_age: int,
) -> tuple[float, float]:
    """Return (your_conversion, spouse_conversion) after age and YTD clamping.

    Zeroes conversions once a planner reaches their RMD start age; subtracts already-done
    YTD conversions from your_conversion.
    """
    your_conversion = your_planned
    if ya >= your_rmd_start_age:
        your_conversion = 0.0
    spouse_conversion = spouse_planned
    if sa >= spouse_rmd_start_age:
        spouse_conversion = 0.0

    if ytd_year is not None and ytd_year.ira_conversions_ytd > 0:
        remaining = max(your_conversion - ytd_year.ira_conversions_ytd, 0.0)
        your_conversion = remaining

    if ytd_year is not None and ytd_year.spouse_ira_conversions_ytd > 0:
        remaining_spouse = max(spouse_conversion - ytd_year.spouse_ira_conversions_ytd, 0.0)
        spouse_conversion = remaining_spouse

    return your_conversion, spouse_conversion


# ---------------------------------------------------------------------------
# Block 4 — RMD calculation
# ---------------------------------------------------------------------------


def compute_rmds(
    your_ira: float,
    spouse_ira: float,
    ya: int,
    sa: int,
    your_rmd_start_age: int,
    spouse_rmd_start_age: int,
    your_qcd_planned: float,
    spouse_qcd_planned: float,
    qcd_limit: float,
    your_defer_first_rmd: bool = False,
    spouse_defer_first_rmd: bool = False,
    your_prior_year_balance: float = 0.0,
    spouse_prior_year_balance: float = 0.0,
) -> tuple[float, float, float, float, float, float]:
    """Return (your_rmd, qcd, taxable_rmd, spouse_rmd, spouse_qcd, spouse_taxable_rmd).

    your_defer_first_rmd / spouse_defer_first_rmd: IRC §401(a)(9)(C)(ii) April-1 deferral
      election.  When True the first-year RMD is deferred (returns 0) and added to year 2.
    your_prior_year_balance / spouse_prior_year_balance: IRA balance at the START of the
      prior year; used to compute the deferred first-year RMD that lands in year 2.
    """
    your_rmd = calc_rmd(
        your_ira,
        ya,
        your_rmd_start_age,
        first_year_deferred=your_defer_first_rmd,
        prior_year_balance=your_prior_year_balance,
    )
    qcd = min(your_qcd_planned, qcd_limit) if ya >= QCD_MIN_AGE else 0.0
    taxable_rmd = max(your_rmd - min(qcd, your_rmd), 0)
    spouse_rmd = calc_rmd(
        spouse_ira,
        sa,
        spouse_rmd_start_age,
        first_year_deferred=spouse_defer_first_rmd,
        prior_year_balance=spouse_prior_year_balance,
    )
    spouse_qcd = min(spouse_qcd_planned, qcd_limit) if sa >= QCD_MIN_AGE else 0.0
    spouse_taxable_rmd = max(spouse_rmd - min(spouse_qcd, spouse_rmd), 0)
    return your_rmd, qcd, taxable_rmd, spouse_rmd, spouse_qcd, spouse_taxable_rmd


# ---------------------------------------------------------------------------
# Block 5 — Social Security + taxable SS
# ---------------------------------------------------------------------------


def compute_social_security(
    hh: Household,
    ya: int,
    sa: int,
    survivor_active: bool,
    who_dies: str | None,
    current_filing_status: str,
    your_conversion: float,
    spouse_conversion: float,
    taxable_rmd: float,
    spouse_taxable_rmd: float,
    extra_withdrawal: float,
    spouse_extra_withdrawal: float,
    option_income: float,
    your_inherited_distribution: float,
    spouse_inherited_distribution: float,
    ord_div_this_year: float,
    ytd_year: YTDSnapshot | None,
    qual_div_this_year: float = 0.0,
    realized_gains: float = 0.0,
) -> tuple[float, float, float, float]:
    """Return (your_ss, spouse_ss, combined_ss, taxable_ss_amt)."""
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
    # Survivor: apply SSA step-up rule from death_year + 1 onward.
    # The survivor keeps the LARGER of the two COLA-grown benefits; the smaller stops.
    # Pre-survivor years (including year-of-death MFJ year) are unchanged.
    if survivor_active and who_dies is not None:
        survivor_combined = max(your_ss, spouse_ss)
        if who_dies == "you":
            your_ss = 0.0
            spouse_ss = survivor_combined
        else:
            your_ss = survivor_combined
            spouse_ss = 0.0
    combined_ss = your_ss + spouse_ss

    # other_inc excludes SS itself (provisional income formula adds 50% SS separately
    # inside taxable_ss()). All non-SS ordinary income sources are included.
    other_inc = (
        option_income
        + your_conversion
        + spouse_conversion
        + taxable_rmd
        + spouse_taxable_rmd
        + extra_withdrawal
        + spouse_extra_withdrawal
    )
    # YTD ordinary income affects SS taxation.
    # Includes wages, NEC, STCG, ordinary dividends, conversions already
    # done, IRA distributions, and interest — matching total_ordinary_income
    # minus nqo_exercise_ytd (NQO spread is captured in option_income for
    # the base year, not double-counted here).
    if ytd_year is not None:
        other_inc += (
            ytd_year.wages_ytd
            + ytd_year.nec_income_ytd
            + ytd_year.stcg_ytd
            + ytd_year.ordinary_dividends_ytd
            + ytd_year.ira_conversions_ytd
            + ytd_year.ira_distributions_ytd
            + ytd_year.interest_ytd  # C-3: fully taxable ordinary interest (IRC §86(b)(2))
            # F3: LTCG and qualified dividends are AGI items per IRC §86(b)(2) provisional-income
            + ytd_year.ltcg_ytd
            + ytd_year.qualified_dividends_ytd
        )
    # A-3: inherited IRA distributions are AGI → required in provisional income (IRC §86(b)(2))
    other_inc += your_inherited_distribution + spouse_inherited_distribution
    # B-3: forecast ordinary brokerage dividends are ordinary income → provisional income
    # F4: forecast qualified dividends and realized brokerage gains are also AGI items
    other_inc += ord_div_this_year + qual_div_this_year + realized_gains
    taxable_ss_amt = taxable_ss(combined_ss, other_inc, filing_status=current_filing_status)
    return your_ss, spouse_ss, combined_ss, taxable_ss_amt


# ---------------------------------------------------------------------------
# Block 6 — MAGI assembly (partial — excludes realized_gains)
# ---------------------------------------------------------------------------


def compute_magi(
    option_income_for_magi: float,
    your_conversion: float,
    spouse_conversion: float,
    taxable_rmd: float,
    spouse_taxable_rmd: float,
    extra_withdrawal: float,
    spouse_extra_withdrawal: float,
    taxable_ss_amt: float,
    your_inherited_distribution: float,
    spouse_inherited_distribution: float,
    qual_div_this_year: float,
    ord_div_this_year: float,
    ytd_year: YTDSnapshot | None,
) -> float:
    """Return partial MAGI (for IRMAA/ACA) BEFORE adding realized_gains.

    CRITICAL: realized_gains is intentionally excluded — it is added
    later in the brokerage block of run_scenario() after realized_gains
    is computed. Do not include it here.

    D-1: use taxable_ss_amt (up to 85% of SS) not full combined_ss — per §1395r(i)(4).
    QCD IS excluded from MAGI, so use taxable_rmd / spouse_taxable_rmd.
    C-7: option_income_for_magi has already had nqo_exercise_ytd subtracted by caller.
    """
    magi = (
        option_income_for_magi
        + your_conversion
        + spouse_conversion
        + taxable_rmd
        + spouse_taxable_rmd
        + extra_withdrawal
        + spouse_extra_withdrawal
        + taxable_ss_amt
        + your_inherited_distribution
        + spouse_inherited_distribution
    )
    # YTD: add all MAGI components via the canonical magi_ytd property.
    # Using the property ensures parity with _auto_fill_core and avoids
    # missing fields (nec_income_ytd, ira_conversions_ytd,
    # ira_distributions_ytd were absent in the prior manual enumeration).
    if ytd_year is not None:
        magi += ytd_year.magi_ytd
    # Forecast brokerage dividends: both qual and ord affect MAGI
    magi += qual_div_this_year + ord_div_this_year
    return magi


# ---------------------------------------------------------------------------
# Block 7 — Federal tax + conversion tax
# ---------------------------------------------------------------------------


def compute_federal_tax(
    taxable_income: float,
    combined_gross: float,
    your_conversion: float,
    spouse_conversion: float,
    base_total_deductions: float,
    current_filing_status: str,
    year: int,
    cpi: float,
) -> tuple[float, float, float]:
    """Return (federal_tax_amt, marginal_bracket, conversion_tax).

    ``current_filing_status`` is the effective status for the year ("Single" for
    survivor years and single-from-the-start households; "MFJ" otherwise).
    Dispatching on it (rather than a raw survivor flag) lets a non-survivor
    Single household use the single brackets while MFJ/survivor math is unchanged.
    """
    if current_filing_status == "Single":
        federal_tax_amt = federal_tax_single(taxable_income, year=year, cpi=cpi)
        marginal_bracket = marginal_rate_single(taxable_income, year=year, cpi=cpi)
    else:
        federal_tax_amt = federal_tax(taxable_income, year=year, cpi=cpi)
        marginal_bracket = marginal_rate(taxable_income, year=year, cpi=cpi)

    base_gross = combined_gross - your_conversion - spouse_conversion
    base_taxable = max(base_gross - base_total_deductions, 0)
    if current_filing_status == "Single":
        conversion_tax = federal_tax_single(
            taxable_income, year=year, cpi=cpi
        ) - federal_tax_single(base_taxable, year=year, cpi=cpi)
    else:
        conversion_tax = federal_tax(taxable_income, year=year, cpi=cpi) - federal_tax(
            base_taxable, year=year, cpi=cpi
        )

    return federal_tax_amt, marginal_bracket, conversion_tax


# ---------------------------------------------------------------------------
# Block 8 — ACA subsidy loss + clawback
# ---------------------------------------------------------------------------


def compute_aca(
    magi: float,
    combined_ss: float,
    taxable_ss_amt: float,
    your_conversion: float,
    spouse_conversion: float,
    ya: int,
    sa: int,
    your_aca_enrolled: bool,
    spouse_aca_enrolled: bool,
    aca_benchmark_premium_annual: float,
    aca_enhanced_subsidies_active: bool,
    advance_aptc_annual: float,
    current_filing_status: str,
    year: int,
    cpi: float,
) -> tuple[float, float, float]:
    """Return (aca_magi, aca_loss, aca_clawback).

    The caller must apply aca_clawback to federal_tax_amt:
        yr.federal_tax_amt += aca_clawback
    That mutation stays in run_scenario().
    """
    num_on_aca = (1 if aca_applies(ya, your_aca_enrolled) else 0) + (
        1 if aca_applies(sa, spouse_aca_enrolled) else 0
    )
    # ACA MAGI per IRC §36B(d)(2)(B)(iii): AGI + tax-exempt interest + non-taxable SS.
    # yr.magi already includes taxable_ss_amt; add the non-taxable remainder.
    # Distinct from yr.magi (IRMAA §1839(i)(4)) which does NOT add non-taxable SS.
    aca_magi = magi + (combined_ss - taxable_ss_amt)
    # Scale the couple benchmark by the number of enrollees (used by BOTH the
    # subsidy-loss and the excess-APTC clawback below, for consistency).
    effective_benchmark = aca_benchmark_premium_annual * (num_on_aca / 2)
    if num_on_aca > 0:
        base_aca_magi = aca_magi - your_conversion - spouse_conversion
        aca_loss = aca_subsidy_loss(
            base_aca_magi,
            aca_magi,
            effective_benchmark,
            enhanced_subsidies_active=aca_enhanced_subsidies_active,
            filing_status=current_filing_status,
            year=year,
            cpi=cpi,
        )
    else:
        aca_loss = 0.0

    # P.L. 119-21 eliminated the IRC §36B(f)(2)(B) repayment cap for TY 2026+.
    # Only applies when household elected advance APTC payments.
    if advance_aptc_annual > 0:
        aca_clawback = aca_excess_aptc_repayment(
            advance_aptc_annual=advance_aptc_annual,
            actual_magi=aca_magi,
            benchmark_premium_annual=effective_benchmark,
            enhanced_subsidies_active=aca_enhanced_subsidies_active,
            filing_status=current_filing_status,
            year=year,
            cpi=cpi,
        )
    else:
        aca_clawback = 0.0

    return aca_magi, aca_loss, aca_clawback


# ---------------------------------------------------------------------------
# Block 9 — Bracket room
# ---------------------------------------------------------------------------


def compute_bracket_room(
    combined_gross: float,
    total_deductions: float,
    current_filing_status: str,
    year: int,
    cpi: float,
) -> tuple[float, float]:
    """Return (room_12, room_22) — headroom to the 12% and 22% bracket ceilings.

    Dispatches on the effective filing status so a non-survivor Single household
    uses the single-filer bracket ceilings (MFJ/survivor math unchanged).
    """
    if current_filing_status == "Single":
        room_12 = room_to_bracket(
            combined_gross,
            total_deductions,
            _index_value(BRACKETS_SINGLE[1][0], year, cpi),
        )
        room_22 = room_to_bracket(
            combined_gross,
            total_deductions,
            _index_value(BRACKETS_SINGLE[2][0], year, cpi),
        )
    else:
        room_12 = room_to_12(combined_gross, total_deductions, year=year, cpi=cpi)
        room_22 = room_to_22(combined_gross, total_deductions, year=year, cpi=cpi)
    return room_12, room_22
