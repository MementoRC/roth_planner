"""RMD Squeeze Analyzer — visualize the forced distribution pressure.

Ages 75+ force Required Minimum Distributions that compound:
  RMD → higher bracket → IRMAA surcharges → excess flows to brokerage →
  cap gains tax → NIIT exposure

This page shows:
1. RMD waterfall: how much comes out and where it goes
2. Tax bracket escalation year-by-year
3. Excess RMD overflow into brokerage and its tax drag
4. QCD strategy analysis (charitable giving to offset RMD)
5. Side-by-side: no-conversion vs conversion RMD impact
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.ira import RMD_DIVISORS
from engine.scenario import ConversionPlan, auto_fill_12, run_no_conversion, run_scenario
from models.household import Household


def render(hh: Household):
    st.title("📉 RMD Squeeze Analyzer")
    st.caption(
        "See how Required Minimum Distributions force you into higher brackets, "
        "trigger IRMAA, and overflow into taxable brokerage accounts."
    )

    # --- Scenario selection ---
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        show_qcd = st.toggle(
            "Apply QCD Strategy",
            value=False,
            help="Qualified Charitable Distributions reduce taxable RMD (up to $111K/yr per person age 70½+).",
        )
    with col_s2:
        qcd_annual = st.number_input(
            "Annual QCD Amount",
            value=50_000,
            step=5_000,
            format="%d",
            disabled=not show_qcd,
            help=f"Max ${hh.qcd_limit:,.0f}/yr per person (2026, inflation-indexed).",
        )

    # --- Run scenarios ---
    no_conv = run_no_conversion(hh, end_age=95)
    plan_12 = auto_fill_12(hh)

    # Build QCD plan if toggled
    if show_qcd:
        qcd_plan = ConversionPlan(
            your_conversions=dict(plan_12.your_conversions),
            spouse_conversions=dict(plan_12.spouse_conversions),
            qcds={yr: qcd_annual for yr in range(hh.base_year, hh.base_year + 35) if hh.your_age_in(yr) >= 75},
        )
        with_conv = run_scenario(hh, qcd_plan, "Fill 12% + QCD", end_age=95)
        no_conv_qcd = run_scenario(
            hh,
            ConversionPlan(qcds={yr: qcd_annual for yr in range(hh.base_year, hh.base_year + 35) if hh.your_age_in(yr) >= 75}),
            "No Conv + QCD",
            end_age=95,
        )
    else:
        with_conv = run_scenario(hh, plan_12, "Fill 12%", end_age=95)
        no_conv_qcd = None

    # Filter to RMD years (75+)
    rmd_nc = [yr for yr in no_conv.years if yr.your_age >= 75]
    rmd_wc = [yr for yr in with_conv.years if yr.your_age >= 75]
    rmd_nc_qcd = [yr for yr in no_conv_qcd.years if yr.your_age >= 75] if no_conv_qcd else None

    if not rmd_nc:
        st.warning("No RMD years in projection range.")
        return

    ages = [yr.your_age for yr in rmd_nc]

    # --- Top metrics ---
    c1, c2, c3, c4 = st.columns(4)

    yr75_nc = rmd_nc[0]
    yr75_wc = rmd_wc[0]

    with c1:
        st.metric(
            "IRA at 75 (No Conv)",
            f"${(yr75_nc.your_ira_begin + yr75_nc.spouse_ira_begin) / 1e6:.2f}M",
        )
    with c2:
        st.metric(
            "IRA at 75 (With Conv)",
            f"${(yr75_wc.your_ira_begin + yr75_wc.spouse_ira_begin) / 1e6:.2f}M",
            f"{(yr75_wc.your_ira_begin + yr75_wc.spouse_ira_begin - yr75_nc.your_ira_begin - yr75_nc.spouse_ira_begin) / 1e6:+.2f}M",
        )
    with c3:
        total_rmd_nc = sum(yr.your_rmd for yr in rmd_nc)
        total_rmd_wc = sum(yr.your_rmd for yr in rmd_wc)
        st.metric(
            "Total RMDs (75-95)",
            f"${total_rmd_nc / 1e6:.1f}M",
            f"Conv: ${total_rmd_wc / 1e6:.1f}M ({(total_rmd_wc - total_rmd_nc) / total_rmd_nc * 100:+.0f}%)",
        )
    with c4:
        total_tax_nc = sum(yr.federal_tax_amt for yr in rmd_nc)
        total_tax_wc = sum(yr.federal_tax_amt for yr in rmd_wc)
        st.metric(
            "Total RMD-Era Tax",
            f"${total_tax_nc:,.0f}",
            f"Conv: ${total_tax_wc:,.0f} (save ${total_tax_nc - total_tax_wc:,.0f})",
        )

    st.markdown("---")

    # --- Chart 1: RMD Income Waterfall ---
    st.markdown("### Income Composition During RMD Years")
    st.caption("Stacked view: where your income comes from and how it fills brackets.")

    fig_w = go.Figure()

    # No-conversion scenario stacks
    fig_w.add_trace(go.Bar(
        x=ages, y=[yr.taxable_rmd for yr in rmd_nc],
        name="Taxable RMD", marker_color="#ef4444",
        hovertemplate="RMD: $%{y:,.0f}<extra></extra>",
    ))
    fig_w.add_trace(go.Bar(
        x=ages, y=[yr.taxable_ss_amt for yr in rmd_nc],
        name="Taxable SS", marker_color="#60a5fa",
        hovertemplate="Taxable SS: $%{y:,.0f}<extra></extra>",
    ))

    # Bracket ceiling lines
    for yr in rmd_nc[:1]:  # use first year's deductions for reference
        ded = yr.total_deductions
        fig_w.add_hline(
            y=ded + 100_800, line_dash="dash", line_color="#22c55e",
            annotation_text="12% ceiling",
        )
        fig_w.add_hline(
            y=ded + 211_400, line_dash="dash", line_color="#f59e0b",
            annotation_text="22% ceiling",
        )

    fig_w.update_layout(
        barmode="stack",
        xaxis_title="Your Age",
        yaxis_title="Income ($)",
        yaxis_tickformat="$,.0s",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
    )
    st.plotly_chart(fig_w, width="stretch")

    # --- Chart 2: Bracket Comparison ---
    st.markdown("### Marginal Bracket: No Conversion vs With Conversion")

    fig_br = go.Figure()
    fig_br.add_trace(go.Scatter(
        x=ages,
        y=[yr.marginal_bracket * 100 for yr in rmd_nc],
        name="No Conversion",
        line={"color": "#ef4444", "width": 3},
        mode="lines+markers",
        hovertemplate="Age %{x}: %{y:.0f}%<extra>No Conv</extra>",
    ))
    fig_br.add_trace(go.Scatter(
        x=ages,
        y=[yr.marginal_bracket * 100 for yr in rmd_wc],
        name="With Conversion (12%)",
        line={"color": "#22c55e", "width": 3},
        mode="lines+markers",
        hovertemplate="Age %{x}: %{y:.0f}%<extra>With Conv</extra>",
    ))

    fig_br.update_layout(
        xaxis_title="Your Age",
        yaxis_title="Marginal Bracket (%)",
        yaxis={"dtick": 2},
        height=350,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig_br, width="stretch")

    # --- Chart 3: Annual Tax + IRMAA + Brokerage Tax ---
    st.markdown("### All-In Annual Cost: Tax + IRMAA + Brokerage Drag")

    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown("#### No Conversion")
        fig_nc = go.Figure()
        fig_nc.add_trace(go.Bar(
            x=ages, y=[yr.federal_tax_amt for yr in rmd_nc],
            name="Fed Tax", marker_color="#ef4444",
        ))
        fig_nc.add_trace(go.Bar(
            x=ages, y=[yr.irmaa_cost for yr in rmd_nc],
            name="IRMAA", marker_color="#f59e0b",
        ))
        fig_nc.add_trace(go.Bar(
            x=ages, y=[yr.brokerage_gain_tax for yr in rmd_nc],
            name="Brok Cap Gains", marker_color="#8b5cf6",
        ))
        fig_nc.add_trace(go.Bar(
            x=ages, y=[yr.niit_cost for yr in rmd_nc],
            name="NIIT", marker_color="#ec4899",
        ))
        fig_nc.update_layout(
            barmode="stack",
            xaxis_title="Your Age",
            yaxis_title="Annual Cost ($)",
            yaxis_tickformat="$,.0s",
            height=350,
        )
        st.plotly_chart(fig_nc, width="stretch")

    with col_r:
        st.markdown("#### With Conversion (Fill 12%)")
        fig_wc = go.Figure()
        fig_wc.add_trace(go.Bar(
            x=ages, y=[yr.federal_tax_amt for yr in rmd_wc],
            name="Fed Tax", marker_color="#3b82f6",
        ))
        fig_wc.add_trace(go.Bar(
            x=ages, y=[yr.irmaa_cost for yr in rmd_wc],
            name="IRMAA", marker_color="#f59e0b",
        ))
        fig_wc.add_trace(go.Bar(
            x=ages, y=[yr.brokerage_gain_tax for yr in rmd_wc],
            name="Brok Cap Gains", marker_color="#8b5cf6",
        ))
        fig_wc.add_trace(go.Bar(
            x=ages, y=[yr.niit_cost for yr in rmd_wc],
            name="NIIT", marker_color="#ec4899",
        ))
        fig_wc.update_layout(
            barmode="stack",
            xaxis_title="Your Age",
            yaxis_title="Annual Cost ($)",
            yaxis_tickformat="$,.0s",
            height=350,
        )
        st.plotly_chart(fig_wc, width="stretch")

    # --- Chart 4: Brokerage Overflow ---
    st.markdown("### Brokerage Overflow — Excess RMD Accumulation")
    st.caption(
        "RMDs exceeding living expenses flow into taxable brokerage, "
        "creating ongoing capital gains tax drag."
    )

    fig_brok = go.Figure()
    fig_brok.add_trace(go.Scatter(
        x=ages,
        y=[yr.brokerage_balance for yr in rmd_nc],
        name="No Conversion",
        fill="tozeroy",
        fillcolor="rgba(239,68,68,0.15)",
        line={"color": "#ef4444", "width": 2},
        hovertemplate="Age %{x}: $%{y:,.0f}<extra>No Conv Brok</extra>",
    ))
    fig_brok.add_trace(go.Scatter(
        x=ages,
        y=[yr.brokerage_balance for yr in rmd_wc],
        name="With Conversion",
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.15)",
        line={"color": "#3b82f6", "width": 2},
        hovertemplate="Age %{x}: $%{y:,.0f}<extra>With Conv Brok</extra>",
    ))
    fig_brok.update_layout(
        xaxis_title="Your Age",
        yaxis_title="Brokerage Balance ($)",
        yaxis_tickformat="$,.0s",
        height=350,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
    )
    st.plotly_chart(fig_brok, width="stretch")

    # --- Chart 5: QCD impact (if toggled) ---
    if no_conv_qcd and rmd_nc_qcd:
        st.markdown("### QCD Impact — Tax Savings from Charitable Distributions")
        fig_qcd = go.Figure()
        fig_qcd.add_trace(go.Scatter(
            x=ages,
            y=[yr.federal_tax_amt for yr in rmd_nc],
            name="No Conv, No QCD",
            line={"color": "#ef4444", "width": 2, "dash": "dash"},
        ))
        fig_qcd.add_trace(go.Scatter(
            x=ages,
            y=[yr.federal_tax_amt for yr in rmd_nc_qcd],
            name=f"No Conv + ${qcd_annual / 1000:.0f}K QCD",
            line={"color": "#f59e0b", "width": 2},
        ))
        fig_qcd.add_trace(go.Scatter(
            x=ages,
            y=[yr.federal_tax_amt for yr in rmd_wc],
            name=f"Fill 12% + ${qcd_annual / 1000:.0f}K QCD",
            line={"color": "#22c55e", "width": 3},
        ))
        fig_qcd.update_layout(
            xaxis_title="Your Age",
            yaxis_title="Annual Federal Tax ($)",
            yaxis_tickformat="$,.0s",
            height=400,
            legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
        )
        st.plotly_chart(fig_qcd, width="stretch")

    # --- Detail table ---
    st.markdown("### Year-by-Year RMD Detail")

    rows = []
    for nc, wc in zip(rmd_nc, rmd_wc, strict=False):
        rows.append({
            "Year": str(nc.year),
            "You/Sp": f"{nc.your_age}/{nc.spouse_age}",
            "IRA (NC)": f"${(nc.your_ira_begin + nc.spouse_ira_begin) / 1e6:.2f}M",
            "IRA (WC)": f"${(wc.your_ira_begin + wc.spouse_ira_begin) / 1e6:.2f}M",
            "RMD (NC)": f"${nc.your_rmd:,.0f}",
            "RMD (WC)": f"${wc.your_rmd:,.0f}",
            "SS": f"${nc.combined_ss:,.0f}",
            "Bracket (NC)": f"{nc.marginal_bracket * 100:.0f}%",
            "Bracket (WC)": f"{wc.marginal_bracket * 100:.0f}%",
            "Tax (NC)": f"${nc.federal_tax_amt:,.0f}",
            "Tax (WC)": f"${wc.federal_tax_amt:,.0f}",
            "Saved": f"${nc.federal_tax_amt - wc.federal_tax_amt:,.0f}",
            "IRMAA (NC)": f"${nc.irmaa_cost:,.0f}",
            "IRMAA (WC)": f"${wc.irmaa_cost:,.0f}",
            "Excess RMD": f"${nc.excess_rmd:,.0f}",
            "Brok (NC)": f"${nc.brokerage_balance:,.0f}",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch")

    # --- RMD divisor reference ---
    with st.expander("📖 RMD Divisor Table (Uniform Lifetime)"):
        div_rows = [
            {"Age": age, "Divisor": div, "RMD % of IRA": f"{1 / div * 100:.1f}%"}
            for age, div in sorted(RMD_DIVISORS.items())
            if age >= 75
        ]
        st.dataframe(pd.DataFrame(div_rows), hide_index=True)

    # --- Squeeze explanation ---
    st.markdown("---")
    st.markdown("### The RMD Squeeze Explained")
    st.markdown("""
- **The problem**: At 75, you *must* take distributions from your IRA — the IRS sets the amount
- **Divisor shrinks**: At 75 you withdraw ~4.1%, by 85 it's ~6.3%, by 95 it's ~11.2%
- **Growth amplifies**: If your IRA grew from $1.7M to $4.4M untouched, RMDs are huge
- **Bracket escalation**: Large RMDs + SS push you from 12% into 22-24% brackets
- **IRMAA trigger**: MAGI over $218K means Medicare surcharges ($3,400+/yr for couple)
- **Brokerage overflow**: RMDs exceeding living expenses create taxable investment accounts
- **The fix**: Converting during low-income years (ages 61-74) shrinks the IRA *before* RMDs start
- **QCD option**: At 70½+, donating up to $111K/yr directly from IRA to charity bypasses taxation
""")
