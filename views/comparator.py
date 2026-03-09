"""Scenario Comparator — run multiple strategies side-by-side.

Compares up to 4 scenarios across key metrics:
- IRA trajectory, RMD size, tax burden, IRMAA exposure
- Lifetime net benefit analysis
- Year-by-year exportable detail
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.scenario import (
    ConversionPlan,
    ScenarioResult,
    add_bracket_fill_withdrawals,
    auto_fill_12,
    auto_fill_22,
    auto_fill_irmaa_safe,
    run_no_conversion,
    run_scenario,
)
from models.household import Household

COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#f59e0b", "#8b5cf6"]
SCENARIO_PRESETS = {
    "No Conversion": "no_conv",
    "Fill to 12%": "fill_12",
    "Fill 12% + Bracket Fill": "fill_12_bf",
    "Fill to 22%": "fill_22",
    "IRMAA-Safe Max": "irmaa_safe",
    "Custom (from Planner)": "custom",
}


def _build_scenario(hh: Household, key: str) -> ScenarioResult:
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
        plan = ConversionPlan(
            your_conversions=dict(st.session_state.get("conv_plan_your", {})),
            spouse_conversions=dict(st.session_state.get("conv_plan_spouse", {})),
            qcds=dict(st.session_state.get("conv_plan_qcd", {})),
        )
        return run_scenario(hh, plan, "Custom Plan", end_age=95)
    return run_no_conversion(hh, end_age=95)


def render(hh: Household):
    st.title("⚖️ Scenario Comparator")
    st.caption("Compare conversion strategies side-by-side to find the best approach.")

    # --- Scenario selection ---
    st.markdown("### Select Scenarios to Compare")

    preset_names = list(SCENARIO_PRESETS.keys())
    default_selected = ["No Conversion", "Fill to 12%", "Fill 12% + Bracket Fill"]

    selected = st.multiselect(
        "Choose up to 5 strategies",
        preset_names,
        default=default_selected,
        max_selections=5,
    )

    if len(selected) < 2:
        st.info("Select at least 2 scenarios to compare.")
        return

    # --- Run scenarios ---
    scenarios: list[ScenarioResult] = []
    for name in selected:
        key = SCENARIO_PRESETS[name]
        result = _build_scenario(hh, key)
        result.name = name  # override name for display
        scenarios.append(result)

    # --- Summary metrics ---
    st.markdown("### Summary Comparison")

    def _total_conv(s: ScenarioResult) -> float:
        return s.total_your_conv + s.total_spouse_conv

    def _lifetime_tax(s: ScenarioResult) -> float:
        return sum(yr.federal_tax_amt for yr in s.years)

    def _lifetime_irmaa(s: ScenarioResult) -> float:
        return sum(yr.irmaa_cost for yr in s.years)

    def _lifetime_brok_tax(s: ScenarioResult) -> float:
        return sum(yr.brokerage_gain_tax for yr in s.years)

    def _ira_at_age(s: ScenarioResult, age: int) -> float:
        yr = next((y for y in s.years if y.your_age == age), None)
        return (yr.your_ira_begin + yr.spouse_ira_begin) if yr else 0

    # Build summary table
    summary_rows = []
    baseline = scenarios[0]  # first scenario is baseline for delta
    for s in scenarios:
        total_conv = _total_conv(s)
        lifetime_tax = _lifetime_tax(s)
        lifetime_irmaa = _lifetime_irmaa(s)
        lifetime_brok = _lifetime_brok_tax(s)
        total_cost = lifetime_tax + lifetime_irmaa + lifetime_brok

        summary_rows.append({
            "Scenario": s.name,
            "Total Converted": f"${total_conv:,.0f}",
            "Conv Tax Paid": f"${s.total_conv_tax:,.0f}",
            "Avg Conv Rate": f"{s.total_conv_tax / max(total_conv, 1) * 100:.1f}%",
            "Lifetime Tax": f"${lifetime_tax:,.0f}",
            "Lifetime IRMAA": f"${lifetime_irmaa:,.0f}",
            "Lifetime Brok Tax": f"${lifetime_brok:,.0f}",
            "Total All-In Cost": f"${total_cost:,.0f}",
            "vs Baseline": f"${total_cost - (_lifetime_tax(baseline) + _lifetime_irmaa(baseline) + _lifetime_brok_tax(baseline)):+,.0f}",
            "IRA at 75": f"${_ira_at_age(s, 75) / 1e6:.2f}M",
            "IRA at 85": f"${_ira_at_age(s, 85) / 1e6:.2f}M",
            "IRA at 95": f"${_ira_at_age(s, 95) / 1e6:.2f}M",
        })

    df_summary = pd.DataFrame(summary_rows)
    st.dataframe(df_summary, hide_index=True, width="stretch")

    st.markdown("---")

    # --- Chart 1: IRA Trajectory ---
    st.markdown("### IRA Balance Trajectory")

    fig_ira = go.Figure()
    ages = [yr.your_age for yr in scenarios[0].years]

    for i, s in enumerate(scenarios):
        ira_vals = [yr.your_ira_begin + yr.spouse_ira_begin for yr in s.years]
        fig_ira.add_trace(go.Scatter(
            x=ages, y=ira_vals,
            name=s.name,
            line={"color": COLORS[i % len(COLORS)], "width": 2 + (1 if i == 0 else 0)},
            hovertemplate=f"{s.name}<br>Age %{{x}}: $%{{y:,.0f}}<extra></extra>",
        ))

    fig_ira.add_vline(x=75, line_dash="dot", line_color="gray", annotation_text="RMDs begin")
    fig_ira.update_layout(
        xaxis_title="Your Age",
        yaxis_title="Combined IRA ($)",
        yaxis_tickformat="$,.0s",
        height=450,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig_ira, width="stretch")

    # --- Chart 2: Annual Tax Comparison ---
    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown("### Annual Federal Tax")
        fig_tax = go.Figure()
        for i, s in enumerate(scenarios):
            fig_tax.add_trace(go.Scatter(
                x=ages,
                y=[yr.federal_tax_amt for yr in s.years],
                name=s.name,
                line={"color": COLORS[i % len(COLORS)]},
                hovertemplate=f"{s.name}<br>Age %{{x}}: $%{{y:,.0f}}<extra></extra>",
            ))
        fig_tax.update_layout(
            xaxis_title="Your Age",
            yaxis_title="Federal Tax ($)",
            yaxis_tickformat="$,.0s",
            height=350,
        )
        st.plotly_chart(fig_tax, width="stretch")

    with col_r:
        st.markdown("### Marginal Bracket")
        fig_br = go.Figure()
        for i, s in enumerate(scenarios):
            fig_br.add_trace(go.Scatter(
                x=ages,
                y=[yr.marginal_bracket * 100 for yr in s.years],
                name=s.name,
                line={"color": COLORS[i % len(COLORS)]},
                mode="lines+markers",
                marker={"size": 3},
                hovertemplate=f"{s.name}<br>Age %{{x}}: %{{y:.0f}}%<extra></extra>",
            ))
        fig_br.update_layout(
            xaxis_title="Your Age",
            yaxis_title="Marginal Bracket (%)",
            yaxis={"dtick": 2},
            height=350,
        )
        st.plotly_chart(fig_br, width="stretch")

    # --- Chart 3: Cumulative Net Benefit ---
    st.markdown("### Cumulative Net Benefit vs No-Conversion Baseline")
    st.caption(
        "Positive = this strategy has saved money vs doing nothing. "
        "Accounts for conversion tax paid, RMD tax saved, and brokerage tax saved."
    )

    # Find the no-conversion scenario (or use first as baseline)
    baseline_idx = next((i for i, s in enumerate(scenarios) if "No Conv" in s.name), 0)
    baseline_s = scenarios[baseline_idx]

    fig_net = go.Figure()
    for i, s in enumerate(scenarios):
        if i == baseline_idx:
            continue  # skip baseline vs itself

        cum_benefit = []
        cum_conv_tax = s.total_conv_tax  # sunk cost
        cum_rmd_saved = 0.0
        cum_brok_saved = 0.0

        for yr_b, yr_s in zip(baseline_s.years, s.years, strict=False):
            if yr_b.your_age >= 75:
                cum_rmd_saved += yr_b.federal_tax_amt - yr_s.federal_tax_amt
            cum_brok_saved += yr_b.brokerage_gain_tax - yr_s.brokerage_gain_tax
            cum_benefit.append(cum_rmd_saved + cum_brok_saved - cum_conv_tax)

        fig_net.add_trace(go.Scatter(
            x=ages, y=cum_benefit,
            name=s.name,
            line={"color": COLORS[i % len(COLORS)], "width": 2},
            fill="tozeroy" if len(scenarios) <= 3 else None,
            hovertemplate=f"{s.name}<br>Age %{{x}}: $%{{y:,.0f}}<extra></extra>",
        ))

    fig_net.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_net.add_vline(x=75, line_dash="dot", line_color="gray", annotation_text="RMDs begin")
    fig_net.update_layout(
        xaxis_title="Your Age",
        yaxis_title="Net Benefit vs No Conversion ($)",
        yaxis_tickformat="$,.0s",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
    )
    st.plotly_chart(fig_net, width="stretch")

    # --- Chart 4: RMD Comparison ---
    st.markdown("### RMD Size Comparison (Ages 75+)")

    fig_rmd = go.Figure()
    rmd_ages = [a for a in ages if a >= 75]

    for i, s in enumerate(scenarios):
        rmd_vals = [yr.your_rmd for yr in s.years if yr.your_age >= 75]
        fig_rmd.add_trace(go.Bar(
            x=rmd_ages, y=rmd_vals,
            name=s.name,
            marker_color=COLORS[i % len(COLORS)],
            opacity=0.7,
            hovertemplate=f"{s.name}<br>Age %{{x}}: $%{{y:,.0f}}<extra></extra>",
        ))

    fig_rmd.update_layout(
        barmode="group",
        xaxis_title="Your Age",
        yaxis_title="Annual RMD ($)",
        yaxis_tickformat="$,.0s",
        height=400,
    )
    st.plotly_chart(fig_rmd, width="stretch")

    # --- Milestone comparison table ---
    st.markdown("### Key Age Milestones")

    milestone_ages = [70, 75, 80, 85, 90, 95]
    milestone_rows = []
    for age in milestone_ages:
        row = {"Age": str(age), "Sp Age": str(age - hh.age_gap)}
        for s in scenarios:
            yr = next((y for y in s.years if y.your_age == age), None)
            if yr:
                ira = yr.your_ira_begin + yr.spouse_ira_begin
                row[f"{s.name} IRA"] = f"${ira / 1e6:.2f}M"
                row[f"{s.name} RMD"] = f"${yr.your_rmd:,.0f}" if yr.your_rmd > 0 else "---"
                row[f"{s.name} Bracket"] = f"{yr.marginal_bracket * 100:.0f}%"
            else:
                row[f"{s.name} IRA"] = "---"
                row[f"{s.name} RMD"] = "---"
                row[f"{s.name} Bracket"] = "---"
        milestone_rows.append(row)

    st.dataframe(pd.DataFrame(milestone_rows), hide_index=True, width="stretch")

    # --- Conversion detail per scenario ---
    with st.expander("📋 Conversion Detail by Scenario"):
        for s in scenarios:
            conv_total = _total_conv(s)
            if conv_total == 0:
                continue
            st.markdown(f"#### {s.name}")
            conv_rows = []
            for yr in s.years:
                if yr.your_conversion > 0 or yr.spouse_conversion > 0:
                    conv_rows.append({
                        "Year": str(yr.year),
                        "You/Sp": f"{yr.your_age}/{yr.spouse_age}",
                        "Your Conv": f"${yr.your_conversion:,.0f}",
                        "Sp Conv": f"${yr.spouse_conversion:,.0f}",
                        "Bracket": f"{yr.marginal_bracket * 100:.0f}%",
                        "Conv Tax": f"${yr.conversion_tax:,.0f}",
                        "IRMAA": f"${yr.irmaa_cost:,.0f}",
                    })
            if conv_rows:
                st.dataframe(pd.DataFrame(conv_rows), hide_index=True, width="stretch")

    # --- Surviving Spouse Analysis ---
    st.markdown("---")
    st.markdown("### Surviving Spouse Analysis")
    st.caption(
        "What happens if you die early? Your spouse inherits your IRA (rolls into hers), "
        "files Single (tighter brackets), and keeps the higher of two SS benefits."
    )

    from engine.ira import calc_rmd  # noqa: E402
    from engine.tax import (  # noqa: E402
        SENIOR_EXTRA_SINGLE,
        STD_DEDUCTION_SINGLE,
        federal_tax_single,
        marginal_rate_single,
        taxable_ss,
    )

    death_ages = [70, 75, 80, 85]

    # For each scenario, compute survivor impact at each death age
    survivor_rows = []
    for death_age in death_ages:
        row = {"Your Death Age": str(death_age), "Spouse Age": str(death_age - hh.age_gap)}

        for s in scenarios:
            # Find the year of death
            yr_death = next((y for y in s.years if y.your_age == death_age), None)
            if not yr_death:
                row[f"{s.name} Inherited IRA"] = "---"
                row[f"{s.name} Survivor Tax"] = "---"
                row[f"{s.name} Bracket"] = "---"
                continue

            # Spouse inherits both IRAs
            inherited_ira = yr_death.your_ira_begin + yr_death.spouse_ira_begin

            # Survivor gets higher of two SS (with COLA applied)
            survivor_ss = max(yr_death.your_ss, yr_death.spouse_ss)

            # Project 5 years out — what does year death+5 look like for survivor?
            proj_years = 5
            survivor_age = (death_age - hh.age_gap) + proj_years
            # Use spouse's growth rate since she inherits into her IRA
            death_year = hh.base_year + (death_age - hh.your_age)
            surv_rate = hh.spouse_ira_rate(death_year + proj_years)
            ira_grown = inherited_ira * (1 + surv_rate) ** proj_years

            # RMD on combined IRA (survivor's RMD age)
            rmd = calc_rmd(ira_grown, survivor_age, hh.rmd_start_age)

            # SS with COLA
            from engine.ira import ss_with_cola
            ss_at_proj = ss_with_cola(survivor_ss, proj_years, hh.ss_cola) if survivor_ss > 0 else 0

            # Single filer tax
            other_inc = rmd
            tss = taxable_ss(ss_at_proj, other_inc)  # SS taxation uses Single thresholds too but formula is same
            gross = rmd + tss
            ded = STD_DEDUCTION_SINGLE + (SENIOR_EXTRA_SINGLE if survivor_age >= 65 else 0)
            taxable = max(gross - ded, 0)
            tax = federal_tax_single(taxable)
            bracket = marginal_rate_single(taxable)

            row[f"{s.name} Inherited IRA"] = f"${inherited_ira / 1e6:.2f}M"
            row[f"{s.name} Survivor Tax"] = f"${tax:,.0f}/yr"
            row[f"{s.name} Bracket"] = f"{bracket * 100:.0f}%"

        survivor_rows.append(row)

    st.dataframe(pd.DataFrame(survivor_rows), hide_index=True, width="stretch")

    st.markdown("""
**Why this matters**: When you die, your spouse:
- Files **Single** — 12% bracket tops at $50K taxable (vs $101K for MFJ)
- Inherits your IRA — combined with hers, RMDs are massive
- Gets only the **higher** of two SS benefits (not both)
- Result: unconverted IRAs create an even worse squeeze for the survivor

**Inheritance for non-spouse**: IRA/Roth can go to anyone. Non-spouse beneficiaries must
empty inherited accounts within **10 years** (SECURE Act). Inherited Roth is tax-free;
inherited traditional IRA is fully taxable — making pre-death Roth conversion especially
valuable if you plan to leave assets to non-family.
""")

    # --- Strategy guidance ---
    st.markdown("---")
    st.markdown("### Strategy Guide")
    st.markdown("""
- **No Conversion**: Baseline — lets IRA grow tax-deferred, faces full RMD squeeze
- **Fill to 12%**: Conservative — converts only within the lowest useful bracket, minimizes current tax
- **Fill 12% + Bracket Fill**: Same as Fill 12%, plus voluntary excess withdrawals post-75 to fill the 22% bracket. Depletes IRA faster to reduce future RMD pressure. After-tax proceeds go to brokerage (not Roth).
- **Fill to 22%**: Aggressive — converts more now at 22%, but dramatically reduces future RMDs
- **IRMAA-Safe Max**: Balanced — converts as much as possible without triggering Medicare surcharges
- **Custom**: Your plan from the Conversion Planner page

**Key insight**: Compare the "vs Baseline" column in the summary. A negative number means that
strategy costs *less* over your lifetime than doing nothing — even after paying conversion tax now.
""")
