"""Asset Location Optimizer — equity-first vs proportional vs bond-first conversion.

Shows how converting high-growth assets first into Roth maximizes tax-free
compounding and reduces future RMD pressure on the remaining (slower-growing) IRA.
"""

import plotly.graph_objects as go
import streamlit as st

from engine.asset_location import project_asset_location
from engine.scenario import auto_fill_12
from models.household import Household
from views._format import fmt_dollars, fmt_pct

STRATEGIES = {
    "equity_first": ("Equity First", "#22c55e"),
    "proportional": ("Proportional", "#3b82f6"),
    "bond_first": ("Bond First", "#ef4444"),
}


def render(hh: Household):
    st.title("Asset Location Optimizer")
    st.caption(
        "Compare converting high-growth equities first vs proportionally vs bonds first. "
        "Same total conversion amount — different long-term outcomes."
    )

    # --- Controls ---
    c1, c2, c3 = st.columns(3)
    with c1:
        equity_pct = st.slider("IRA Equity %", 20, 80, 60, 5) / 100
    with c2:
        equity_return = st.slider("Equity Return %", 4.0, 12.0, 9.0, 0.5) / 100
    with c3:
        bond_return = st.slider("Bond Return %", 2.0, 6.0, 4.0, 0.5) / 100

    # Use the auto-fill 12% plan as the conversion schedule
    plan = auto_fill_12(hh)
    # Combine your + spouse conversions into a single annual amount
    all_years = set(plan.your_conversions.keys()) | set(plan.spouse_conversions.keys())
    annual_conv = {
        y: plan.your_conversions.get(y, 0) + plan.spouse_conversions.get(y, 0) for y in all_years
    }

    total_planned = sum(annual_conv.values())
    st.metric("Total Planned Conversions (Fill 12%)", fmt_dollars(total_planned))

    # --- Run all three strategies ---
    results = {}
    for strat in STRATEGIES:
        results[strat] = project_asset_location(
            hh, annual_conv, equity_pct, equity_return, bond_return, strat
        )

    # --- Summary table ---
    st.markdown("### Strategy Comparison")

    summary_data = []
    for strat, (label, _) in STRATEGIES.items():
        r = results[strat]
        summary_data.append(
            {
                "Strategy": label,
                "IRA at 75": fmt_dollars(r.ira_at_75),
                "IRA at 85": fmt_dollars(r.ira_at_85),
                "RMD at 75": fmt_dollars(r.rmd_at_75),
                "RMD at 85": fmt_dollars(r.rmd_at_85),
                "IRA Growth at 75": fmt_pct(r.ira_growth_at_75),
            }
        )

    import pandas as pd

    st.dataframe(pd.DataFrame(summary_data), hide_index=True, width="stretch")

    # Key insight callout
    eq_r = results["equity_first"]
    prop_r = results["proportional"]
    ira_diff_85 = prop_r.ira_at_85 - eq_r.ira_at_85
    rmd_diff_85 = prop_r.rmd_at_85 - eq_r.rmd_at_85

    if ira_diff_85 > 0:
        st.success(
            f"**Equity-first saves {fmt_dollars(ira_diff_85)} in IRA at 85** vs proportional, "
            f"reducing RMDs by ~{fmt_dollars(rmd_diff_85)}/yr. "
            f"After converting equities, IRA growth drops to ~{fmt_pct(eq_r.ira_growth_at_75)} "
            f"(mostly bonds) vs {fmt_pct(prop_r.ira_growth_at_75)} proportional."
        )

    # --- IRA Trajectory Chart ---
    st.markdown("### IRA Balance Over Time")
    fig_ira = go.Figure()

    for strat, (label, color) in STRATEGIES.items():
        r = results[strat]
        fig_ira.add_trace(
            go.Scatter(
                x=[y.your_age for y in r.years],
                y=[y.ira_total for y in r.years],
                name=label,
                line={"color": color, "width": 2},
            )
        )

    fig_ira.add_vline(
        x=hh.your_rmd_start_age, line_dash="dot", line_color="gray", annotation_text="RMDs begin"
    )
    fig_ira.update_layout(
        xaxis_title="Your Age",
        yaxis_title="Combined IRA ($)",
        yaxis_tickformat="$,.0s",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig_ira, width="stretch")

    # --- Roth Trajectory Chart ---
    st.markdown("### Roth Balance Over Time")
    fig_roth = go.Figure()

    for strat, (label, color) in STRATEGIES.items():
        r = results[strat]
        fig_roth.add_trace(
            go.Scatter(
                x=[y.your_age for y in r.years],
                y=[y.roth_total for y in r.years],
                name=label,
                line={"color": color, "width": 2},
            )
        )

    fig_roth.update_layout(
        xaxis_title="Your Age",
        yaxis_title="Roth Balance ($)",
        yaxis_tickformat="$,.0s",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig_roth, width="stretch")

    # --- IRA Composition Chart (equity vs bond) ---
    st.markdown("### IRA Composition — Equity First Strategy")

    eq_years = results["equity_first"].years
    fig_comp = go.Figure()
    fig_comp.add_trace(
        go.Bar(
            x=[y.your_age for y in eq_years],
            y=[y.ira_equity for y in eq_years],
            name="Equities",
            marker_color="#22c55e",
        )
    )
    fig_comp.add_trace(
        go.Bar(
            x=[y.your_age for y in eq_years],
            y=[y.ira_bond for y in eq_years],
            name="Bonds",
            marker_color="#60a5fa",
        )
    )

    fig_comp.add_vline(
        x=hh.your_rmd_start_age, line_dash="dot", line_color="gray", annotation_text="RMDs begin"
    )
    fig_comp.update_layout(
        barmode="stack",
        xaxis_title="Your Age",
        yaxis_title="IRA Balance ($)",
        yaxis_tickformat="$,.0s",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig_comp, width="stretch")

    # --- Blended Growth Rate Chart ---
    st.markdown("### IRA Blended Growth Rate")
    st.caption(
        "As equities are converted out, the remaining IRA shifts to bonds with a lower growth rate, "
        "reducing future RMD pressure."
    )

    fig_gr = go.Figure()
    for strat, (label, color) in STRATEGIES.items():
        r = results[strat]
        fig_gr.add_trace(
            go.Scatter(
                x=[y.your_age for y in r.years if y.ira_total > 0],
                y=[y.ira_growth_rate * 100 for y in r.years if y.ira_total > 0],
                name=label,
                line={"color": color, "width": 2},
            )
        )

    fig_gr.update_layout(
        xaxis_title="Your Age",
        yaxis_title="IRA Blended Growth (%)",
        height=350,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig_gr, width="stretch")

    # --- RMD Comparison ---
    st.markdown("### RMD Size Comparison")

    fig_rmd = go.Figure()
    for strat, (label, color) in STRATEGIES.items():
        r = results[strat]
        rmd_years = [y for y in r.years if y.rmd > 0]
        fig_rmd.add_trace(
            go.Bar(
                x=[y.your_age for y in rmd_years],
                y=[y.rmd for y in rmd_years],
                name=label,
                marker_color=color,
            )
        )

    fig_rmd.update_layout(
        barmode="group",
        xaxis_title="Your Age",
        yaxis_title="Annual RMD ($)",
        yaxis_tickformat="$,.0s",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig_rmd, width="stretch")

    # --- Explanation ---
    st.markdown("---")
    st.markdown("### How It Works")
    st.markdown("""
**The core idea**: You pay the same conversion tax regardless of *which* assets you convert.
But the long-term outcome differs dramatically:

- **Equity First**: Convert stocks into Roth where they grow tax-free at 9%+.
  The IRA retains mostly bonds (4%), so it grows slower, producing smaller RMDs.
  Your Roth grows faster (tax-free!). Best of both worlds.

- **Proportional**: Maintain the same 60/40 split in both accounts.
  IRA keeps growing at blended 7%. Standard approach but suboptimal.

- **Bond First**: Worst strategy — converts the slow growers, leaves equities
  in the IRA where they generate the largest future RMDs at the highest tax rates.

**Practical steps**:
1. Before converting, rebalance your IRA so equities are in the account you'll convert from
2. Convert equity positions first (individual stocks, equity ETFs/funds)
3. Keep bonds, CDs, stable value in the IRA for last
4. Your Roth ends up equity-heavy (tax-free growth!) while IRA is bond-heavy (smaller RMDs)
""")
