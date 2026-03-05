"""Conversion Planner — interactive 20-year conversion grid.

Shows all 20 years of the spouse's conversion window with:
- Editable conversion amounts per year per spouse
- Live bracket/tax/room feedback
- IRA balance tracking
- Phase-based color coding
- QCD inputs for squeeze years
- Auto-fill buttons
"""

import plotly.graph_objects as go
import streamlit as st

from engine.scenario import ConversionPlan, auto_fill_12, run_scenario
from models.household import Household

PHASE_COLORS = {
    "options": "#7c3aed",  # purple
    "clean": "#22c55e",  # green
    "ss_conv": "#3b82f6",  # blue
    "squeeze": "#ef4444",  # red
    "rmd": "#6b7280",  # gray
}

PHASE_LABELS = {
    "options": "🟣 Options + Conv",
    "clean": "🟢 Clean Conversion",
    "ss_conv": "🔵 SS + Conversion",
    "squeeze": "🔴 RMD Squeeze",
    "rmd": "⚪ RMD Only",
}


def render(hh: Household):
    st.title("📋 Conversion Planner — 20-Year Grid")
    st.caption(
        "Set conversion amounts per year. Watch bracket room, taxes, and IRA balances update in real-time."
    )

    # --- Auto-fill buttons ---
    col_btn1, col_btn2, col_btn3, col_btn4 = st.columns(4)

    if "conv_plan_your" not in st.session_state:
        st.session_state.conv_plan_your = {}
        st.session_state.conv_plan_spouse = {}
        st.session_state.conv_plan_qcd = {}

    with col_btn1:
        if st.button("🎯 Auto-Fill to 12%", width=True):
            plan = auto_fill_12(hh)
            st.session_state.conv_plan_your = plan.your_conversions
            st.session_state.conv_plan_spouse = plan.spouse_conversions
            st.session_state.conv_plan_qcd = plan.qcds
            st.rerun()

    with col_btn2:
        if st.button("🗑️ Clear All", width=True):
            st.session_state.conv_plan_your = {}
            st.session_state.conv_plan_spouse = {}
            st.session_state.conv_plan_qcd = {}
            st.rerun()

    # --- Build and run scenario ---
    plan = ConversionPlan(
        your_conversions=dict(st.session_state.conv_plan_your),
        spouse_conversions=dict(st.session_state.conv_plan_spouse),
        qcds=dict(st.session_state.conv_plan_qcd),
    )
    result = run_scenario(hh, plan, "Custom", end_age=95)

    # Filter to spouse's conversion window (20 years)
    conv_window = [yr for yr in result.years if yr.your_age <= 80]

    # --- Phase legend ---
    phases_present = {yr.phase for yr in conv_window}
    legend = " · ".join(
        PHASE_LABELS.get(p, p)
        for p in ["options", "clean", "ss_conv", "squeeze", "rmd"]
        if p in phases_present
    )
    st.markdown(f"**Phases:** {legend}")

    # --- Interactive grid ---
    st.markdown("### Conversion Grid")
    st.caption(
        "Enter amounts in the Your Conv / Sp Conv columns. Yellow = editable, gray = blocked."
    )

    # We'll use columns for a compact layout
    # Header row
    hdr_cols = st.columns([1, 0.6, 0.6, 1.5, 1.2, 1.5, 1.5, 1, 1.2, 1.2, 1, 1.2, 1.2])
    headers = [
        "Year",
        "You",
        "Sp",
        "Your IRA",
        "Options",
        "Your Conv",
        "Sp Conv",
        "QCD",
        "Gross",
        "Bracket",
        "Conv Tax",
        "Room 12%",
        "Room 22%",
    ]
    for col, h in zip(hdr_cols, headers, strict=False):
        col.markdown(f"**{h}**")

    # Data rows
    for yr in conv_window:
        ya, sa = yr.your_age, yr.spouse_age
        you_can_conv = ya <= 74
        sp_can_conv = 60 <= sa <= 74
        qcd_ok = ya >= 71

        cols = st.columns([1, 0.6, 0.6, 1.5, 1.2, 1.5, 1.5, 1, 1.2, 1.2, 1, 1.2, 1.2])

        # Phase color indicator
        phase_emoji = {
            "options": "🟣",
            "clean": "🟢",
            "ss_conv": "🔵",
            "squeeze": "🔴",
            "rmd": "⚪",
        }.get(yr.phase, "")

        cols[0].markdown(f"{phase_emoji} {yr.year}")
        cols[1].markdown(f"**{ya}**")
        cols[2].markdown(f"**{sa}**")
        cols[3].markdown(f"${yr.your_ira_begin:,.0f}")
        cols[4].markdown(f"${yr.option_income:,.0f}" if yr.option_income > 0 else "—")

        # Your conversion input
        if you_can_conv:
            yc_key = f"yc_{yr.year}"
            yc_val = st.session_state.conv_plan_your.get(yr.year, 0)
            new_yc = cols[5].number_input(
                f"yc{yr.year}",
                value=int(yc_val),
                step=5000,
                min_value=0,
                max_value=int(yr.your_ira_begin),
                label_visibility="collapsed",
                key=yc_key,
            )
            if new_yc != yc_val:
                st.session_state.conv_plan_your[yr.year] = new_yc
        else:
            cols[5].markdown("*RMD era*" if ya >= 75 else "—")

        # Spouse conversion input
        if sp_can_conv:
            sc_key = f"sc_{yr.year}"
            sc_val = st.session_state.conv_plan_spouse.get(yr.year, 0)
            new_sc = cols[6].number_input(
                f"sc{yr.year}",
                value=int(sc_val),
                step=5000,
                min_value=0,
                label_visibility="collapsed",
                key=sc_key,
            )
            if new_sc != sc_val:
                st.session_state.conv_plan_spouse[yr.year] = new_sc
        elif sa < 60:
            cols[6].markdown(f"*Sp {sa}<60*")
        else:
            cols[6].markdown("—")

        # QCD input
        if qcd_ok and ya >= 75:
            qcd_key = f"qcd_{yr.year}"
            qcd_val = st.session_state.conv_plan_qcd.get(yr.year, 0)
            new_qcd = cols[7].number_input(
                f"qcd{yr.year}",
                value=int(qcd_val),
                step=5000,
                min_value=0,
                max_value=int(hh.qcd_limit),
                label_visibility="collapsed",
                key=qcd_key,
            )
            if new_qcd != qcd_val:
                st.session_state.conv_plan_qcd[yr.year] = new_qcd
        else:
            cols[7].markdown("—")

        # Computed columns
        cols[8].markdown(f"${yr.combined_gross:,.0f}")

        # Bracket with color
        br_pct = yr.marginal_bracket * 100
        br_color = "green" if br_pct <= 12 else ("orange" if br_pct <= 22 else "red")
        cols[9].markdown(f":{br_color}[**{br_pct:.0f}%**]")

        cols[10].markdown(f"${yr.conversion_tax:,.0f}" if yr.conversion_tax > 0 else "—")

        # Room with color
        r12 = yr.room_12
        r12_color = "green" if r12 > 50_000 else ("orange" if r12 > 0 else "red")
        cols[11].markdown(f":{r12_color}[${r12:,.0f}]")

        r22 = yr.room_22
        r22_color = "green" if r22 > 50_000 else ("orange" if r22 > 0 else "red")
        cols[12].markdown(f":{r22_color}[${r22:,.0f}]")

    # --- Totals ---
    st.markdown("---")
    total_yc = sum(yr.your_conversion for yr in conv_window)
    total_sc = sum(yr.spouse_conversion for yr in conv_window)
    total_tax = sum(yr.conversion_tax for yr in conv_window)

    tcol1, tcol2, tcol3, tcol4 = st.columns(4)
    tcol1.metric("Your Total Conv", f"${total_yc:,.0f}")
    tcol2.metric("Spouse Total Conv", f"${total_sc:,.0f}")
    tcol3.metric("Combined Conv", f"${total_yc + total_sc:,.0f}")
    tcol4.metric(
        "Total Conv Tax",
        f"${total_tax:,.0f}",
        f"Avg rate: {total_tax / max(total_yc + total_sc, 1) * 100:.1f}%",
    )

    # --- IRA Trajectory Chart ---
    st.markdown("### IRA Balance Over Time")

    from engine.scenario import run_no_conversion

    no_conv = run_no_conversion(hh, end_age=95)

    fig = go.Figure()
    all_ages = [yr.your_age for yr in result.years]
    fig.add_trace(
        go.Scatter(
            x=all_ages,
            y=[yr.your_ira_begin + yr.spouse_ira_begin for yr in no_conv.years],
            name="No Conversion",
            line={"color": "#ef4444", "width": 2, "dash": "dash"},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=all_ages,
            y=[yr.your_ira_begin + yr.spouse_ira_begin for yr in result.years],
            name="Your Plan",
            line={"color": "#22c55e", "width": 3},
        )
    )
    fig.add_vline(x=75, line_dash="dot", line_color="gray", annotation_text="RMDs begin")

    fig.update_layout(
        xaxis_title="Your Age",
        yaxis_title="Combined IRA ($)",
        yaxis_tickformat="$,.0s",
        template="plotly_dark",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig, width=True)

    # --- Bracket fill visualization ---
    st.markdown("### Bracket Usage by Year")
    fig_br = go.Figure()

    for yr in conv_window:
        # Stacked bar: option_income, your_conv, sp_conv, SS, RMD, room_12, room_22
        segs = []
        if yr.option_income > 0:
            segs.append(("Options", yr.option_income, "#a78bfa"))
        if yr.taxable_rmd > 0:
            segs.append(("Taxable RMD", yr.taxable_rmd, "#f87171"))
        if yr.taxable_ss_amt > 0:
            segs.append(("Taxable SS", yr.taxable_ss_amt, "#60a5fa"))
        if yr.your_conversion > 0:
            segs.append(("Your Conv", yr.your_conversion, "#34d399"))
        if yr.spouse_conversion > 0:
            segs.append(("Sp Conv", yr.spouse_conversion, "#f472b6"))
        if yr.room_12 > 0:
            segs.append(("Room (12%)", yr.room_12, "#1e293b"))

        for name, val, color in segs:
            fig_br.add_trace(
                go.Bar(
                    x=[yr.year],
                    y=[val],
                    name=name,
                    marker_color=color,
                    showlegend=(yr == conv_window[0]),
                    hovertemplate=f"{name}: $%{{y:,.0f}}<extra>{yr.year}</extra>",
                )
            )

    # Add 12% ceiling line
    ceil_12_values = [yr.total_deductions + 100_800 for yr in conv_window]
    fig_br.add_trace(
        go.Scatter(
            x=[yr.year for yr in conv_window],
            y=ceil_12_values,
            name="12% Ceiling",
            line={"color": "#22c55e", "width": 2, "dash": "dash"},
            mode="lines",
        )
    )

    fig_br.update_layout(
        barmode="stack",
        xaxis_title="Year",
        yaxis_title="Gross Income ($)",
        yaxis_tickformat="$,.0s",
        template="plotly_dark",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
    )
    st.plotly_chart(fig_br, width=True)
