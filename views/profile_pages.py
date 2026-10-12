"""Profile pages (redesign PR 3b): Household & Accounts, Assumptions, Holdings.

Split out of the six-tab Setup page (the Domains shell, deleted in 3b). Each page composes the existing views/setup/_partials and ends with ONE
autosave_user_defaults(): it must run last so it sees this run's edits
(audit-0823 M2), and it is safe on a subset of partials because
_user_defaults_from_session presence-guards every key.
"""

from __future__ import annotations

import streamlit as st

from models.household import Household
from views._shared import edit_link_button, render_completeness_badge
from views._style import render_page_header
from views.portfolio import (
    render_account_overview,
    render_allocation,
    render_grant_comparison,
    render_growth_mapping,
    render_stock_shares,
)
from views.setup._partials import (
    _render_portfolio_sub_tabs,
    render_accounts_partial,
    render_assumptions_partial,
    render_household_partial,
    render_options_partial,
    render_portfolio_legacy,
    render_ssa_finextract_sync,
    render_stock_price_widget,
)
from views.setup._state import autosave_user_defaults


def render_household(hh: Household) -> None:
    render_page_header("household")
    st.subheader("Filing status")
    _is_single = bool(render_household_partial(hh, st, "joint"))
    col_you, col_spouse = st.columns(2)
    col_you.subheader("Me")
    render_household_partial(hh, col_you, "your")
    col_spouse.subheader("Spouse")
    if _is_single:
        col_spouse.info(
            "Single filer — spouse inputs are disabled and treated as zero. "
            "Switch Filing status to Married filing jointly to re-enable."
        )
    render_household_partial(hh, col_spouse, "spouse")

    st.header("Accounts")
    col_you, col_spouse = st.columns(2)
    col_you.subheader("Me")
    render_accounts_partial(hh, col_you, "your")
    col_spouse.subheader("Spouse")
    render_accounts_partial(hh, col_spouse, "spouse")
    with st.expander("Legacy: FinExtract", expanded=False):
        st.caption("Kept while FinExtract is retired. PDF statements are the primary source.")
        c_you, c_spouse = st.columns(2)
        render_ssa_finextract_sync(c_you, "you")
        render_ssa_finextract_sync(c_spouse, "spouse")
    autosave_user_defaults()


def render_assumptions(hh: Household) -> None:
    render_page_header("assumptions")
    render_assumptions_partial(hh, st)
    # Stock price + growth moved here from Setup › Data (user, 2026-10-04): the
    # one place for rates and forward guesses. "Sync everything" on Review
    # Changes still refreshes the quote through its governance card.
    render_stock_price_widget(st.container(border=True))
    autosave_user_defaults()


def render_holdings(hh: Household) -> None:
    render_page_header("holdings")
    render_completeness_badge(hh)
    snap = st.session_state.get("portfolio_snapshot")
    # Any loaded snapshot renders, including a bundle-imported one
    # (server_available is False there). No gate on it.
    if snap is None:
        st.info("No holdings loaded yet. Import a brokerage statement to fill this page.")
        edit_link_button(key="holdings_to_import", page="import", label="Go to Import Statements →")
    else:
        render_account_overview(snap)
        _render_portfolio_sub_tabs(snap, st)  # Me / Spouse / All
        render_allocation(snap)
        render_options_partial(hh, st)  # the one grants table
        render_grant_comparison(snap, hh)
        render_stock_shares(snap)
        render_growth_mapping(snap, hh)
    # Outside the snapshot branch: a local user with no snapshot loads one here.
    with st.expander("Legacy: FinExtract", expanded=False):
        st.caption("Kept while FinExtract is retired. PDF statements are the primary source.")
        render_portfolio_legacy(hh, st)
    autosave_user_defaults()
