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
from engine.tax import (
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
    federal_tax_single,
    marginal_rate_single,
    senior_bonus_deduction,
    taxable_ss,
)
from models.household import Household, SurvivorScenario


def survivor_year_tax(
    survivor_age: int,
    rmd: float,
    survivor_ss: float,
    *,
    year: int,
    cpi: float,
) -> tuple[float, float, float]:
    """Tax, marginal rate, and taxable income for a Single survivor in a future year."""
    # Index the standard deduction and brackets to the projection year (via cpi) so
    # inflation-grown income is taxed against indexed thresholds, matching the main
    # engine (scenario.py) rather than raw BASE_YEAR (2026) constants.
    tss = taxable_ss(survivor_ss, rmd, filing_status="Single")
    gross = rmd + tss
    ded = deductions(survivor_age, 0, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, year=year, cpi=cpi)
    ded += senior_bonus_deduction(
        survivor_age, 0, gross, year=year, cpi=cpi, filing_status="Single"
    )
    taxable = max(gross - ded, 0.0)
    return (
        federal_tax_single(taxable, year=year, cpi=cpi),
        marginal_rate_single(taxable, year=year, cpi=cpi),
        taxable,
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
            for proj_offset in range(proj_years):
                year_offset = proj_offset + 1
                age_at_offset = _surv_age(death_age, year_offset)
                year_at_offset = death_year_calc + year_offset
                rmd_withdrawal = calc_rmd(ira_balance, age_at_offset, survivor_rmd_start)
                ira_balance = max(ira_balance - rmd_withdrawal, 0.0)
                ira_balance *= 1 + _surv_rate(year_at_offset)
            ira_grown = ira_balance

            # FIX: use the surviving spouse's own rmd_start_age
            rmd = calc_rmd(ira_grown, survivor_age, survivor_rmd_start)

            ss_grown = ss_with_cola(survivor_ss, proj_years, hh.ss_cola) if survivor_ss > 0 else 0.0

            # Single survivor, indexed to the projection year so inflation-grown
            # income is taxed against indexed brackets + deduction (not raw 2026).
            tax, bracket, _taxable = survivor_year_tax(
                survivor_age,
                rmd,
                ss_grown,
                year=death_year_calc + proj_years,
                cpi=hh.cpi_assumption,
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
    all_in_cost: float  # lifetime_tax + lifetime_irmaa + lifetime_brok_tax
    vs_baseline: float  # this.all_in_cost - baseline.all_in_cost (positive = worse)
    ira_at_75: float  # IRA + Roth combined at your_age == 75 (grid-01 fix: includes roth begins)
    ira_at_85: float
    ira_at_95: float


def compute_summary_rows(
    scenarios: list[ScenarioResult],
    baseline: ScenarioResult,
) -> list[ScenarioSummary]:
    """Aggregate per-scenario summary metrics.

    Baseline is the first scenario (no-conversion run).
    vs_baseline = this.all_in_cost - baseline.all_in_cost
    (positive = costs more than baseline; negative = saves money vs baseline).
    """

    def _total_conv(s: ScenarioResult) -> float:
        return s.total_your_conv + s.total_spouse_conv

    def _lifetime_tax(s: ScenarioResult) -> float:
        return sum(yr.federal_tax_amt for yr in s.years)

    def _lifetime_irmaa(s: ScenarioResult) -> float:
        return sum(yr.irmaa_cost for yr in s.years)

    def _lifetime_brok_tax(s: ScenarioResult) -> float:
        return sum(yr.brokerage_gain_tax for yr in s.years)

    def _ira_at_age(s: ScenarioResult, age: int) -> float:
        # Value includes Roth balances so converted principal is not invisible (grid-01).
        yr = next((y for y in s.years if y.your_age == age), None)
        return (
            (yr.your_ira_begin + yr.spouse_ira_begin + yr.your_roth_begin + yr.spouse_roth_begin)
            if yr
            else 0.0
        )

    baseline_all_in = (
        _lifetime_tax(baseline) + _lifetime_irmaa(baseline) + _lifetime_brok_tax(baseline)
    )

    summaries: list[ScenarioSummary] = []
    for s in scenarios:
        total_conv = _total_conv(s)
        lifetime_tax = _lifetime_tax(s)
        lifetime_irmaa = _lifetime_irmaa(s)
        lifetime_brok = _lifetime_brok_tax(s)
        all_in_cost = lifetime_tax + lifetime_irmaa + lifetime_brok

        summaries.append(
            ScenarioSummary(
                name=s.name,
                total_conv=total_conv,
                conv_tax=s.total_conv_tax,
                avg_rate=s.total_conv_tax / max(total_conv, 1),
                lifetime_tax=lifetime_tax,
                lifetime_irmaa=lifetime_irmaa,
                lifetime_brok_tax=lifetime_brok,
                all_in_cost=all_in_cost,
                vs_baseline=all_in_cost - baseline_all_in,
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
    """For each year in scenario.years, compute cumulative net benefit.

    cum_benefit[i] = (sum of RMD tax savings from rmd_start_age onward)
                   + (sum of brokerage tax savings)
                   - scenario.total_conv_tax (sunk cost, constant)

    RMD savings = baseline.federal_tax_amt - scenario.federal_tax_amt (only when your_age >= rmd_start_age).
    Brokerage savings = baseline.brokerage_gain_tax - scenario.brokerage_gain_tax (every year).
    """
    cum_benefit: list[float] = []
    cum_conv_tax = scenario.total_conv_tax  # sunk cost
    cum_rmd_saved = 0.0
    cum_brok_saved = 0.0

    for yr_b, yr_s in zip(baseline.years, scenario.years, strict=False):
        if yr_b.your_age >= rmd_start_age:
            cum_rmd_saved += yr_b.federal_tax_amt - yr_s.federal_tax_amt
        cum_brok_saved += yr_b.brokerage_gain_tax - yr_s.brokerage_gain_tax
        cum_benefit.append(cum_rmd_saved + cum_brok_saved - cum_conv_tax)

    return cum_benefit
