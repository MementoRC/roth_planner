"""YTD Income Tracker & Conversion Headroom Calculator.

Shows real-world mid-year income events (stop-loss triggers, wages, etc.)
and computes remaining headroom for Roth conversions against bracket,
IRMAA, NIIT, and ACA thresholds.

Key insight: LTCG consumes IRMAA/NIIT room but NOT ordinary bracket room.
"""

from datetime import datetime

import streamlit as st

from engine.data_status import compute_ytd_completeness
from engine.portfolio_sync import save_ytd_snapshot
from models.household import Household
from models.ytd_income import YTDSnapshot
from views.ytd_income._partials import (
    render_analysis_partial,
    render_manual_entry_partial,
    render_sync_scan_partial,
)


def render(hh: Household, theme: str | None = None) -> None:
    st.title("YTD Income & Conversion Headroom")
    st.caption(
        "Track mid-year income events and see how much Roth conversion room remains. "
        "LTCG from stop-loss triggers consumes IRMAA room but leaves bracket room intact."
    )

    _snapshot_for_badge = st.session_state.get("ytd_snapshot", YTDSnapshot())
    _completeness = compute_ytd_completeness(_snapshot_for_badge, now=datetime.now())
    if _completeness.issues:
        st.caption(f"⚠️ {_completeness.issues[0].detail}")

    _theme = theme if theme is not None else st.session_state.get("ui_theme", "Classic")
    ytd = _render_domains(hh) if _theme == "Domains" else _render_classic(hh)

    save_ytd_snapshot(ytd)


def _render_classic(hh: Household) -> YTDSnapshot:
    render_sync_scan_partial(hh)
    ytd = render_manual_entry_partial(hh)
    render_analysis_partial(hh, ytd)
    return ytd


def _render_domains(hh: Household) -> YTDSnapshot:
    tab1, tab2 = st.tabs(["Update Your Data", "Review Headroom"])
    with tab1:
        render_sync_scan_partial(hh)
        ytd = render_manual_entry_partial(hh)
    with tab2:
        render_analysis_partial(hh, ytd)
    return ytd
