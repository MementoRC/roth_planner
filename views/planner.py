"""Conversion Planner — interactive 20-year conversion grid.

Shows the conversion window (WINDOW_YEARS years from the starting age) with:
- Editable conversion amounts per year per spouse via st.data_editor
- Live bracket/tax/room feedback
- IRA balance tracking
- Phase-based color coding
- QCD inputs for squeeze years
- Auto-fill buttons

Performance note: replaced a loop of ~300 individual Streamlit widgets with a
single st.data_editor call (audit-0706 w2, ui-primary-2).  Per-row constraints
(IRA balance cap, age-gating) are now enforced by post-edit validation in
apply_conversion_grid_edits() rather than at widget-render time.  The UX
tradeoff is that users can type any value; invalid entries are silently clamped
or zeroed and a warning banner explains what was adjusted.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.scenario import ConversionPlan, run_scenario
from engine.scenario_autofill import auto_fill_12
from engine.scenario_compute import QCD_MIN_AGE
from engine.tax import BRACKETS_MFJ, BRACKETS_SINGLE
from engine.tax_indexing import index_value as _index_value
from models.household import Household
from views._format import FORM_8606_CAPTION, fmt_dollars, fmt_pct
from views._shared import render_completeness_badge

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

# session_state key used for the data_editor widget; cleared on auto-fill/clear
_GRID_KEY = "conv_grid_editor"


# ---------------------------------------------------------------------------
# Pure helper — testable without Streamlit
# ---------------------------------------------------------------------------


def should_refresh_grid(state_changed: bool, edit_warnings: list[str]) -> bool:
    """Decide whether the data_editor widget key must be cleared + the page
    rerun after a grid edit (C31, audit-0721).

    Must fire whenever `edit_warnings` is non-empty, NOT just when the
    resulting dict differs from what was already in session_state -- a clamp
    that happens to land back on the already-stored value still leaves the
    editor's cached raw (invalid) input on screen unless it is cleared.
    """
    return state_changed or bool(edit_warnings)


def apply_conversion_grid_edits(
    edited_df: pd.DataFrame,
    yr_rows: list[dict],
) -> tuple[dict[int, float], dict[int, float], dict[int, float], dict[int, float], list[str]]:
    """Validate and clamp conversion grid edits from st.data_editor.

    Parameters
    ----------
    edited_df:
        DataFrame returned by st.data_editor with columns:
        year (int), your_conv, sp_conv, qcd, sp_qcd (all float/int).
    yr_rows:
        List of dicts with per-year metadata used for constraint checking.
        Required keys per row: year, your_age, spouse_age, your_ira_begin,
        spouse_ira_begin, your_rmd_start_age, spouse_rmd_start_age.
        QCD age eligibility uses the engine constant QCD_MIN_AGE directly.

    Returns
    -------
    conv_your, conv_sp, qcd_out, sp_qcd_out : dict[int, float]
        Updated conversion/QCD amounts keyed by year (int).
        Years with zero value are omitted unless explicitly needed.
    warnings : list[str]
        Human-readable messages describing any clamps or zeroing applied.
    """
    # Build lookup from year -> yr_row metadata
    yr_lookup: dict[int, dict] = {int(r["year"]): r for r in yr_rows}

    conv_your: dict[int, float] = {}
    conv_sp: dict[int, float] = {}
    qcd_out: dict[int, float] = {}
    sp_qcd_out: dict[int, float] = {}
    warnings: list[str] = []

    for _, row in edited_df.iterrows():
        year = int(row["year"])
        meta = yr_lookup.get(year)
        if meta is None:
            continue

        ya: int = int(meta["your_age"])
        sa: int = int(meta["spouse_age"])
        your_ira_begin: float = float(meta["your_ira_begin"])
        spouse_ira_begin: float = float(meta["spouse_ira_begin"])
        your_rmd_start: int = int(meta["your_rmd_start_age"])
        spouse_rmd_start: int = int(meta["spouse_rmd_start_age"])

        # --- your_conv: block in RMD era, clamp to IRA balance ---
        raw_yc = max(0.0, float(row["your_conv"]))
        if ya >= your_rmd_start:
            if raw_yc > 0:
                warnings.append(
                    f"{year}: your conversion blocked (RMD era, age {ya} ≥ {your_rmd_start}); zeroed."
                )
            yc = 0.0
        elif raw_yc > your_ira_begin:
            warnings.append(
                f"{year}: your conversion clamped from {raw_yc:,.0f} to {your_ira_begin:,.0f} (IRA balance limit)."
            )
            yc = your_ira_begin
        else:
            yc = raw_yc
        if yc != 0.0:
            conv_your[year] = yc

        # --- sp_conv: block in RMD era, clamp to spouse IRA balance ---
        raw_sc = max(0.0, float(row["sp_conv"]))
        if sa >= spouse_rmd_start:
            if raw_sc > 0:
                warnings.append(
                    f"{year}: spouse conversion blocked (RMD era, age {sa} ≥ {spouse_rmd_start}); zeroed."
                )
            sc = 0.0
        elif raw_sc > spouse_ira_begin:
            warnings.append(
                f"{year}: spouse conversion clamped from {raw_sc:,.0f} to {spouse_ira_begin:,.0f} (IRA balance limit)."
            )
            sc = spouse_ira_begin
        else:
            sc = raw_sc
        if sc != 0.0:
            conv_sp[year] = sc

        # --- qcd: IRC §408(d)(8)(B): QCD eligible at 70½; engine uses QCD_MIN_AGE = 71 ---
        raw_qcd = max(0.0, float(row["qcd"]))
        if ya >= QCD_MIN_AGE:
            q = raw_qcd
        else:
            if raw_qcd > 0:
                warnings.append(
                    f"{year}: your QCD zeroed (age {ya} < QCD minimum age {QCD_MIN_AGE})."
                )
            q = 0.0
        if q != 0.0:
            qcd_out[year] = q

        # --- sp_qcd: IRC §408(d)(8)(B): QCD eligible at 70½; engine uses QCD_MIN_AGE = 71 ---
        raw_sp_qcd = max(0.0, float(row["sp_qcd"]))
        if sa >= QCD_MIN_AGE:
            sq = raw_sp_qcd
        else:
            if raw_sp_qcd > 0:
                warnings.append(
                    f"{year}: spouse QCD zeroed (age {sa} < QCD minimum age {QCD_MIN_AGE})."
                )
            sq = 0.0
        if sq != 0.0:
            sp_qcd_out[year] = sq

    return conv_your, conv_sp, qcd_out, sp_qcd_out, warnings


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(hh: Household) -> None:
    st.title("📋 Conversion Planner — 20-Year Grid")
    st.caption(
        "Set conversion amounts per year. Watch bracket room, taxes, and IRA balances update in real-time."
    )
    st.caption(FORM_8606_CAPTION)
    render_completeness_badge(hh)

    # --- Auto-fill buttons ---
    col_btn1, col_btn2 = st.columns(2)

    st.session_state.setdefault("conv_plan_your", {})
    st.session_state.setdefault("conv_plan_spouse", {})
    st.session_state.setdefault("conv_plan_qcd", {})
    st.session_state.setdefault("conv_plan_spouse_qcd", {})

    with col_btn1:
        if st.button("🎯 Auto-Fill to 12%", use_container_width=True):
            plan = auto_fill_12(hh)
            st.session_state.conv_plan_your = plan.your_conversions
            st.session_state.conv_plan_spouse = plan.spouse_conversions
            st.session_state.conv_plan_qcd = plan.qcds
            st.session_state.conv_plan_spouse_qcd = plan.spouse_qcds
            # Clear old per-widget keys (backward compat) and grid editor key
            for _k in list(st.session_state):
                if _k.startswith(("yc_", "sc_", "qcd_", "sp_qcd_")) or _k == _GRID_KEY:
                    del st.session_state[_k]
            st.rerun()

    with col_btn2:
        if st.button("🗑️ Clear All", use_container_width=True):
            st.session_state.conv_plan_your = {}
            st.session_state.conv_plan_spouse = {}
            st.session_state.conv_plan_qcd = {}
            st.session_state.conv_plan_spouse_qcd = {}
            for _k in list(st.session_state):
                if _k.startswith(("yc_", "sc_", "qcd_", "sp_qcd_")) or _k == _GRID_KEY:
                    del st.session_state[_k]
            st.rerun()

    # --- Build and run scenario ---
    plan = ConversionPlan(
        your_conversions=dict(st.session_state.conv_plan_your),
        spouse_conversions=dict(st.session_state.conv_plan_spouse),
        qcds=dict(st.session_state.conv_plan_qcd),
        spouse_qcds=dict(st.session_state.conv_plan_spouse_qcd),
    )
    # C26 (audit-0721): thread base-year YTD actuals through, mirroring the
    # apply_ytd_to_projection gating already used by sweet_spot.py/aca_irmaa.py
    # -- otherwise the YTD Income page's "Apply YTD to projections" toggle has
    # no effect on this page. run_scenario itself narrows ytd to the base year.
    _apply_ytd = st.session_state.get("apply_ytd_to_projection", False)
    _ytd = st.session_state.get("ytd_snapshot") if _apply_ytd else None
    result = run_scenario(hh, plan, "Custom", end_age=95, ytd=_ytd)

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

    # --- Build grid DataFrame for data_editor ---
    st.markdown("### Conversion Grid")
    st.caption(
        "Edit Your Conv / Sp Conv / QCD / Sp QCD columns. "
        "Values exceeding the IRA balance or entered outside the eligible age window "
        "are clamped or zeroed after editing, with a warning shown below the grid."
    )

    # Build per-year metadata for post-edit validation
    yr_rows: list[dict] = []
    grid_records: list[dict] = []
    for yr in conv_window:
        phase_emoji = {
            "options": "🟣",
            "clean": "🟢",
            "ss_conv": "🔵",
            "squeeze": "🔴",
            "rmd": "⚪",
        }.get(yr.phase, "")
        yr_rows.append(
            {
                "year": yr.year,
                "your_age": yr.your_age,
                "spouse_age": yr.spouse_age,
                "your_ira_begin": yr.your_ira_begin,
                "spouse_ira_begin": yr.spouse_ira_begin,
                "your_rmd_start_age": hh.your_rmd_start_age,
                "spouse_rmd_start_age": hh.spouse_rmd_start_age,
            }
        )
        grid_records.append(
            {
                "Year": f"{phase_emoji} {yr.year}",
                "year": yr.year,  # hidden integer key used by helper
                "You": yr.your_age,
                "Sp": yr.spouse_age,
                "Your IRA": yr.your_ira_begin,
                "Sp IRA": yr.spouse_ira_begin,
                "Options": yr.option_income,
                "your_conv": float(st.session_state.conv_plan_your.get(yr.year, 0)),
                "sp_conv": float(st.session_state.conv_plan_spouse.get(yr.year, 0)),
                "qcd": float(st.session_state.conv_plan_qcd.get(yr.year, 0)),
                "sp_qcd": float(st.session_state.conv_plan_spouse_qcd.get(yr.year, 0)),
                "Gross": yr.combined_gross,
                "Bracket": yr.marginal_bracket * 100,
                "Conv Tax": yr.conversion_tax,
                "Room 12%": yr.room_12,
                "Room 22%": yr.room_22,
            }
        )

    grid_df = pd.DataFrame(grid_records)

    edited_df = st.data_editor(
        grid_df,
        key=_GRID_KEY,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Year": st.column_config.TextColumn("Year", disabled=True),
            "year": st.column_config.NumberColumn("year", disabled=True),
            "You": st.column_config.NumberColumn("You", disabled=True, format="%d"),
            "Sp": st.column_config.NumberColumn("Sp", disabled=True, format="%d"),
            "Your IRA": st.column_config.NumberColumn(
                "Your IRA", disabled=True, format="$%,.0f"
            ),
            "Sp IRA": st.column_config.NumberColumn(
                "Sp IRA", disabled=True, format="$%,.0f"
            ),
            "Options": st.column_config.NumberColumn(
                "Options", disabled=True, format="$%,.0f"
            ),
            "your_conv": st.column_config.NumberColumn(
                "Your Conv",
                min_value=0,
                step=5000,
                format="$%,.0f",
                help="Your Roth conversion for this year (clamped to IRA balance after edit)",
            ),
            "sp_conv": st.column_config.NumberColumn(
                "Sp Conv",
                min_value=0,
                step=5000,
                format="$%,.0f",
                help="Spouse Roth conversion for this year (clamped to IRA balance after edit)",
            ),
            "qcd": st.column_config.NumberColumn(
                "QCD",
                min_value=0,
                step=5000,
                format="$%,.0f",
                help="Your Qualified Charitable Distribution (requires age ≥ 71)",
            ),
            "sp_qcd": st.column_config.NumberColumn(
                "Sp QCD",
                min_value=0,
                step=5000,
                format="$%,.0f",
                help="Spouse Qualified Charitable Distribution (requires age ≥ 71)",
            ),
            "Gross": st.column_config.NumberColumn(
                "Gross", disabled=True, format="$%,.0f"
            ),
            "Bracket": st.column_config.NumberColumn(
                "Bracket", disabled=True, format="%.0f%%"
            ),
            "Conv Tax": st.column_config.NumberColumn(
                "Conv Tax", disabled=True, format="$%,.0f"
            ),
            "Room 12%": st.column_config.NumberColumn(
                "Room 12%", disabled=True, format="$%,.0f"
            ),
            "Room 22%": st.column_config.NumberColumn(
                "Room 22%", disabled=True, format="$%,.0f"
            ),
        },
        column_order=[
            "Year", "You", "Sp", "Your IRA", "Sp IRA", "Options",
            "your_conv", "sp_conv", "qcd", "sp_qcd",
            "Gross", "Bracket", "Conv Tax", "Room 12%", "Room 22%",
        ],
    )

    # Post-edit validation: clamp + age-gate, write back to session_state
    # Extract just the editable columns needed by the helper
    helper_df = edited_df[["year", "your_conv", "sp_conv", "qcd", "sp_qcd"]].copy()
    conv_your, conv_sp, qcd_vals, sp_qcd_vals, edit_warnings = apply_conversion_grid_edits(
        helper_df, yr_rows
    )

    if edit_warnings:
        for msg in edit_warnings:
            st.warning(msg)

    # Update session_state only when values changed (avoids infinite rerun loop)
    new_your = conv_your
    new_sp = conv_sp
    new_qcd = qcd_vals
    new_sp_qcd = sp_qcd_vals

    state_changed = (
        new_your != dict(st.session_state.conv_plan_your)
        or new_sp != dict(st.session_state.conv_plan_spouse)
        or new_qcd != dict(st.session_state.conv_plan_qcd)
        or new_sp_qcd != dict(st.session_state.conv_plan_spouse_qcd)
    )

    if state_changed:
        st.session_state.conv_plan_your = new_your
        st.session_state.conv_plan_spouse = new_sp
        st.session_state.conv_plan_qcd = new_qcd
        st.session_state.conv_plan_spouse_qcd = new_sp_qcd

    # C31 (audit-0721): a clamp/zero correction that happens to land back on
    # the value ALREADY stored in session_state (state_changed == False) was
    # previously masked -- the grid-key clear + rerun lived INSIDE the
    # state_changed branch, so st.data_editor kept echoing the user's raw
    # (invalid) input instead of the clamped value. Refresh whenever
    # edit_warnings fired, independent of whether the dict comparison changed.
    if should_refresh_grid(state_changed, edit_warnings):
        if _GRID_KEY in st.session_state:
            del st.session_state[_GRID_KEY]
        st.rerun()

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

    # C26 follow-up (audit-0721): thread the same YTD gating into the
    # no-conversion baseline so the IRA trajectory chart stays consistent
    # with the YTD-aware "Custom" result above.
    no_conv = run_no_conversion(hh, end_age=95, ytd=_ytd)

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
            round50=True,
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
