"""Pure compute helpers for the Scenario Comparator view.

Returns plain dataclasses with raw numeric values; view layer formats
via fmt_dollars / fmt_pct / fmt_dollars_short.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.ira import calc_rmd, ss_with_cola
from engine.scenario import (
    ConversionPlan,
    ScenarioResult,
    YearResult,
    run_no_conversion,
    run_scenario,
)
from engine.scenario_autofill import (
    add_bracket_fill_withdrawals,
    auto_fill_12,
    auto_fill_22,
    auto_fill_irmaa_safe,
)
from engine.scenario_compute import compute_brokerage_dividends
from engine.tax import (
    LTCG_RATES_MFJ,
    LTCG_THRESHOLDS_SINGLE,
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
    federal_tax_single,
    marginal_rate_single,
    senior_bonus_deduction,
    taxable_ss,
)
from engine.tax_indexing import index_tuple
from models.household import Household, SurvivorScenario


def survivor_year_tax(
    survivor_age: int,
    rmd: float,
    survivor_ss: float,
    *,
    year: int,
    cpi: float,
    brok_ord_income: float = 0.0,  # ordinary brokerage income (ordinary divs + interest + STCG)
    brok_ltcg_income: float = 0.0,  # LTCG-rate brokerage income (qualified divs + realized gains)
) -> tuple[float, float, float]:
    """Tax, marginal rate, and taxable income for a Single survivor in a future year.

    The function handles brokerage income in three distinct buckets (IRC §86(b)(2)):
    - brok_ord_income: enters ordinary SS-provisional income AND the federal-tax base
    - brok_ltcg_income: enters SS-provisional income and OBBBA senior-bonus MAGI,
                        and is taxed at preferential 0/15/20% LTCG rates (IRC §1(h))
                        via a stack-walk stacked on top of taxable_ordinary.

    Index the standard deduction and brackets to the projection year (via cpi) so
    inflation-grown income is taxed against indexed thresholds, matching the main
    engine (scenario.py) rather than raw BASE_YEAR (2026) constants.

    Returns (total_tax, marginal_rate, taxable_ordinary) where total_tax includes
    both the ordinary bracket tax and the LTCG preferential tax.
    """
    # (a) Taxable SS provisional income includes ALL brokerage income (IRC §86(b)(2))
    tss = taxable_ss(survivor_ss, rmd + brok_ord_income + brok_ltcg_income, filing_status="Single")
    # (b) Ordinary gross adds ONLY ordinary brokerage income (NOT LTCG/qualified divs)
    gross = rmd + tss + brok_ord_income
    ded = deductions(survivor_age, 0, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, filing_status="Single", year=year, cpi=cpi)
    # (c) senior_bonus_deduction uses full MAGI: ordinary gross + LTCG-rate income
    _survivor_magi = (
        gross + brok_ltcg_income
    )  # gross already has brok_ord_income; add LTCG-rate income for MAGI
    ded += senior_bonus_deduction(
        survivor_age, 0, _survivor_magi, year=year, cpi=cpi, filing_status="Single"
    )
    taxable_ordinary = max(gross - ded, 0.0)
    ordinary_tax = federal_tax_single(taxable_ordinary, year=year, cpi=cpi)

    # (d) LTCG stack-walk: brok_ltcg_income taxed at 0/15/20% stacked on taxable_ordinary
    # (IRC §1(h); mirrors estimate_ytd_federal_tax in engine/tax.py:391-405)
    ltcg_tax = 0.0
    if brok_ltcg_income > 0.0:
        _ltcg_thresholds = index_tuple(LTCG_THRESHOLDS_SINGLE, year, cpi)
        ltcg_start = taxable_ordinary
        ltcg_end = taxable_ordinary + brok_ltcg_income
        ltcg_at_15 = max(
            0.0,
            min(ltcg_end, _ltcg_thresholds[1]) - max(ltcg_start, _ltcg_thresholds[0]),
        )
        ltcg_at_20 = max(0.0, ltcg_end - max(ltcg_start, _ltcg_thresholds[1]))
        ltcg_tax = ltcg_at_15 * LTCG_RATES_MFJ[1] + ltcg_at_20 * LTCG_RATES_MFJ[2]

    return (
        ordinary_tax + ltcg_tax,
        marginal_rate_single(taxable_ordinary, year=year, cpi=cpi),
        taxable_ordinary,
    )


# ---------------------------------------------------------------------------
# Relocated helpers (previously private in views/comparator.py)
# ---------------------------------------------------------------------------


def survivor_death_ages(hh: Household) -> tuple[str, list[int]]:
    """Return (who_dies, death_ages_to_sweep) based on hh.survivor.

    When hh.survivor is set: single-element list from the configured scenario.
    When hh.survivor is None: default sweep [70, 75, 80, 85] treating 'you' as dying.
    """
    surv: SurvivorScenario | None = hh.survivor
    if surv is not None:
        who_dies = surv.who_dies
        base_age = hh.your_age if who_dies == "you" else hh.spouse_age
        death_age = base_age + (surv.death_year - hh.base_year)
        return who_dies, [death_age]
    return "you", [70, 75, 80, 85]


def compute_survivor_snapshot(
    hh: Household,
    scenarios: list[ScenarioResult],
    who_dies: str,
    death_ages: list[int],
) -> list[dict[str, str]]:
    """Compute survivor analysis rows (pure function, no Streamlit calls).

    For each death_age and scenario, projects the surviving spouse's tax
    burden 5 years after the death year using Single-filer rules.

    Fixes vs legacy inline code:
    - Uses the SURVIVING spouse's per-person rmd_start_age (not deprecated hh.rmd_start_age)
    - Passes filing_status="Single" to taxable_ss (correct $25K/$34K thresholds)
    - Supports who_dies="spouse" in addition to who_dies="you"
    """
    if who_dies == "you":
        death_col = "Your Death Age"
        survivor_col = "Spouse Age"
        survivor_rmd_start = hh.spouse_rmd_start_age

        def _surv_age(deceased_age: int, proj: int) -> int:
            return (deceased_age - hh.age_gap) + proj

        def _surv_rate(year: int) -> float:
            return hh.spouse_ira_rate(year)

        def _yr_death_match(y: YearResult, da: int) -> bool:
            return y.your_age == da

    else:
        death_col = "Spouse Death Age"
        survivor_col = "Your Age"
        survivor_rmd_start = hh.your_rmd_start_age

        def _surv_age(deceased_age: int, proj: int) -> int:  # type: ignore[misc]
            return (deceased_age + hh.age_gap) + proj

        def _surv_rate(year: int) -> float:  # type: ignore[misc]
            return hh.your_ira_rate(year)

        def _yr_death_match(y: YearResult, da: int) -> bool:  # type: ignore[misc]
            return y.spouse_age == da

    deceased_base_age = hh.your_age if who_dies == "you" else hh.spouse_age

    rows: list[dict[str, str]] = []
    for death_age in death_ages:
        row: dict[str, str] = {
            death_col: str(death_age),
            survivor_col: str(_surv_age(death_age, 0)),
        }
        for s in scenarios:
            yr_death = next((y for y in s.years if _yr_death_match(y, death_age)), None)
            if not yr_death:
                row[f"{s.name} Inherited IRA"] = "---"
                row[f"{s.name} Survivor Tax"] = "---"
                row[f"{s.name} Bracket"] = "---"
                continue

            inherited_ira = yr_death.your_ira_end + yr_death.spouse_ira_end
            survivor_ss = max(yr_death.your_ss, yr_death.spouse_ss)

            proj_years = 5
            survivor_age = _surv_age(death_age, proj_years)
            death_year_calc = hh.base_year + (death_age - deceased_base_age)

            # Project IRA year-by-year, deducting RMD each year before growing.
            # A single-rate end-compounding ignores ~5 years of RMD withdrawals and
            # overstates the inherited balance fed into the tax projection.
            ira_balance = float(inherited_ira)
            rmd = 0.0
            for proj_offset in range(proj_years):
                year_offset = proj_offset + 1
                age_at_offset = _surv_age(death_age, year_offset)
                year_at_offset = death_year_calc + year_offset
                rmd_withdrawal = calc_rmd(ira_balance, age_at_offset, survivor_rmd_start)
                ira_balance = max(ira_balance - rmd_withdrawal, 0.0)
                ira_balance *= 1 + _surv_rate(year_at_offset)
                if proj_offset == proj_years - 1:
                    # scenario-compare-1 fix: capture the proj-year RMD taken at
                    # survivor_age on the START-of-proj-year balance (pre-growth).
                    # Re-computing calc_rmd on the post-growth ira_grown balance
                    # overstates the RMD by ~one year of growth (Treas. Reg.
                    # §1.401(a)(9)-9; RMD denominator = prior-year-end balance).
                    rmd = rmd_withdrawal

            ss_grown = ss_with_cola(survivor_ss, proj_years, hh.ss_cola) if survivor_ss > 0 else 0.0

            # Project brokerage balance 5 years forward from the death-year balance,
            # mirroring the IRA loop pattern above (compare-M3ss / compare-M7senior fix).
            # The survivor's brokerage income (ordinary divs + qualified divs) is
            # material for taxable SS and the OBBBA senior-bonus MAGI phase-out.
            brok_balance = yr_death.brokerage_balance
            proj_year = death_year_calc + proj_years
            for proj_offset in range(proj_years):
                year_at_offset = death_year_calc + proj_offset + 1
                brok_rate = hh.brokerage_rate(year_at_offset)
                if hh.brokerage_growth is not None:
                    brok_appreciation_rate = hh.brokerage_growth.appreciation_for(year_at_offset)
                else:
                    brok_appreciation_rate = brok_rate
                # Realized gains: turnover fraction of appreciation (mirrors engine/scenario.py)
                brok_realized = brok_balance * brok_appreciation_rate * hh.brok_turnover
                # Dividends for this growth year (no YTD actuals in the survivor projection)
                brok_qual, brok_ord = compute_brokerage_dividends(
                    year_at_offset, hh.base_year, brok_balance, hh.brokerage_growth, None
                )
                total_div = brok_qual + brok_ord
                # scenario-compare-3 fix: subtract only the LTCG TAX on realized gains
                # (not the full realized amount).  The main engine (scenario.py:605-611)
                # subtracts brokerage_gain_tax, not the principal.  Subtracting the full
                # brok_realized treats realized gains as a cash outflow (like a withdrawal)
                # rather than a tax liability, understating the balance by ~turnover×gain
                # per year (≈3.5% annually vs ≈0.5% under the correct tax-only drain).
                # Use the same LTCG 0/15/20 stack-walk as survivor_year_tax; stack on
                # taxable_ordinary=0 (conservative: survivor has no other taxable income
                # in the projection loop — the tax calc below happens separately for the
                # final year).  This matches engine/scenario.py's brokerage_gain_tax logic.
                _ltcg_thr = index_tuple(LTCG_THRESHOLDS_SINGLE, year_at_offset, hh.cpi_assumption)
                _ltcg_at_15 = max(0.0, min(brok_realized, _ltcg_thr[1]) - _ltcg_thr[0])
                _ltcg_at_20 = max(0.0, brok_realized - _ltcg_thr[1])
                brok_gain_tax = (
                    _ltcg_at_15 * LTCG_RATES_MFJ[1] + _ltcg_at_20 * LTCG_RATES_MFJ[2]
                )
                # Grow balance: subtract only the tax on realized gains (not the gains themselves)
                brok_balance = (
                    brok_balance + brok_balance * brok_appreciation_rate - brok_gain_tax + total_div
                )
            # Derive the projection-year income split from the grown balance
            brok_qual_proj, brok_ord_proj = compute_brokerage_dividends(
                proj_year, hh.base_year, brok_balance, hh.brokerage_growth, None
            )
            brok_appreciation_rate_proj = (
                hh.brokerage_growth.appreciation_for(proj_year)
                if hh.brokerage_growth is not None
                else hh.brokerage_rate(proj_year)
            )
            # Realized gains modeled as turnover × appreciation (same formula as main engine)
            brok_realized_proj = brok_balance * brok_appreciation_rate_proj * hh.brok_turnover
            # Map to buckets: ordinary (ordinary divs) vs LTCG-rate (qualified divs + realized gains)
            brok_ord_income = brok_ord_proj
            brok_ltcg_income = brok_qual_proj + brok_realized_proj

            # Single survivor, indexed to the projection year so inflation-grown
            # income is taxed against indexed brackets + deduction (not raw 2026).
            tax, bracket, _taxable = survivor_year_tax(
                survivor_age,
                rmd,
                ss_grown,
                year=proj_year,
                cpi=hh.cpi_assumption,
                brok_ord_income=brok_ord_income,
                brok_ltcg_income=brok_ltcg_income,
            )

            from views._format import fmt_dollars, fmt_dollars_short, fmt_pct  # noqa: PLC0415

            row[f"{s.name} Inherited IRA"] = fmt_dollars_short(inherited_ira, decimals=2)
            row[f"{s.name} Survivor Tax"] = f"{fmt_dollars(tax)}/yr"
            row[f"{s.name} Bracket"] = fmt_pct(bracket, 0)

        rows.append(row)
    return rows


def build_scenario(hh: Household, key: str) -> ScenarioResult:
    """Build a scenario from a preset key."""
    if key == "no_conv":
        return run_no_conversion(hh, end_age=95)
    if key == "fill_12":
        return run_scenario(hh, auto_fill_12(hh), "Fill to 12%", end_age=95)
    if key == "fill_12_bf":
        base = auto_fill_12(hh)
        plan = add_bracket_fill_withdrawals(hh, base, target_bracket=0.22)
        return run_scenario(hh, plan, "Fill 12% + Bracket Fill", end_age=95)
    if key == "fill_22":
        return run_scenario(hh, auto_fill_22(hh), "Fill to 22%", end_age=95)
    if key == "irmaa_safe":
        return run_scenario(hh, auto_fill_irmaa_safe(hh), "IRMAA-Safe Max", end_age=95)
    if key == "custom":
        import streamlit as st  # noqa: PLC0415 — deferred: engine must not import st at module level

        plan = ConversionPlan(
            your_conversions=dict(st.session_state.get("conv_plan_your", {})),
            spouse_conversions=dict(st.session_state.get("conv_plan_spouse", {})),
            qcds=dict(st.session_state.get("conv_plan_qcd", {})),
        )
        return run_scenario(hh, plan, "Custom Plan", end_age=95)
    return run_no_conversion(hh, end_age=95)


# ---------------------------------------------------------------------------
# New compute dataclasses and functions
# ---------------------------------------------------------------------------


@dataclass
class ScenarioSummary:
    """Aggregated metrics for one scenario."""

    name: str
    total_conv: float
    conv_tax: float
    avg_rate: float  # conv_tax / total_conv (or 0)
    lifetime_tax: float  # sum of federal_tax_amt across years
    lifetime_irmaa: float  # sum of irmaa_cost
    lifetime_brok_tax: float  # sum of brokerage_gain_tax
    lifetime_aca_loss: float  # sum of aca_loss (ACA subsidy lost)
    lifetime_niit: float  # sum of niit_cost
    all_in_cost: float  # tax + irmaa + brok + aca_loss + niit (matches Sweet Spot / ACA+IRMAA "all-in")
    savings_vs_baseline: float  # baseline.all_in_cost - this.all_in_cost (positive = SAVES money vs baseline)
    ira_at_75: float  # IRA + Roth combined at your_age == 75 (grid-01 fix: includes roth begins)
    ira_at_85: float
    ira_at_95: float


def compute_summary_rows(
    scenarios: list[ScenarioResult],
    baseline: ScenarioResult,
) -> list[ScenarioSummary]:
    """Aggregate per-scenario summary metrics.

    Baseline is the first scenario (no-conversion run).
    savings_vs_baseline = baseline.all_in_cost - this.all_in_cost
    (positive = saves money vs baseline; negative = costs more).
    """

    def _total_conv(s: ScenarioResult) -> float:
        return s.total_your_conv + s.total_spouse_conv

    def _lifetime_tax(s: ScenarioResult) -> float:
        return sum(yr.federal_tax_amt for yr in s.years)

    def _lifetime_irmaa(s: ScenarioResult) -> float:
        return sum(yr.irmaa_cost for yr in s.years)

    def _lifetime_brok_tax(s: ScenarioResult) -> float:
        return sum(yr.brokerage_gain_tax for yr in s.years)

    def _lifetime_aca(s: ScenarioResult) -> float:
        return sum(yr.aca_loss for yr in s.years)

    def _lifetime_niit(s: ScenarioResult) -> float:
        return sum(yr.niit_cost for yr in s.years)

    def _ira_at_age(s: ScenarioResult, age: int) -> float:
        # Value includes Roth balances so converted principal is not invisible (grid-01).
        yr = next((y for y in s.years if y.your_age == age), None)
        return (
            (yr.your_ira_begin + yr.spouse_ira_begin + yr.your_roth_begin + yr.spouse_roth_begin)
            if yr
            else 0.0
        )

    baseline_all_in = (
        _lifetime_tax(baseline)
        + _lifetime_irmaa(baseline)
        + _lifetime_brok_tax(baseline)
        + _lifetime_aca(baseline)
        + _lifetime_niit(baseline)
    )

    summaries: list[ScenarioSummary] = []
    for s in scenarios:
        total_conv = _total_conv(s)
        lifetime_tax = _lifetime_tax(s)
        lifetime_irmaa = _lifetime_irmaa(s)
        lifetime_brok = _lifetime_brok_tax(s)
        lifetime_aca = _lifetime_aca(s)
        lifetime_niit = _lifetime_niit(s)
        all_in_cost = lifetime_tax + lifetime_irmaa + lifetime_brok + lifetime_aca + lifetime_niit

        summaries.append(
            ScenarioSummary(
                name=s.name,
                total_conv=total_conv,
                conv_tax=s.total_conv_tax,
                avg_rate=s.total_conv_tax / max(total_conv, 1),
                lifetime_tax=lifetime_tax,
                lifetime_irmaa=lifetime_irmaa,
                lifetime_brok_tax=lifetime_brok,
                lifetime_aca_loss=lifetime_aca,
                lifetime_niit=lifetime_niit,
                all_in_cost=all_in_cost,
                savings_vs_baseline=baseline_all_in - all_in_cost,
                ira_at_75=_ira_at_age(s, 75),
                ira_at_85=_ira_at_age(s, 85),
                ira_at_95=_ira_at_age(s, 95),
            )
        )
    return summaries


@dataclass
class MilestoneRow:
    """Per-scenario per-age milestone snapshot."""

    scenario_name: str
    age: int
    ira_balance: (
        float  # IRA + Roth combined (your_ira_begin + spouse_ira_begin + roth begins); grid-01 fix
    )
    total_rmd: float  # your_rmd + spouse_rmd
    marginal_bracket: float  # raw fraction (e.g. 0.22), view multiplies by 100


@dataclass
class ConversionRow:
    """One year of conversion detail for a scenario with conversions."""

    year: int
    your_age: int
    spouse_age: int
    your_conv: float
    spouse_conv: float
    conv_tax: float
    irmaa_cost: float
    bracket: float  # raw fraction (e.g. 0.22), view multiplies by 100


def compute_milestone_rows(
    scenarios: list[ScenarioResult],
    *,
    milestone_ages: tuple[int, ...] = (70, 75, 80, 85, 90, 95),
) -> list[MilestoneRow]:
    """Return one MilestoneRow per (scenario, age) pair.

    Only includes rows where a matching year is found in the scenario.
    View is responsible for building the display dict and handling missing ages.
    """
    rows: list[MilestoneRow] = []
    for age in milestone_ages:
        for s in scenarios:
            yr = next((y for y in s.years if y.your_age == age), None)
            if yr is not None:
                rows.append(
                    MilestoneRow(
                        scenario_name=s.name,
                        age=age,
                        ira_balance=yr.your_ira_begin
                        + yr.spouse_ira_begin
                        + yr.your_roth_begin
                        + yr.spouse_roth_begin,
                        total_rmd=yr.your_rmd + yr.spouse_rmd,
                        marginal_bracket=yr.marginal_bracket,
                    )
                )
    return rows


def compute_conversion_rows(scenario: ScenarioResult) -> list[ConversionRow]:
    """Year-by-year conversion detail. Only includes years with conversion > 0."""
    rows: list[ConversionRow] = []
    for yr in scenario.years:
        if yr.your_conversion > 0 or yr.spouse_conversion > 0:
            rows.append(
                ConversionRow(
                    year=yr.year,
                    your_age=yr.your_age,
                    spouse_age=yr.spouse_age,
                    your_conv=yr.your_conversion,
                    spouse_conv=yr.spouse_conversion,
                    conv_tax=yr.conversion_tax,
                    irmaa_cost=yr.irmaa_cost,
                    bracket=yr.marginal_bracket,
                )
            )
    return rows


def compute_cumulative_net_benefit(
    scenario: ScenarioResult,
    baseline: ScenarioResult,
    *,
    rmd_start_age: int,
) -> list[float]:
    """For each year in scenario.years, compute cumulative all-in net benefit.

    cum_benefit[i] = cumulative (baseline_all_in_cost − scenario_all_in_cost) up to year i

    All-in cost per year = federal_tax_amt + irmaa_cost + brokerage_gain_tax
                         + aca_loss + niit_cost  (matching Scenario Comparator)

    Convention: positive = saves money vs baseline (same sign as
    ``compute_summary_rows(...).savings_vs_baseline``).

    During conversion years scenario pays MORE federal tax, so the running sum
    starts negative and climbs into positive territory as RMD-phase savings
    accumulate — the crossover is the break-even age shown on the chart.
    The final element of the returned list equals
    ``compute_summary_rows([baseline, scenario], baseline)[1].savings_vs_baseline``
    within floating-point precision (audit 0705 #views-financial-10).

    ``rmd_start_age`` is retained in the signature for API compatibility; it no
    longer restricts which years contribute because conversion-year extra taxes
    are already captured in the per-year federal_tax_amt delta (the running sum
    naturally goes negative during conversion years and crosses zero at
    break-even without any explicit sunk-cost deduction).
    """
    cum_benefit: list[float] = []
    cum_all_in_saved = 0.0

    for yr_b, yr_s in zip(baseline.years, scenario.years, strict=False):
        baseline_year_cost = (
            yr_b.federal_tax_amt
            + yr_b.irmaa_cost
            + yr_b.brokerage_gain_tax
            + yr_b.aca_loss
            + yr_b.niit_cost
        )
        scenario_year_cost = (
            yr_s.federal_tax_amt
            + yr_s.irmaa_cost
            + yr_s.brokerage_gain_tax
            + yr_s.aca_loss
            + yr_s.niit_cost
        )
        cum_all_in_saved += baseline_year_cost - scenario_year_cost
        cum_benefit.append(cum_all_in_saved)

    return cum_benefit
