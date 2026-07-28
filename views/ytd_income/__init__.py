"""YTD Income Tracker & Conversion Headroom Calculator.

Shows real-world mid-year income events (stop-loss triggers, wages, etc.)
and computes remaining headroom for Roth conversions against bracket,
IRMAA, NIIT, and ACA thresholds.

Key insight: LTCG consumes IRMAA/NIIT room but NOT ordinary bracket room.
"""

import streamlit as st

from engine.portfolio_sync import save_ytd_snapshot
from models.household import Household
from views.ytd_income._partials import (
    render_analysis_partial,
    render_manual_entry_partial,
    render_sync_scan_partial,
)


def _color_for_room(room: float) -> str:
    if room <= 0:
        return "inverse"  # red
    if room <= 50_000:
        return "off"  # orange-ish (streamlit uses "off" for warning-style)
    return "normal"  # green


def _metric_delta_color(room: float) -> str:
    if room <= 0:
        return "inverse"
    return "normal"


def render(hh: Household):
    st.title("YTD Income & Conversion Headroom")
    st.caption(
        "Track mid-year income events and see how much Roth conversion room remains. "
        "LTCG from stop-loss triggers consumes IRMAA room but leaves bracket room intact."
    )

    render_sync_scan_partial(hh)

    ytd = render_manual_entry_partial(hh)

    render_analysis_partial(hh, ytd)

    # Save snapshot for persistence
    save_ytd_snapshot(ytd)
