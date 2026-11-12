"""Income Control — read-only: room left under each tax/cost line, per year, after
the active plan (issue #123, slice 1). Changes nothing; levers arrive in later slices.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.income_control import (
    IncomeControlResult,
    IncomeControlYear,
    build_income_control,
)
from engine.room_lines import LINE_KEYS
from models.household import Household
from views._format import DOLLAR_TICKFORMAT, DOLLAR_TICKFORMATSTOPS, fmt_dollars, md_dollars
from views._style import render_page_header
from views.plan_source import active_plan

PICKER_KEY = "income_control_year"

LINE_LABELS: dict[str, str] = {
    "top_12": "Top of 12% bracket",
    "top_22": "Top of 22% bracket",
    "top_24": "Top of 24% bracket",
    "ltcg_zero": "0% LTCG limit",
    "irmaa_t1": "IRMAA tier 1",
    "aca_cliff": "ACA cliff",
    "niit": "NIIT threshold",
}
LINE_COLORS: dict[str, str] = {
    "top_12": "#22c55e",
    "top_22": "#eab308",
    "top_24": "#f97316",
    "ltcg_zero": "#14b8a6",
    "irmaa_t1": "#ef4444",
    "aca_cliff": "#a855f7",
    "niit": "#64748b",
}
_FIXED = ("Fixed income", "#94a3b8")
_FORCED = ("Forced exercise", "#7c3aed")
_DISC = ("Discretionary exercise", "#a78bfa")
_CONV = ("Planned conversion", "#34d399")


# ---------------------------------------------------------------------------
# Pure helpers -- testable without Streamlit
# ---------------------------------------------------------------------------


def default_year(result: IncomeControlResult) -> int:
    """First year with any crossed line, else the base year."""
    for row in result.years:
        if row.crossed_lines:
            return row.year
    return result.window_start


def tightest_first(row: IncomeControlYear) -> list[tuple[str, float]]:
    """Applicable lines as (key, room), smallest room first (crossed lines lead)."""
    rooms = [(key, room) for key, room in row.rooms.items() if room is not None]
    return sorted(rooms, key=lambda kr: (kr[1], LINE_KEYS.index(kr[0])))


def chart_layers(result: IncomeControlResult) -> list[tuple[str, list[float], str]]:
    """Stacked-bar layers; the forced layer is dropped when no year has forced exercise."""
    rows = result.years
    layers: list[tuple[str, list[float], str]] = [
        (_FIXED[0], [r.fixed_income for r in rows], _FIXED[1]),
    ]
    if any(r.forced_exercise for r in rows):
        layers.append((_FORCED[0], [r.forced_exercise for r in rows], _FORCED[1]))
    layers.append((_DISC[0], [r.discretionary_exercise for r in rows], _DISC[1]))
    layers.append((_CONV[0], [r.planned_conversion for r in rows], _CONV[1]))
    return layers


def line_heights(result: IncomeControlResult, key: str) -> list[float | None]:
    """Height each year's bar would have to reach to touch ``key``: total + room."""
    heights: list[float | None] = []
    for row in result.years:
        room = row.rooms.get(key)
        heights.append(None if room is None else row.total_income + room)
    return heights


def _binding_name(row: IncomeControlYear) -> str:
    return LINE_LABELS[row.binding_line] if row.binding_line else "-"


def _ages_text(row: IncomeControlYear) -> str:
    return " / ".join(str(age) for age in row.ages if age is not None)


def table_rows(result: IncomeControlResult) -> list[dict[str, object]]:
    """One display row per year for the all-years table."""
    out: list[dict[str, object]] = []
    for row in result.years:
        entry: dict[str, object] = {
            "Year": row.year,
            "Ages": _ages_text(row),
            "Fixed income": fmt_dollars(row.fixed_income),
            "Forced exercise": fmt_dollars(row.forced_exercise),
            "Discretionary exercise": fmt_dollars(row.discretionary_exercise),
            "Planned conversion": fmt_dollars(row.planned_conversion),
        }
        for key in LINE_KEYS:
            room = row.rooms[key]
            entry[LINE_LABELS[key]] = "n/a" if room is None else fmt_dollars(room)
        entry["Binding line"] = _binding_name(row)
        out.append(entry)
    return out


def _hover_text(row: IncomeControlYear) -> str:
    crossed = ", ".join(LINE_LABELS[k] for k in row.crossed_lines) or "none"
    return f"Binding: {_binding_name(row)}<br>Crossed: {crossed}"


def build_figure(result: IncomeControlResult) -> go.Figure:
    years = [r.year for r in result.years]
    hover = [_hover_text(r) for r in result.years]
    fig = go.Figure()
    for name, values, color in chart_layers(result):
        fig.add_trace(
            go.Bar(
                x=years,
                y=values,
                name=name,
                marker_color=color,
                hovertext=hover,
                hovertemplate=f"{name}: $%{{y:,.0f}}<br>%{{hovertext}}<extra></extra>",
            )
        )
    for key in LINE_KEYS:
        heights = line_heights(result, key)
        if all(h is None for h in heights):
            continue
        fig.add_trace(
            go.Scatter(
                x=years,
                y=heights,
                name=LINE_LABELS[key],
                mode="lines",
                line={"color": LINE_COLORS[key], "dash": "dash", "shape": "hv", "width": 2},
                hovertemplate=f"{LINE_LABELS[key]}: $%{{y:,.0f}}<extra></extra>",
            )
        )
    fig.update_layout(
        barmode="stack",
        xaxis_title="Year",
        yaxis_title="Income vs. line height ($)",
        yaxis_tickformat=DOLLAR_TICKFORMAT,
        yaxis_tickformatstops=DOLLAR_TICKFORMATSTOPS,
        height=450,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
    )
    fig.update_xaxes(type="category")
    return fig


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _income_stack(row: IncomeControlYear) -> list[tuple[str, float]]:
    stack = [("Fixed income (SS, RMD, dividends)", row.fixed_income)]
    if row.forced_exercise:
        stack.append(("Forced exercise (grant expires)", row.forced_exercise))
    stack.append(("Discretionary exercise", row.discretionary_exercise))
    stack.append(("Planned conversion", row.planned_conversion))
    return stack


def _binding_note(row: IncomeControlYear) -> str:
    if row.binding_line:
        return f"Tightest line: {LINE_LABELS[row.binding_line]}."
    if row.crossed_lines:
        return "Every applicable line is already crossed."
    return "No line applies this year."


def _render_detail(row: IncomeControlYear) -> None:
    left, right = st.columns(2)
    with left:
        st.markdown(f"**{row.year}** · age {_ages_text(row)}")
        for label, value in _income_stack(row):
            st.markdown(md_dollars(f"{label}: {fmt_dollars(value)}"))
        st.markdown(md_dollars(f"**Total: {fmt_dollars(row.total_income)}**"))
    with right:
        for key, room in tightest_first(row):
            label = LINE_LABELS[key]
            if room < 0:
                st.markdown(md_dollars(f":red[{label}: over by {fmt_dollars(-room)}]"))
            else:
                st.markdown(md_dollars(f"{label}: {fmt_dollars(room)} left"))
        st.caption(_binding_note(row))


def render(hh: Household) -> None:
    render_page_header("control")
    plan, ytd, nii, plan_label = active_plan(hh)
    result = build_income_control(
        hh, plan, plan_label=plan_label, ytd=ytd, net_inv_income=float(nii)
    )
    st.caption(f"Room left under each line after {result.plan_label}")
    if not result.years:
        st.info("No years in the decision window for this household.")
        return
    st.caption(f"Decision window {result.window_start}–{result.window_end}")
    st.caption(
        "Realized gains and forecast interest are not yet included in these measures "
        "(later slices of the Income Control work)."
    )

    st.plotly_chart(build_figure(result), width="stretch")

    years = [row.year for row in result.years]
    if st.session_state.get(PICKER_KEY) not in years:
        st.session_state[PICKER_KEY] = default_year(result)
    picked = st.segmented_control("Year", years, key=PICKER_KEY, selection_mode="single")
    year = picked if picked in years else default_year(result)
    _render_detail(next(row for row in result.years if row.year == year))

    with st.expander("All years (table)", expanded=False):
        st.dataframe(pd.DataFrame(table_rows(result)), hide_index=True, width="stretch")
