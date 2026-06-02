"""Dashboard page — the 'is this worth it?' overview.

Shows:
1. IRA trajectory: Convert vs Don't (combined both spouses)
2. Annual tax comparison
3. Cumulative net benefit over time
4. Key metrics at ages 80/85/90/95
"""

import plotly.graph_objects as go
import streamlit as st

from engine.scenario import auto_fill_12, run_no_conversion, run_scenario
from models.household import Household


def render(hh: Household):
    st.title("📊 Dashboard — Is Converting Worth It?")
    st.caption(
        f"You {hh.your_age} / Spouse {hh.spouse_age} · "
        f"IRAs ${hh.your_ira / 1e6:.1f}M + ${hh.spouse_ira / 1e6:.1f}M · "
        f"{hh.growth_rate * 100:.0f}% growth · RMD at {hh.rmd_start_age}"
    )

    # --- Run both scenarios ---
    no_conv = run_no_conversion(hh, end_age=95)
    plan_12 = auto_fill_12(hh)
    with_conv = run_scenario(hh, plan_12, "Fill 12%", end_age=95)

    # --- Build data ---
    ages = [yr.your_age for yr in no_conv.years]
    [yr.year for yr in no_conv.years]

    # Combined IRA
    ira_nc = [yr.your_ira_begin + yr.spouse_ira_begin for yr in no_conv.years]
    ira_wc = [yr.your_ira_begin + yr.spouse_ira_begin for yr in with_conv.years]

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

    # Brokerage balance
    [yr.brokerage_balance for yr in no_conv.years]
    [yr.brokerage_balance for yr in with_conv.years]

    # --- Top metrics ---
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        conv_total = with_conv.total_your_conv + with_conv.total_spouse_conv
        st.metric(
            "Total Converted",
            f"${conv_total / 1e6:.2f}M",
            f"You ${with_conv.total_your_conv / 1e6:.2f}M + Sp ${with_conv.total_spouse_conv / 1e6:.2f}M",
        )

    with col2:
        st.metric(
            "Conversion Tax Paid",
            f"${with_conv.total_conv_tax:,.0f}",
            f"Avg rate: {with_conv.total_conv_tax / max(conv_total, 1) * 100:.1f}%",
        )

    with col3:
        tax_saved = no_conv.total_rmd_tax - with_conv.total_rmd_tax
        st.metric(
            "RMD Tax Saved (75-95)",
            f"${tax_saved:,.0f}",
            f"${no_conv.total_rmd_tax:,.0f} → ${with_conv.total_rmd_tax:,.0f}",
        )

    with col4:
        brok_saved = no_conv.total_brok_tax - with_conv.total_brok_tax
        net = tax_saved + brok_saved - with_conv.total_conv_tax
        st.metric(
            "Net Lifetime Benefit", f"${net:,.0f}", f"{'Positive ✓' if net > 0 else 'Negative ✗'}"
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
            hovertemplate="Age %{x}<br>IRA: $%{y:,.0f}<extra>No Conversion</extra>",
        )
    )
    fig_ira.add_trace(
        go.Scatter(
            x=ages,
            y=ira_wc,
            name="With Conversion (12%)",
            line={"color": "#22c55e", "width": 3},
            hovertemplate="Age %{x}<br>IRA: $%{y:,.0f}<extra>With Conversion</extra>",
        )
    )
    # RMD start line
    fig_ira.add_vline(x=75, line_dash="dot", line_color="gray", annotation_text="RMDs begin")
    fig_ira.update_layout(
        title="Combined IRA Trajectory (Both Spouses)",
        xaxis_title="Your Age",
        yaxis_title="IRA Balance ($)",
        yaxis_tickformat="$,.0s",

        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig_ira, use_container_width=True)

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
        st.plotly_chart(fig_tax, use_container_width=True)

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
        st.plotly_chart(fig_cum, use_container_width=True)

    # --- Chart 4: Net Benefit Over Time ---
    net_benefit = []
    cum_conv_tax = 0
    cum_rmd_savings = 0
    cum_brok_savings = 0
    for yr_nc, yr_wc in zip(no_conv.years, with_conv.years, strict=False):
        cum_conv_tax = with_conv.total_conv_tax  # sunk cost paid during conv years
        cum_rmd_savings += (
            yr_nc.federal_tax_amt - yr_wc.federal_tax_amt if yr_nc.your_age >= 75 else 0
        )
        cum_brok_savings += yr_nc.brokerage_gain_tax - yr_wc.brokerage_gain_tax
        net_benefit.append(cum_rmd_savings + cum_brok_savings - cum_conv_tax)

    fig_net = go.Figure()
    ["#ef4444" if v < 0 else "#22c55e" for v in net_benefit]
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
    fig_net.add_vline(x=75, line_dash="dot", line_color="gray", annotation_text="RMDs begin")

    # Find break-even age
    breakeven = None
    for _i, (a, nb) in enumerate(zip(ages, net_benefit, strict=False)):
        if nb >= 0 and a >= 75:
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
        title="Net Benefit: Conversion Tax Paid vs RMD + Brokerage Tax Saved",
        xaxis_title="Your Age",
        yaxis_title="Net Benefit ($)",
        yaxis_tickformat="$,.0s",

        height=400,
    )
    st.plotly_chart(fig_net, use_container_width=True)

    # --- Summary table ---
    st.markdown("### Key Age Milestones")
    milestones = [
        (75, "RMDs begin"),
        (80, "5 yrs of RMDs"),
        (85, "10 yrs of RMDs"),
        (90, "15 yrs of RMDs"),
        (95, "20 yrs of RMDs"),
    ]
    cols = st.columns(len(milestones))
    for col, (age, label) in zip(cols, milestones, strict=False):
        yr_nc = next((yr for yr in no_conv.years if yr.your_age == age), None)
        yr_wc = next((yr for yr in with_conv.years if yr.your_age == age), None)
        nb_idx = age - hh.your_age
        nb = net_benefit[nb_idx] if nb_idx < len(net_benefit) else 0
        with col:
            st.markdown(f"**Age {age}** (Sp {age - hh.age_gap})")
            st.caption(label)
            if yr_nc and yr_wc:
                st.markdown(
                    f"IRA (NC): **${(yr_nc.your_ira_begin + yr_nc.spouse_ira_begin) / 1e6:.1f}M**"
                )
                st.markdown(
                    f"IRA (WC): **${(yr_wc.your_ira_begin + yr_wc.spouse_ira_begin) / 1e6:.1f}M**"
                )
                st.markdown(f"RMD (NC): ${yr_nc.your_rmd:,.0f}")
                st.markdown(f"RMD (WC): ${yr_wc.your_rmd:,.0f}")
                color = "green" if nb > 0 else "red"
                st.markdown(f"Net: :{color}[**${nb:,.0f}**]")

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
                        "Bracket": f"{yr.marginal_bracket * 100:.0f}%",
                        "Conv Tax": yr.conversion_tax,
                        "Room 12%": yr.room_12,
                        "Room 22%": yr.room_22,
                    }
                )
        if conv_years:
            df = pd.DataFrame(conv_years)
            st.dataframe(
                df.style.format(
                    {
                        "Options": "${:,.0f}",
                        "Your Conv": "${:,.0f}",
                        "Sp Conv": "${:,.0f}",
                        "Gross": "${:,.0f}",
                        "Taxable": "${:,.0f}",
                        "Conv Tax": "${:,.0f}",
                        "Room 12%": "${:,.0f}",
                        "Room 22%": "${:,.0f}",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    # --- RMD detail table ---
    with st.expander("📋 RMD Year Detail (Ages 75-95)"):
        import pandas as pd

        rmd_years = []
        for yr_nc, yr_wc in zip(no_conv.years, with_conv.years, strict=False):
            if yr_nc.your_age >= 75:
                rmd_years.append(
                    {
                        "Year": yr_nc.year,
                        "You": yr_nc.your_age,
                        "Sp": yr_nc.spouse_age,
                        "IRA (NC)": yr_nc.your_ira_begin + yr_nc.spouse_ira_begin,
                        "IRA (WC)": yr_wc.your_ira_begin + yr_wc.spouse_ira_begin,
                        "RMD (NC)": yr_nc.your_rmd,
                        "RMD (WC)": yr_wc.your_rmd,
                        "Tax (NC)": yr_nc.federal_tax_amt,
                        "Tax (WC)": yr_wc.federal_tax_amt,
                        "Tax Saved": yr_nc.federal_tax_amt - yr_wc.federal_tax_amt,
                    }
                )
        if rmd_years:
            df = pd.DataFrame(rmd_years)
            st.dataframe(
                df.style.format(
                    {
                        "IRA (NC)": "${:,.0f}",
                        "IRA (WC)": "${:,.0f}",
                        "RMD (NC)": "${:,.0f}",
                        "RMD (WC)": "${:,.0f}",
                        "Tax (NC)": "${:,.0f}",
                        "Tax (WC)": "${:,.0f}",
                        "Tax Saved": "${:,.0f}",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )
