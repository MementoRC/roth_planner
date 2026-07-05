"""Scenario Comparator — run multiple strategies side-by-side.

Compares up to 4 scenarios across key metrics:
- IRA trajectory, RMD size, tax burden, IRMAA exposure
- Lifetime net benefit analysis
- Year-by-year exportable detail
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.scenario import ScenarioResult
from engine.scenario_compare import (
    build_scenario,
    compute_conversion_rows,
    compute_cumulative_net_benefit,
    compute_milestone_rows,
    compute_summary_rows,
    compute_survivor_snapshot,
    survivor_death_ages,
)
from engine.tax import BRACKETS_MFJ, BRACKETS_SINGLE
from models.household import Household
from views._format import FORM_8606_CAPTION, fmt_dollars, fmt_dollars_short, fmt_pct

COLORS = ["#ef4444", "#3b82f6", "#22c55e", "#f59e0b", "#8b5cf6"]
SCENARIO_PRESETS = {
    "No Conversion": "no_conv",
    "Fill to 12%": "fill_12",
    "Fill 12% + Bracket Fill": "fill_12_bf",
    "Fill to 22%": "fill_22",
    "IRMAA-Safe Max": "irmaa_safe",
    "Custom (from Planner)": "custom",
}


def render(hh: Household):
    st.title("⚖️ Scenario Comparator")
    st.caption("Compare conversion strategies side-by-side to find the best approach.")
    st.caption(FORM_8606_CAPTION)

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
        result = build_scenario(hh, key)
        result.name = name  # override name for display
        scenarios.append(result)

    # --- Summary metrics ---
    st.markdown("### Summary Comparison")

    baseline = scenarios[0]  # first scenario is baseline for delta
    summaries = compute_summary_rows(scenarios, baseline)

    # Format raw ScenarioSummary values into display dicts
    summary_rows = [
        {
            "Scenario": s.name,
            "Total Converted": fmt_dollars(s.total_conv),
            "Conv Tax Paid": fmt_dollars(s.conv_tax),
            "Avg Conv Rate": fmt_pct(s.avg_rate),
            "Lifetime Tax": fmt_dollars(s.lifetime_tax),
            "Lifetime IRMAA": fmt_dollars(s.lifetime_irmaa),
            "Lifetime Brok Tax": fmt_dollars(s.lifetime_brok_tax),
            "Total All-In Cost": fmt_dollars(s.all_in_cost),
            "Lifetime Savings vs No Conversion": fmt_dollars(s.savings_vs_baseline, sign=True),
            "IRA+Roth at 75": fmt_dollars_short(s.ira_at_75, decimals=2),
            "IRA+Roth at 85": fmt_dollars_short(s.ira_at_85, decimals=2),
            "IRA+Roth at 95": fmt_dollars_short(s.ira_at_95, decimals=2),
        }
        for s in summaries
    ]

    df_summary = pd.DataFrame(summary_rows)
    st.dataframe(df_summary, hide_index=True, width="stretch")

    st.markdown("---")

    # --- Chart 1: IRA + Roth Trajectory ---
    st.markdown("### IRA + Roth Balance Trajectory")

    fig_ira = go.Figure()
    ages = [yr.your_age for yr in scenarios[0].years]

    for i, s in enumerate(scenarios):
        ira_vals = [
            yr.your_ira_begin + yr.spouse_ira_begin + yr.your_roth_begin + yr.spouse_roth_begin
            for yr in s.years
        ]
        fig_ira.add_trace(
            go.Scatter(
                x=ages,
                y=ira_vals,
                name=s.name,
                line={"color": COLORS[i % len(COLORS)], "width": 2 + (1 if i == 0 else 0)},
                hovertemplate=f"{s.name}<br>Age %{{x}}: $%{{y:,.0f}}<extra></extra>",
            )
        )

    fig_ira.add_vline(
        x=hh.your_rmd_start_age, line_dash="dot", line_color="gray", annotation_text="RMDs begin"
    )
    fig_ira.update_layout(
        xaxis_title="Your Age",
        yaxis_title="IRA + Roth Balance ($)",
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
            fig_tax.add_trace(
                go.Scatter(
                    x=ages,
                    y=[yr.federal_tax_amt for yr in s.years],
                    name=s.name,
                    line={"color": COLORS[i % len(COLORS)]},
                    hovertemplate=f"{s.name}<br>Age %{{x}}: $%{{y:,.0f}}<extra></extra>",
                )
            )
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
            fig_br.add_trace(
                go.Scatter(
                    x=ages,
                    y=[yr.marginal_bracket * 100 for yr in s.years],
                    name=s.name,
                    line={"color": COLORS[i % len(COLORS)]},
                    mode="lines+markers",
                    marker={"size": 3},
                    hovertemplate=f"{s.name}<br>Age %{{x}}: %{{y:.0f}}%<extra></extra>",
                )
            )
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

        cum_benefit = compute_cumulative_net_benefit(
            s, baseline_s, rmd_start_age=hh.your_rmd_start_age
        )

        fig_net.add_trace(
            go.Scatter(
                x=ages,
                y=cum_benefit,
                name=s.name,
                line={"color": COLORS[i % len(COLORS)], "width": 2},
                fill="tozeroy" if len(scenarios) <= 3 else None,
                hovertemplate=f"{s.name}<br>Age %{{x}}: $%{{y:,.0f}}<extra></extra>",
            )
        )

    fig_net.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_net.add_vline(
        x=hh.your_rmd_start_age, line_dash="dot", line_color="gray", annotation_text="RMDs begin"
    )
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
    rmd_ages = [a for a in ages if a >= hh.your_rmd_start_age]

    for i, s in enumerate(scenarios):
        rmd_vals = [
            yr.your_rmd + yr.spouse_rmd for yr in s.years if yr.your_age >= hh.your_rmd_start_age
        ]
        fig_rmd.add_trace(
            go.Bar(
                x=rmd_ages,
                y=rmd_vals,
                name=s.name,
                marker_color=COLORS[i % len(COLORS)],
                opacity=0.7,
                hovertemplate=f"{s.name}<br>Age %{{x}}: $%{{y:,.0f}}<extra></extra>",
            )
        )

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

    # Collect raw milestone data and build display dicts (view-layer formatting)
    raw_milestones = compute_milestone_rows(scenarios)
    # Group by age to reconstruct the per-age row with per-scenario columns
    milestone_ages_list = [70, 75, 80, 85, 90, 95]
    # Build lookup: (scenario_name, age) -> MilestoneRow
    _ms_lookup = {(m.scenario_name, m.age): m for m in raw_milestones}
    is_mfj = hh.filing_status == "MFJ"
    milestone_rows = []
    for age in milestone_ages_list:
        row: dict[str, str] = {"Age": str(age)}
        if is_mfj:
            row["Sp Age"] = str(age - hh.age_gap)
        for s in scenarios:
            m = _ms_lookup.get((s.name, age))
            if m is not None:
                row[f"{s.name} IRA+Roth"] = fmt_dollars_short(m.ira_balance, decimals=2)
                row[f"{s.name} RMD"] = fmt_dollars(m.total_rmd) if m.total_rmd > 0 else "---"
                row[f"{s.name} Bracket"] = fmt_pct(m.marginal_bracket, 0)
            else:
                row[f"{s.name} IRA+Roth"] = "---"
                row[f"{s.name} RMD"] = "---"
                row[f"{s.name} Bracket"] = "---"
        milestone_rows.append(row)

    st.dataframe(pd.DataFrame(milestone_rows), hide_index=True, width="stretch")

    # --- Conversion detail per scenario ---
    with st.expander("📋 Conversion Detail by Scenario"):
        for s in scenarios:
            raw_conv_rows = compute_conversion_rows(s)
            if not raw_conv_rows:
                continue
            st.markdown(f"#### {s.name}")
            conv_rows = [
                {
                    "Year": str(r.year),
                    **(
                        {"You/Sp": f"{r.your_age}/{r.spouse_age}"}
                        if is_mfj
                        else {"Age": str(r.your_age)}
                    ),
                    "Your Conv": fmt_dollars(r.your_conv),
                    **({"Sp Conv": fmt_dollars(r.spouse_conv)} if is_mfj else {}),
                    "Bracket": fmt_pct(r.bracket, 0),
                    "Conv Tax": fmt_dollars(r.conv_tax),
                    "IRMAA": fmt_dollars(r.irmaa_cost),
                }
                for r in raw_conv_rows
            ]
            st.dataframe(pd.DataFrame(conv_rows), hide_index=True, width="stretch")

    # --- Surviving Spouse Analysis (MFJ only) ---
    if is_mfj:
        st.markdown("---")
        st.markdown("### Surviving Spouse Analysis")
        st.caption(
            "What happens if one spouse dies early? The survivor inherits both IRAs, "
            "files Single (tighter brackets), and keeps the higher of two SS benefits."
        )

        who_dies, death_ages = survivor_death_ages(hh)

        # Scenario source caption
        surv = hh.survivor
        if surv is not None:
            base_age = hh.your_age if who_dies == "you" else hh.spouse_age
            death_age_display = base_age + (surv.death_year - hh.base_year)
            st.caption(
                f"Modeling: **{who_dies}** dies in **{surv.death_year}** (age {death_age_display})."
            )
        else:
            default_death_age = hh.your_age + 5
            st.caption(
                f"Default snapshot (you die at age {default_death_age}) — "
                "set a Survivor scenario in **⚙️ Setup → 📊 Parameters → Joint** to model a specific case."
            )

        survivor_rows = compute_survivor_snapshot(hh, scenarios, who_dies, death_ages)

        st.dataframe(pd.DataFrame(survivor_rows), hide_index=True, width="stretch")

        _single_12 = fmt_dollars(BRACKETS_SINGLE[1][0])
        _mfj_12 = fmt_dollars(BRACKETS_MFJ[1][0])
        st.markdown(f"""
**Why this matters**: When one spouse dies, the survivor:
- Files **Single** — 12% bracket tops at {_single_12} taxable (vs {_mfj_12} for MFJ) — 2026 base; both are CPI-indexed in the projection
- Inherits the deceased's IRA — combined with their own, RMDs are massive
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

**Key insight**: Compare the "Lifetime Savings vs No Conversion" column in the summary. A positive number means that
strategy costs *less* over your lifetime than doing nothing — even after paying conversion tax now.
""")
