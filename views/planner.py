"""Conversion Planner — interactive 20-year conversion grid.

Shows the conversion window (WINDOW_YEARS years from the starting age) with:
- Editable conversion amounts per year per spouse
- Live bracket/tax/room feedback
- IRA balance tracking
- Phase-based color coding
- QCD inputs for squeeze years
- Auto-fill buttons
"""

import plotly.graph_objects as go
import streamlit as st

from engine.scenario import ConversionPlan, run_scenario
from engine.scenario_autofill import auto_fill_12
from engine.scenario_compute import QCD_MIN_AGE
from engine.tax import BRACKETS_MFJ, BRACKETS_SINGLE
from engine.tax_indexing import index_value as _index_value
from models.household import Household
from views._format import FORM_8606_CAPTION, fmt_dollars, fmt_pct

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

WINDOW_YEARS = 20  # conversion grid horizon (years from the starting age)


def render(hh: Household):
    st.title("📋 Conversion Planner — 20-Year Grid")
    st.caption(
        "Set conversion amounts per year. Watch bracket room, taxes, and IRA balances update in real-time."
    )
    st.caption(FORM_8606_CAPTION)

    # --- Auto-fill buttons ---
    col_btn1, col_btn2 = st.columns(2)

    if "conv_plan_your" not in st.session_state:
        st.session_state.conv_plan_your = {}
        st.session_state.conv_plan_spouse = {}
        st.session_state.conv_plan_qcd = {}
        st.session_state.conv_plan_spouse_qcd = {}

    with col_btn1:
        if st.button("🎯 Auto-Fill to 12%", width="stretch"):
            plan = auto_fill_12(hh)
            st.session_state.conv_plan_your = plan.your_conversions
            st.session_state.conv_plan_spouse = plan.spouse_conversions
            st.session_state.conv_plan_qcd = plan.qcds
            st.session_state.conv_plan_spouse_qcd = plan.spouse_qcds
            for _k in list(st.session_state):
                if _k.startswith(("yc_", "sc_", "qcd_", "sp_qcd_")):
                    del st.session_state[_k]
            st.rerun()

    with col_btn2:
        if st.button("🗑️ Clear All", width="stretch"):
            st.session_state.conv_plan_your = {}
            st.session_state.conv_plan_spouse = {}
            st.session_state.conv_plan_qcd = {}
            st.session_state.conv_plan_spouse_qcd = {}
            for _k in list(st.session_state):
                if _k.startswith(("yc_", "sc_", "qcd_", "sp_qcd_")):
                    del st.session_state[_k]
            st.rerun()

    # --- Build and run scenario ---
    plan = ConversionPlan(
        your_conversions=dict(st.session_state.conv_plan_your),
        spouse_conversions=dict(st.session_state.conv_plan_spouse),
        qcds=dict(st.session_state.conv_plan_qcd),
        spouse_qcds=dict(st.session_state.conv_plan_spouse_qcd),
    )
    result = run_scenario(hh, plan, "Custom", end_age=95)

    # Filter to the conversion window (WINDOW_YEARS years from the starting age)
    conv_window = [yr for yr in result.years if yr.your_age <= hh.your_age + WINDOW_YEARS - 1]

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
    hdr_cols = st.columns([1, 0.6, 0.6, 1.5, 1.2, 1.5, 1.5, 1, 1, 1.2, 1.2, 1, 1.2, 1.2])
    headers = [
        "Year",
        "You",
        "Sp",
        "Your IRA",
        "Options",
        "Your Conv",
        "Sp Conv",
        "QCD",
        "Sp QCD",
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
        you_can_conv = ya < hh.your_rmd_start_age
        sp_can_conv = sa < hh.spouse_rmd_start_age

        cols = st.columns([1, 0.6, 0.6, 1.5, 1.2, 1.5, 1.5, 1, 1, 1.2, 1.2, 1, 1.2, 1.2])

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
        cols[3].markdown(fmt_dollars(yr.your_ira_begin))
        cols[4].markdown(fmt_dollars(yr.option_income) if yr.option_income > 0 else "—")

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
            cols[5].markdown("*RMD era*" if ya >= hh.your_rmd_start_age else "—")

        # Spouse conversion input
        if sp_can_conv:
            sc_key = f"sc_{yr.year}"
            sc_val = st.session_state.conv_plan_spouse.get(yr.year, 0)
            new_sc = cols[6].number_input(
                f"sc{yr.year}",
                value=int(sc_val),
                step=5000,
                min_value=0,
                max_value=int(yr.spouse_ira_begin),
                label_visibility="collapsed",
                key=sc_key,
            )
            if new_sc != sc_val:
                st.session_state.conv_plan_spouse[yr.year] = new_sc
        else:
            cols[6].markdown("—")

        # Your QCD input  # IRC §408(d)(8)(B): QCD eligible at 70½
        if ya >= QCD_MIN_AGE:
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

        # Spouse QCD input  # IRC §408(d)(8)(B): QCD eligible at 70½
        if sa >= QCD_MIN_AGE:
            sp_qcd_key = f"sp_qcd_{yr.year}"
            sp_qcd_val = st.session_state.conv_plan_spouse_qcd.get(yr.year, 0)
            new_sp_qcd = cols[8].number_input(
                f"sp_qcd{yr.year}",
                value=int(sp_qcd_val),
                step=5000,
                min_value=0,
                max_value=int(hh.qcd_limit),
                label_visibility="collapsed",
                key=sp_qcd_key,
            )
            if new_sp_qcd != sp_qcd_val:
                st.session_state.conv_plan_spouse_qcd[yr.year] = new_sp_qcd
        else:
            cols[8].markdown("—")

        # Computed columns
        cols[9].markdown(fmt_dollars(yr.combined_gross))

        # Bracket with color
        br_pct = yr.marginal_bracket * 100
        br_color = "green" if br_pct <= 12 else ("orange" if br_pct <= 22 else "red")
        cols[10].markdown(f":{br_color}[**{br_pct:.0f}%**]")

        cols[11].markdown(fmt_dollars(yr.conversion_tax) if yr.conversion_tax > 0 else "—")

        # Room with color
        r12 = yr.room_12
        r12_color = "green" if r12 > 50_000 else ("orange" if r12 > 0 else "red")
        cols[12].markdown(f":{r12_color}[{fmt_dollars(r12)}]")

        r22 = yr.room_22
        r22_color = "green" if r22 > 50_000 else ("orange" if r22 > 0 else "red")
        cols[13].markdown(f":{r22_color}[{fmt_dollars(r22)}]")

    # --- Totals ---
    st.markdown("---")
    total_yc = sum(yr.your_conversion for yr in conv_window)
    total_sc = sum(yr.spouse_conversion for yr in conv_window)
    total_tax = sum(yr.conversion_tax for yr in conv_window)

    tcol1, tcol2, tcol3, tcol4 = st.columns(4)
    tcol1.metric("Your Total Conv", fmt_dollars(total_yc))
    tcol2.metric("Spouse Total Conv", fmt_dollars(total_sc))
    tcol3.metric("Combined Conv", fmt_dollars(total_yc + total_sc))
    tcol4.metric(
        "Total Conv Tax",
        fmt_dollars(total_tax),
        f"Avg rate: {fmt_pct(total_tax / max(total_yc + total_sc, 1))}",
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
            y=[
                yr.your_ira_begin + yr.spouse_ira_begin + yr.your_roth_begin + yr.spouse_roth_begin
                for yr in no_conv.years
            ],
            name="No Conversion",
            line={"color": "#ef4444", "width": 2, "dash": "dash"},
        )
    )
    fig.add_trace(
        go.Scatter(
            x=all_ages,
            y=[
                yr.your_ira_begin + yr.spouse_ira_begin + yr.your_roth_begin + yr.spouse_roth_begin
                for yr in result.years
            ],
            name="Your Plan",
            line={"color": "#22c55e", "width": 3},
        )
    )
    fig.add_vline(
        x=hh.your_rmd_start_age,
        line_dash="dot",
        line_color="gray",
        annotation_text="RMDs begin",
    )

    fig.update_layout(
        xaxis_title="Your Age",
        yaxis_title="IRA + Roth Balance ($)",
        yaxis_tickformat="$,.0s",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig, width="stretch")

    # --- Bracket fill visualization ---
    st.markdown("### Bracket Usage by Year")
    fig_br = go.Figure()

    # Pre-aggregate one trace per income segment so the legend always shows
    # exactly 6 entries (one per segment) regardless of which years have values.
    _years = [yr.year for yr in conv_window]
    _segments = [
        ("Options",     [yr.option_income     for yr in conv_window], "#a78bfa"),
        ("Taxable RMD", [yr.taxable_rmd       for yr in conv_window], "#f87171"),
        ("Taxable SS",  [yr.taxable_ss_amt    for yr in conv_window], "#60a5fa"),
        ("Your Conv",   [yr.your_conversion   for yr in conv_window], "#34d399"),
        ("Sp Conv",     [yr.spouse_conversion for yr in conv_window], "#f472b6"),
        ("Room (12%)",  [yr.room_12           for yr in conv_window], "#1e293b"),
    ]
    for name, vals, color in _segments:
        fig_br.add_trace(
            go.Bar(
                x=_years,
                y=vals,
                name=name,
                marker_color=color,
                showlegend=True,
                hovertemplate=f"{name}: $%{{y:,.0f}}<extra>%{{x}}</extra>",
            )
        )

    # Add 12% ceiling line (CPI-indexed, per-year filing-status-aware).
    # Uses yr.filing_status (not hh.filing_status) so post-death Single years
    # in a SurvivorScenario get BRACKETS_SINGLE[1][0] (~$50,400) instead of
    # the MFJ value (~$100,800), which would overstate conversion headroom.
    _cpi = hh.cpi_assumption
    ceil_12_values = [
        yr.total_deductions
        + _index_value(
            (BRACKETS_SINGLE if yr.filing_status == "Single" else BRACKETS_MFJ)[1][0],
            yr.year,
            _cpi,
        )
        for yr in conv_window
    ]
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
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
    )
    st.plotly_chart(fig_br, width="stretch")
