"""Top-level views.setup package — render() dispatches to per-tab modules."""

from __future__ import annotations

import streamlit as st

from models.household import Household

from .data_bridge import render_data_bridge_tab
from .parameters import (
    _FILING_STATUS_OPTIONS,
    _render_pdf_1040_import,
    filing_status_from_label,
    render_parameters_tab,
    spouse_single_overrides,
)
from .portfolio import render_portfolio_tab


def render(hh: Household) -> None:
    """Render the Setup page — household parameters, sync, and data bridge."""
    st.title("⚙️ Setup")

    tab_params, tab_portfolio, tab_bridge = st.tabs(
        ["📊 Parameters", "💼 Portfolio", "🔗 Data bridge"]
    )
    with tab_params:
        render_parameters_tab(hh)
    with tab_portfolio:
        render_portfolio_tab(hh)
    with tab_bridge:
        render_data_bridge_tab(hh)


__all__ = [
    "_FILING_STATUS_OPTIONS",
    "_render_pdf_1040_import",
    "filing_status_from_label",
    "render",
    "render_data_bridge_tab",
    "render_parameters_tab",
    "render_portfolio_tab",
    "spouse_single_overrides",
]
