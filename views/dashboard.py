"""Dashboard page — the 'is this worth it?' overview.

Shows:
1. IRA trajectory: Convert vs Don't (combined both spouses)
2. Annual tax comparison
3. Cumulative net benefit over time
4. Key metrics at ages 80/85/90/95
"""

import plotly.graph_objects as go
import streamlit as st

from engine.scenario import run_no_conversion, run_scenario
from engine.scenario_autofill import auto_fill_12
from engine.scenario_compare import compute_cumulative_net_benefit, compute_summary_rows
from models.household import Household
from views._format import FORM_8606_CAPTION, fmt_dollars, fmt_dollars_short, fmt_pct


def render(hh: Household):
    st.title("📊 Dashboard — Is Converting Worth It?")
    if hh.filing_status == "Single":
        st.caption(
            f"You {hh.your_age} · "
            f"IRA {fmt_dollars_short(hh.your_ira)} · "
            f"{fmt_pct(hh.growth_rate, 0)} growth · "
            f"RMD at {hh.your_rmd_start_age}"
        )
    else:
        st.caption(
            f"You {hh.your_age} / Spouse {hh.spouse_age} · "
            f"IRAs {fmt_dollars_short(hh.your_ira)} + {fmt_dollars_short(hh.spouse_ira)} · "
            f"{fmt_pct(hh.growth_rate, 0)} growth · "
            f"RMD at {hh.your_rmd_start_age}"
            + (
                f"/{hh.spouse_rmd_start_age}"
                if hh.spouse_rmd_start_age != hh.your_rmd_start_age
                else ""
            )
        )

    st.caption(FORM_8606_CAPTION)

    # --- Run both scenarios ---
    # C27 (audit-0721): thread base-year YTD actuals through, mirroring the
    # apply_ytd_to_projection gating already used by sweet_spot.py/aca_irmaa.py
    # -- otherwise the YTD Income page's "Apply YTD to projections" toggle has
    # no effect on the dashboard. run_scenario/run_no_conversion narrow ytd to
    # the base year internally.
    _apply_ytd = st.session_state.get("apply_ytd_to_projection", False)
    _ytd = st.session_state.get("ytd_snapshot") if _apply_ytd else None
    no_conv = run_no_conversion(hh, end_age=95, ytd=_ytd)
    plan_12 = auto_fill_12(hh)
    with_conv = run_scenario(hh, plan_12, "Fill 12%", end_age=95, ytd=_ytd)

    # --- Build data ---
    ages = [yr.your_age for yr in no_conv.years]

    # Combined IRA + Roth (grid-01: include Roth so converted principal is not invisible)
    ira_nc = [
        yr.your_ira_begin + yr.spouse_ira_begin + yr.your_roth_begin + yr.spouse_roth_begin
        for yr in no_conv.years
    ]
    ira_wc = [
        yr.your_ira_begin + yr.spouse_ira_begin + yr.your_roth_begin + yr.spouse_roth_begin
        for yr in with_conv.years
    ]

    # Annual tax
    tax_nc = [yr.federal_tax_amt for yr in no_conv.years]
    tax_wc = [yr.federal_tax_amt for yr in with_conv.years]

    # Cumulative tax
    cum_tax_nc, cum_tax_wc = [], []
    ct_nc, ct_wc = 0, 0
    for t_nc, t_wc in zip(tax_nc, tax_wc, strict=False):
        ct_nc += t_nc
        ct_wc += t_wc
        cum_tax_nc.append(ct_nc)
        cum_tax_wc.append(ct_wc)

    # --- Top metrics ---
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        conv_total = with_conv.total_your_conv + with_conv.total_spouse_conv
        st.metric(
            "Total Converted",
            fmt_dollars_short(conv_total, decimals=2),
            f"You {fmt_dollars_short(with_conv.total_your_conv, decimals=2)} + Sp {fmt_dollars_short(with_conv.total_spouse_conv, decimals=2)}",
        )

    with col2:
        st.metric(
            "Conversion Tax Paid",
            fmt_dollars(with_conv.total_conv_tax),
            f"Avg rate: {fmt_pct(with_conv.total_conv_tax / max(conv_total, 1))}",
        )

    with col3:
        tax_saved = no_conv.total_rmd_tax - with_conv.total_rmd_tax
        st.metric(
            f"RMD Tax Saved ({hh.your_rmd_start_age}-95)",
            fmt_dollars(tax_saved),
            f"{fmt_dollars(no_conv.total_rmd_tax)} → {fmt_dollars(with_conv.total_rmd_tax)}",
        )

    with col4:
        # Canonical all-in net benefit: matches the Scenario Comparator (audit 0705
        # #views-financial-10).  Folds in federal tax + IRMAA + brokerage gain tax
        # + ACA subsidy loss + NIIT across all years (not just RMD years).
        _summary = compute_summary_rows([no_conv, with_conv], no_conv)
        net = _summary[1].savings_vs_baseline  # positive = saves money vs no-conversion
        st.metric(
            "Net Lifetime Benefit", fmt_dollars(net), fmt_dollars(net, sign=True), delta_color="normal" if net >= 0 else "inverse"
        )

    st.markdown("---")

    # --- Chart 1: IRA Trajectory ---
    fig_ira = go.Figure()
    fig_ira.add_trace(
        go.Scatter(
            x=ages,
            y=ira_nc,
            name="No Conversion",
            line={"color": "#ef4444", "width": 2, "dash": "dash"},
            hovertemplate="Age %{x}<br>IRA + Roth: $%{y:,.0f}<extra>No Conversion</extra>",
        )
    )
    fig_ira.add_trace(
        go.Scatter(
            x=ages,
            y=ira_wc,
            name="With Conversion (12%)",
            line={"color": "#22c55e", "width": 3},
            hovertemplate="Age %{x}<br>IRA + Roth: $%{y:,.0f}<extra>With Conversion</extra>",
        )
    )
    # RMD start line
    fig_ira.add_vline(
        x=hh.your_rmd_start_age, line_dash="dot", line_color="gray", annotation_text="RMDs begin"
    )
    fig_ira.update_layout(
        title="Combined IRA + Roth Trajectory (Both Spouses)",
        xaxis_title="Your Age",
        yaxis_title="IRA + Roth Balance ($)",
        yaxis_tickformat="$,.0s",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig_ira, width="stretch")

    # --- Charts 2 & 3 side by side ---
    col_left, col_right = st.columns(2)

    with col_left:
        # Annual tax comparison
        fig_tax = go.Figure()
        fig_tax.add_trace(
            go.Bar(
                x=ages,
                y=tax_nc,
                name="No Conversion",
                marker_color="#ef4444",
                opacity=0.6,
                hovertemplate="Age %{x}: $%{y:,.0f}<extra>No Conv Tax</extra>",
            )
        )
        fig_tax.add_trace(
            go.Bar(
                x=ages,
                y=tax_wc,
                name="With Conversion",
                marker_color="#3b82f6",
                opacity=0.8,
                hovertemplate="Age %{x}: $%{y:,.0f}<extra>With Conv Tax</extra>",
            )
        )
        fig_tax.update_layout(
            title="Annual Federal Tax",
            xaxis_title="Your Age",
            yaxis_title="Tax ($)",
            yaxis_tickformat="$,.0s",
            barmode="group",
            height=350,
            legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
        )
        st.plotly_chart(fig_tax, width="stretch")

    with col_right:
        # Cumulative tax
        fig_cum = go.Figure()
        fig_cum.add_trace(
            go.Scatter(
                x=ages,
                y=cum_tax_nc,
                name="No Conversion",
                fill="tozeroy",
                fillcolor="rgba(239,68,68,0.15)",
                line={"color": "#ef4444", "width": 2},
            )
        )
        fig_cum.add_trace(
            go.Scatter(
                x=ages,
                y=cum_tax_wc,
                name="With Conversion",
                fill="tozeroy",
                fillcolor="rgba(59,130,246,0.15)",
                line={"color": "#3b82f6", "width": 2},
            )
        )
        fig_cum.update_layout(
            title="Cumulative Federal Tax",
            xaxis_title="Your Age",
            yaxis_title="Cumulative Tax ($)",
            yaxis_tickformat="$,.0s",
            height=350,
            legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
        )
        st.plotly_chart(fig_cum, width="stretch")

    # --- Chart 4: Net Benefit Over Time ---
    # Uses compute_cumulative_net_benefit (all-in: federal + IRMAA + brok + ACA + NIIT)
    # so the chart and the col4 headline metric are consistent (audit 0705 #views-financial-10).
    net_benefit = compute_cumulative_net_benefit(
        with_conv, no_conv, rmd_start_age=hh.your_rmd_start_age
    )

    fig_net = go.Figure()
    fig_net.add_trace(
        go.Scatter(
            x=ages,
            y=net_benefit,
            name="Net Benefit",
            fill="tozeroy",
            fillcolor="rgba(34,197,94,0.15)",
            line={"color": "#22c55e", "width": 3},
            hovertemplate="Age %{x}: $%{y:,.0f}<extra>Net Benefit</extra>",
        )
    )
    fig_net.add_hline(y=0, line_dash="dash", line_color="gray")
    fig_net.add_vline(
        x=hh.your_rmd_start_age, line_dash="dot", line_color="gray", annotation_text="RMDs begin"
    )

    # Find break-even age
    breakeven = None
    for _i, (a, nb) in enumerate(zip(ages, net_benefit, strict=False)):
        if nb >= 0 and a >= hh.your_rmd_start_age:
            breakeven = a
            break

    if breakeven:
        fig_net.add_annotation(
            x=breakeven,
            y=0,
            text=f"Break-even: age {breakeven}",
            showarrow=True,
            arrowhead=2,
            bgcolor="#22c55e",
            font={"color": "white"},
        )

    fig_net.update_layout(
        title="Net Benefit: All-In Cost Savings (Tax + IRMAA + ACA + NIIT + Brokerage)",
        xaxis_title="Your Age",
        yaxis_title="Net Benefit ($)",
        yaxis_tickformat="$,.0s",
        height=400,
    )
    st.plotly_chart(fig_net, width="stretch")

    # --- Summary table ---
    st.markdown("### Key Age Milestones")
    _rmd_age = hh.your_rmd_start_age
    milestones = [
        (_rmd_age, "RMDs begin"),
        (_rmd_age + 5, "5 yrs of RMDs"),
        (_rmd_age + 10, "10 yrs of RMDs"),
        (_rmd_age + 15, "15 yrs of RMDs"),
        (_rmd_age + 20, "20 yrs of RMDs"),
    ]
    cols = st.columns(len(milestones))
    for col, (age, label) in zip(cols, milestones, strict=False):
        yr_nc = next((yr for yr in no_conv.years if yr.your_age == age), None)
        yr_wc = next((yr for yr in with_conv.years if yr.your_age == age), None)
        nb_idx = age - hh.your_age
        nb = net_benefit[nb_idx] if nb_idx < len(net_benefit) else 0
        with col:
            st.markdown(f"**Age {age}**" + (f" (Sp {age - hh.age_gap})" if hh.filing_status != "Single" else ""))
            st.caption(label)
            if yr_nc and yr_wc:
                st.markdown(
                    f"IRA+Roth (NC): **{fmt_dollars_short(yr_nc.your_ira_begin + yr_nc.spouse_ira_begin + yr_nc.your_roth_begin + yr_nc.spouse_roth_begin)}**"
                )
                st.markdown(
                    f"IRA+Roth (WC): **{fmt_dollars_short(yr_wc.your_ira_begin + yr_wc.spouse_ira_begin + yr_wc.your_roth_begin + yr_wc.spouse_roth_begin)}**"
                )
                st.markdown(f"RMD (NC): {fmt_dollars(yr_nc.your_rmd + yr_nc.spouse_rmd)}")
                st.markdown(f"RMD (WC): {fmt_dollars(yr_wc.your_rmd + yr_wc.spouse_rmd)}")
                color = "green" if nb > 0 else "red"
                st.markdown(f"Net: :{color}[**{fmt_dollars(nb)}**]")

    # --- Conversion detail table ---
    st.markdown("---")
    with st.expander("📋 Auto-Fill 12% — Conversion Detail"):
        import pandas as pd

        conv_years = []
        for yr in with_conv.years:
            if yr.your_conversion > 0 or yr.spouse_conversion > 0:
                conv_years.append(
                    {
                        "Year": yr.year,
                        "You": yr.your_age,
                        "Sp": yr.spouse_age,
                        "Phase": yr.phase,
                        "Options": yr.option_income,
                        "Your Conv": yr.your_conversion,
                        "Sp Conv": yr.spouse_conversion,
                        "Gross": yr.combined_gross,
                        "Taxable": yr.taxable_income,
                        "Bracket": fmt_pct(yr.marginal_bracket, 0),
                        "Conv Tax": yr.conversion_tax,
                        "Room 12%": yr.room_12,
                        "Room 22%": yr.room_22,
                    }
                )
        if conv_years:
            df = pd.DataFrame(conv_years)
            for col in (
                "Options",
                "Your Conv",
                "Sp Conv",
                "Gross",
                "Taxable",
                "Conv Tax",
                "Room 12%",
                "Room 22%",
            ):
                df[col] = df[col].apply(fmt_dollars)
            st.dataframe(
                df,
                width="stretch",
                hide_index=True,
            )

    # --- RMD detail table ---
    with st.expander(f"📋 RMD Year Detail (from age {hh.your_rmd_start_age})"):
        import pandas as pd

        rmd_years = []
        for yr_nc, yr_wc in zip(no_conv.years, with_conv.years, strict=False):
            if yr_nc.your_age >= hh.your_rmd_start_age:
                rmd_years.append(
                    {
                        "Year": yr_nc.year,
                        "You": yr_nc.your_age,
                        "Sp": yr_nc.spouse_age,
                        "IRA+Roth (NC)": yr_nc.your_ira_begin
                        + yr_nc.spouse_ira_begin
                        + yr_nc.your_roth_begin
                        + yr_nc.spouse_roth_begin,
                        "IRA+Roth (WC)": yr_wc.your_ira_begin
                        + yr_wc.spouse_ira_begin
                        + yr_wc.your_roth_begin
                        + yr_wc.spouse_roth_begin,
                        "RMD (NC)": yr_nc.your_rmd + yr_nc.spouse_rmd,
                        "RMD (WC)": yr_wc.your_rmd + yr_wc.spouse_rmd,
                        "Tax (NC)": yr_nc.federal_tax_amt,
                        "Tax (WC)": yr_wc.federal_tax_amt,
                        "Tax Saved": yr_nc.federal_tax_amt - yr_wc.federal_tax_amt,
                    }
                )
        if rmd_years:
            df = pd.DataFrame(rmd_years)
            for col in (
                "IRA+Roth (NC)",
                "IRA+Roth (WC)",
                "RMD (NC)",
                "RMD (WC)",
                "Tax (NC)",
                "Tax (WC)",
                "Tax Saved",
            ):
                df[col] = df[col].apply(fmt_dollars)
            st.dataframe(
                df,
                width="stretch",
                hide_index=True,
            )
