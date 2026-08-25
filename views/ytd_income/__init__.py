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

    # audit-0823 M1: save ONLY when session_state actually holds a real
    # "ytd_snapshot" -- never when the returned `ytd` is nothing but
    # render_manual_entry_partial's `st.session_state.get("ytd_snapshot",
    # YTDSnapshot())` fallback default (e.g. manual entry off with no prior
    # snapshot: a "Reset to demo", or any session that never loaded/synced
    # one). In that case nothing writes session_state["ytd_snapshot"] during
    # this render either, so an unconditional save_ytd_snapshot(ytd) below
    # would silently overwrite a real .ytd_cache.json on disk with a blank
    # snapshot on the very first render. A real write DOES land in
    # session_state during this render whenever manual entry or a sync
    # actually produced a value, so checking presence AFTER render (not
    # before) still saves legitimate first-time entries -- this deliberately
    # does not invent a "looks empty" heuristic on field values.
    if "ytd_snapshot" in st.session_state:
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
