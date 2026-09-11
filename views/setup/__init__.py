"""Top-level views.setup package — render() dispatches to per-tab modules."""

from __future__ import annotations

import streamlit as st

from models.household import Household

from ._partials import filing_status_from_label
from ._state import autosave_user_defaults
from .command_center import render_command_center
from .data_bridge import render_data_bridge_tab
from .parameters import (
    _FILING_STATUS_OPTIONS,
    _render_pdf_1040_import,
    render_parameters_tab,
)
from .portfolio import render_portfolio_tab


def render(hh: Household) -> None:
    """Render the Setup page — review gate, household parameters, sync, and data bridge."""
    st.title("⚙️ Setup")

    tab_command_center, tab_params, tab_portfolio, tab_bridge = st.tabs(
        ["🎛️ Command Center", "📊 Parameters", "💼 Portfolio", "🔗 Data bridge"]
    )
    with tab_command_center:
        render_command_center(hh)
    with tab_params:
        render_parameters_tab(hh)
    with tab_portfolio:
        render_portfolio_tab(hh)
    with tab_bridge:
        render_data_bridge_tab(hh)

    # audit-0823 models-views/M5: must run LAST, after all four tabs have
    # rendered. This used to sit at the end of render_parameters_tab (tab 2),
    # which runs BEFORE the Portfolio tab (tab 3) instantiates its unkeyed
    # stock-price widget (`st.session_state.txn_price = st.number_input(...)`
    # in views/setup/_partials/_options.py) -- so an edit there was read by
    # the NEXT rerun's autosave, not this one, and was persisted one rerun
    # late. Same position, and same reason, as the Domains/Hub/Wizard shells.
    autosave_user_defaults()


__all__ = [
    "_FILING_STATUS_OPTIONS",
    "_render_pdf_1040_import",
    "filing_status_from_label",
    "render",
    "render_command_center",
    "render_data_bridge_tab",
    "render_parameters_tab",
    "render_portfolio_tab",
]
