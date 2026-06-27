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
from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE
from engine.scenario import ConversionPlan, run_no_conversion, run_scenario
from engine.scenario_autofill import auto_fill_12
from engine.scenario_compute import QCD_MIN_AGE
from engine.tax import BRACKETS_MFJ, BRACKETS_SINGLE
from engine.tax_indexing import index_value as _index_value
from models.household import Household
from views._format import FORM_8606_CAPTION, fmt_dollars, fmt_dollars_short, fmt_pct


def render(hh: Household):
    st.title("📉 RMD Squeeze Analyzer")
    st.caption(
        "See how Required Minimum Distributions force you into higher brackets, "
        "trigger IRMAA, and overflow into taxable brokerage accounts."
    )
    st.caption(FORM_8606_CAPTION)

    # --- Scenario selection ---
    _is_mfj = hh.filing_status == "MFJ"
    if _is_mfj:
        col_s1, col_s2, col_s3 = st.columns(3)
    else:
        col_s1, col_s2 = st.columns(2)
    with col_s1:
        show_qcd = st.toggle(
            "Apply QCD Strategy",
            value=False,
            help="Qualified Charitable Distributions reduce taxable RMD (up to $111K/yr per person age 70½+).",
        )
    with col_s2:
        qcd_annual = st.number_input(
            "Your Annual QCD",
            value=50_000,
            step=5_000,
            format="%d",
            disabled=not show_qcd,
            help=f"Your QCD: max ${hh.qcd_limit:,.0f}/yr (age 70½+, 2026 limit).",
        )
    if _is_mfj:
        with col_s3:
            spouse_qcd_annual = st.number_input(
                "Spouse Annual QCD",
                value=50_000,
                step=5_000,
                format="%d",
                disabled=not show_qcd,
                help=f"Spouse QCD: max ${hh.qcd_limit:,.0f}/yr (age 70½+, 2026 limit).",
            )
    else:
        spouse_qcd_annual = 0

    # --- Run scenarios ---
    no_conv = run_no_conversion(hh, end_age=95)
    plan_12 = auto_fill_12(hh)

    # Build QCD plan if toggled
    if show_qcd:
        your_qcd_years = {
            yr: qcd_annual
            for yr in range(hh.base_year, hh.base_year + 35)
            if hh.your_age_in(yr) >= QCD_MIN_AGE
        }
        spouse_qcd_years = {
            yr: spouse_qcd_annual
            for yr in range(hh.base_year, hh.base_year + 35)
            if hh.spouse_age_in(yr) >= QCD_MIN_AGE
        }
        qcd_plan = ConversionPlan(
            your_conversions=dict(plan_12.your_conversions),
            spouse_conversions=dict(plan_12.spouse_conversions),
            qcds=your_qcd_years,
            spouse_qcds=spouse_qcd_years,
        )
        with_conv = run_scenario(hh, qcd_plan, "Fill 12% + QCD", end_age=95)
        no_conv_qcd = run_scenario(
            hh,
            ConversionPlan(qcds=your_qcd_years, spouse_qcds=spouse_qcd_years),
            "No Conv + QCD",
            end_age=95,
        )
    else:
        with_conv = run_scenario(hh, plan_12, "Fill 12%", end_age=95)
        no_conv_qcd = None

    # Filter to RMD years (each person's RMD start age and later)
    rmd_nc = [yr for yr in no_conv.years if yr.your_age >= hh.your_rmd_start_age]
    rmd_wc = [yr for yr in with_conv.years if yr.your_age >= hh.your_rmd_start_age]
    rmd_nc_qcd = (
        [yr for yr in no_conv_qcd.years if yr.your_age >= hh.your_rmd_start_age]
        if no_conv_qcd
        else None
    )

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
            f"IRA at {hh.your_rmd_start_age} (No Conv)",
            fmt_dollars_short(yr75_nc.your_ira_begin + yr75_nc.spouse_ira_begin, decimals=2),
        )
    with c2:
        st.metric(
            f"IRA at {hh.your_rmd_start_age} (With Conv)",
            fmt_dollars_short(yr75_wc.your_ira_begin + yr75_wc.spouse_ira_begin, decimals=2),
            fmt_dollars_short(
                yr75_wc.your_ira_begin
                + yr75_wc.spouse_ira_begin
                - yr75_nc.your_ira_begin
                - yr75_nc.spouse_ira_begin,
                decimals=2,
            ),
        )
    with c3:
        total_rmd_nc = sum(yr.your_rmd + yr.spouse_rmd for yr in rmd_nc)
        total_rmd_wc = sum(yr.your_rmd + yr.spouse_rmd for yr in rmd_wc)
        st.metric(
            f"Total RMDs ({hh.your_rmd_start_age}-95)",
            fmt_dollars_short(total_rmd_nc),
            f"Conv: {fmt_dollars_short(total_rmd_wc)} ({fmt_pct((total_rmd_wc - total_rmd_nc) / total_rmd_nc, 0, sign=True)})",
        )
    with c4:
        total_tax_nc = sum(yr.federal_tax_amt for yr in rmd_nc)
        total_tax_wc = sum(yr.federal_tax_amt for yr in rmd_wc)
        st.metric(
            "Total RMD-Era Tax",
            fmt_dollars(total_tax_nc),
            f"Conv: {fmt_dollars(total_tax_wc)} (save {fmt_dollars(total_tax_nc - total_tax_wc)})",
        )

    st.markdown("---")

    # Prior-year MAGI anchor for IRMAA 2-year lookback
    prior_magi = st.session_state.get("prior_year_magi") or {}
    if prior_magi:
        sorted_years = sorted(prior_magi.keys(), reverse=True)
        most_recent = sorted_years[0]
        st.caption(
            f"Prior-year MAGI anchor ({most_recent}): ${prior_magi[most_recent]:,.0f}"
            " — used for IRMAA 2-year lookback"
        )

    # --- Chart 1: RMD Income Waterfall ---
    st.markdown("### Income Composition During RMD Years")
    st.caption("Stacked view: where your income comes from and how it fills brackets.")

    fig_w = go.Figure()

    # No-conversion scenario stacks
    fig_w.add_trace(
        go.Bar(
            x=ages,
            y=[yr.taxable_rmd for yr in rmd_nc],
            name="Taxable RMD",
            marker_color="#ef4444",
            hovertemplate="RMD: $%{y:,.0f}<extra></extra>",
        )
    )
    fig_w.add_trace(
        go.Bar(
            x=ages,
            y=[yr.taxable_ss_amt for yr in rmd_nc],
            name="Taxable SS",
            marker_color="#60a5fa",
            hovertemplate="Taxable SS: $%{y:,.0f}<extra></extra>",
        )
    )

    # Bracket ceiling lines — CPI-indexed per-year (mirrors planner.py)
    _brackets = BRACKETS_SINGLE if hh.filing_status == "Single" else BRACKETS_MFJ
    _cpi = hh.cpi_assumption
    ceil_12_values = [
        yr.total_deductions + _index_value(_brackets[1][0], yr.year, _cpi) for yr in rmd_nc
    ]
    ceil_22_values = [
        yr.total_deductions + _index_value(_brackets[2][0], yr.year, _cpi) for yr in rmd_nc
    ]
    fig_w.add_trace(
        go.Scatter(
            x=ages,
            y=ceil_12_values,
            name="12% ceiling",
            line={"color": "#22c55e", "width": 2, "dash": "dash"},
            mode="lines",
        )
    )
    fig_w.add_trace(
        go.Scatter(
            x=ages,
            y=ceil_22_values,
            name="22% ceiling",
            line={"color": "#f59e0b", "width": 2, "dash": "dash"},
            mode="lines",
        )
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
    fig_br.add_trace(
        go.Scatter(
            x=ages,
            y=[yr.marginal_bracket * 100 for yr in rmd_nc],
            name="No Conversion",
            line={"color": "#ef4444", "width": 3},
            mode="lines+markers",
            hovertemplate="Age %{x}: %{y:.0f}%<extra>No Conv</extra>",
        )
    )
    fig_br.add_trace(
        go.Scatter(
            x=ages,
            y=[yr.marginal_bracket * 100 for yr in rmd_wc],
            name="With Conversion (12%)",
            line={"color": "#22c55e", "width": 3},
            mode="lines+markers",
            hovertemplate="Age %{x}: %{y:.0f}%<extra>With Conv</extra>",
        )
    )

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
        fig_nc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.federal_tax_amt for yr in rmd_nc],
                name="Fed Tax",
                marker_color="#ef4444",
            )
        )
        fig_nc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.irmaa_cost for yr in rmd_nc],
                name="IRMAA",
                marker_color="#f59e0b",
            )
        )
        fig_nc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.brokerage_gain_tax for yr in rmd_nc],
                name="Brok Cap Gains",
                marker_color="#8b5cf6",
            )
        )
        fig_nc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.niit_cost for yr in rmd_nc],
                name="NIIT",
                marker_color="#ec4899",
            )
        )
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
        fig_wc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.federal_tax_amt for yr in rmd_wc],
                name="Fed Tax",
                marker_color="#3b82f6",
            )
        )
        fig_wc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.irmaa_cost for yr in rmd_wc],
                name="IRMAA",
                marker_color="#f59e0b",
            )
        )
        fig_wc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.brokerage_gain_tax for yr in rmd_wc],
                name="Brok Cap Gains",
                marker_color="#8b5cf6",
            )
        )
        fig_wc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.niit_cost for yr in rmd_wc],
                name="NIIT",
                marker_color="#ec4899",
            )
        )
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
    fig_brok.add_trace(
        go.Scatter(
            x=ages,
            y=[yr.brokerage_balance for yr in rmd_nc],
            name="No Conversion",
            fill="tozeroy",
            fillcolor="rgba(239,68,68,0.15)",
            line={"color": "#ef4444", "width": 2},
            hovertemplate="Age %{x}: $%{y:,.0f}<extra>No Conv Brok</extra>",
        )
    )
    fig_brok.add_trace(
        go.Scatter(
            x=ages,
            y=[yr.brokerage_balance for yr in rmd_wc],
            name="With Conversion",
            fill="tozeroy",
            fillcolor="rgba(59,130,246,0.15)",
            line={"color": "#3b82f6", "width": 2},
            hovertemplate="Age %{x}: $%{y:,.0f}<extra>With Conv Brok</extra>",
        )
    )
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
        fig_qcd.add_trace(
            go.Scatter(
                x=ages,
                y=[yr.federal_tax_amt for yr in rmd_nc],
                name="No Conv, No QCD",
                line={"color": "#ef4444", "width": 2, "dash": "dash"},
            )
        )
        fig_qcd.add_trace(
            go.Scatter(
                x=ages,
                y=[yr.federal_tax_amt for yr in rmd_nc_qcd],
                name=f"No Conv + ${qcd_annual / 1000:.0f}K QCD",
                line={"color": "#f59e0b", "width": 2},
            )
        )
        fig_qcd.add_trace(
            go.Scatter(
                x=ages,
                y=[yr.federal_tax_amt for yr in rmd_wc],
                name=f"Fill 12% + ${qcd_annual / 1000:.0f}K QCD",
                line={"color": "#22c55e", "width": 3},
            )
        )
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
        rows.append(
            {
                "Year": str(nc.year),
                "You/Sp": f"{nc.your_age}/{nc.spouse_age}",
                "IRA (NC)": fmt_dollars_short(nc.your_ira_begin + nc.spouse_ira_begin, decimals=2),
                "IRA (WC)": fmt_dollars_short(wc.your_ira_begin + wc.spouse_ira_begin, decimals=2),
                "RMD (NC)": fmt_dollars(nc.your_rmd + nc.spouse_rmd),
                "RMD (WC)": fmt_dollars(wc.your_rmd + wc.spouse_rmd),
                "SS": fmt_dollars(nc.combined_ss),
                "Bracket (NC)": fmt_pct(nc.marginal_bracket, 0),
                "Bracket (WC)": fmt_pct(wc.marginal_bracket, 0),
                "Tax (NC)": fmt_dollars(nc.federal_tax_amt),
                "Tax (WC)": fmt_dollars(wc.federal_tax_amt),
                "Saved": fmt_dollars(nc.federal_tax_amt - wc.federal_tax_amt),
                "IRMAA (NC)": fmt_dollars(nc.irmaa_cost),
                "IRMAA (WC)": fmt_dollars(wc.irmaa_cost),
                "Excess RMD": fmt_dollars(nc.excess_rmd),
                "Brok (NC)": fmt_dollars(nc.brokerage_balance),
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch")

    # --- RMD divisor reference ---
    with st.expander("📖 RMD Divisor Table (Uniform Lifetime)"):
        div_rows = [
            {"Age": age, "Divisor": div, "RMD % of IRA": fmt_pct(1 / div)}
            for age, div in sorted(RMD_DIVISORS.items())
            if age >= 75
        ]
        st.dataframe(pd.DataFrame(div_rows), hide_index=True)

    # --- Squeeze explanation ---
    st.markdown("---")
    st.markdown("### The RMD Squeeze Explained")
    _irmaa_tiers = IRMAA_TIERS_MFJ if _is_mfj else IRMAA_TIERS_SINGLE
    # IRMAA surcharges are assessed per Medicare beneficiary: 2026 Tier-1 is
    # ~$1,150/yr per person (Part B $974.40 + Part D $174.00); a two-beneficiary
    # household pays ~$2,300/yr.
    _irmaa_surcharge_note = (
        "~$2,300/yr for a couple" if _is_mfj else "~$1,150/yr individually"
    )
    st.markdown(f"""
- **The problem**: At 75, you *must* take distributions from your IRA — the IRS sets the amount
- **Divisor shrinks**: At 75 you withdraw ~4.1%, by 85 it's ~6.3%, by 95 it's ~11.2%
- **Growth amplifies**: If your IRA grew from $1.7M to $4.4M untouched, RMDs are huge
- **Bracket escalation**: Large RMDs + SS push you from 12% into 22-24% brackets
- **IRMAA trigger**: MAGI over ${_irmaa_tiers[0][0] / 1000:.0f}K means Medicare surcharges ({_irmaa_surcharge_note} at Tier 1)
- **Brokerage overflow**: RMDs exceeding living expenses create taxable investment accounts
- **The fix**: Converting during low-income years (ages 61-74) shrinks the IRA *before* RMDs start
- **QCD option**: At 70½+, donating up to ${hh.qcd_limit / 1000:.0f}K/yr directly from IRA to charity bypasses taxation
""")
