"""Portfolio (FinExtract sync + accounts/holdings tables) Setup-domain
partial (Task 6 of the ui-shell-theme-toggle plan).

Split out of the original flat ``views/setup/_partials.py`` when that module
grew to ~980 lines (pure mechanical reorganization, no behavior change).
Named ``_portfolio.py`` within the ``_partials`` package — distinct from the
sibling top-level ``views/setup/portfolio.py`` module (different package
path: ``views.setup._partials._portfolio`` vs ``views.setup.portfolio``),
which this module imports from (deferred, see ``render_portfolio_partial``).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from engine.data_bridge_browser import is_pyodide
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
from models.household import Household


def _no_data_msg(noun: str) -> str:
    """Return an empty-state message adapted for the current runtime environment."""
    if is_pyodide():
        return f"No {noun} loaded — upload a data file in ⚙️ Setup → \U0001f517 Data Bridge."
    return f"No {noun} loaded — use the Sync button below (local install) or upload a data file."


def _render_accounts_table(accounts: list[AccountSummary], container, *, show_owner: bool) -> None:
    """Render a read-only accounts dataframe, or an info banner when empty.

    ``container`` is threaded through (not hard-coded to ``st``) so callers
    can pass a tab/expander sub-container — see ``_render_portfolio_sub_tabs``.
    """
    if not accounts:
        container.info(_no_data_msg("accounts"))
        return
    rows = [
        {
            "account_name": a.account_name,
            "type": a.account_type,
            "market_value": a.total_value,
            **({"owner": a.owner} if show_owner else {}),
        }
        for a in accounts
    ]
    container.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "market_value": st.column_config.NumberColumn("Market Value", format="$%,.0f"),
        },
    )


def _render_holdings_table(accounts: list[AccountSummary], container) -> None:
    """Render a read-only holdings dataframe across the given accounts.

    ``container`` is threaded through (not hard-coded to ``st``) so callers
    can pass a tab/expander sub-container — see ``_render_portfolio_sub_tabs``.
    """
    rows = [
        {
            "symbol": h.symbol,
            "account": h.account_name,
            "asset_class": h.asset_class,
            "quantity": h.quantity,
            "market_value": h.market_value,
        }
        for a in accounts
        for h in a.holdings
    ]
    if not rows:
        container.info(_no_data_msg("holdings"))
        return
    container.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "quantity": st.column_config.NumberColumn("Quantity", format="%,.0f"),
            "market_value": st.column_config.NumberColumn("Market Value", format="$%,.0f"),
        },
    )


def _render_portfolio_sub_tabs(
    snap: PortfolioSnapshot | None,
    container,
) -> None:
    """Render Me / Spouse / All sub-tabs for the Portfolio tab.

    ``container.tabs(...)`` (not ``st.tabs(...)``) so the tabs themselves
    nest correctly inside whatever container the caller hands us (Task 8
    may drop ``render_portfolio_partial`` inside an outer expander/tab).
    Content inside each returned tab uses that tab object directly (not
    bare ``st.``) for the same reason.
    """
    me_tab, spouse_tab, all_tab = container.tabs(["Me", "Spouse", "All"])

    if snap is None:
        for tab in (me_tab, spouse_tab, all_tab):
            tab.info(_no_data_msg("accounts"))
        return

    me_accounts = [a for a in snap.accounts if a.owner == "you"]
    spouse_accounts = [a for a in snap.accounts if a.owner == "spouse"]

    me_tab.subheader("Accounts")
    _render_accounts_table(me_accounts, me_tab, show_owner=False)
    me_tab.subheader("Holdings")
    _render_holdings_table(me_accounts, me_tab)

    spouse_tab.subheader("Accounts")
    _render_accounts_table(spouse_accounts, spouse_tab, show_owner=False)
    spouse_tab.subheader("Holdings")
    _render_holdings_table(spouse_accounts, spouse_tab)

    all_tab.subheader("Accounts")
    _render_accounts_table(snap.accounts, all_tab, show_owner=True)
    all_tab.subheader("Holdings")
    _render_holdings_table(snap.accounts, all_tab)


def _render_account_type_overrides(snap: PortfolioSnapshot | None, container) -> None:
    """Render the Account Type Overrides expander.

    ``container.expander(...)`` (not ``st.expander(...)``) so the expander
    itself nests correctly inside whatever container the caller hands us;
    content inside uses the expander object directly (not bare ``st.``).
    """
    from engine.portfolio_sync import (
        _classify_account,
        _resolve_override,
    )

    expander = container.expander("🏷️ Account Type Overrides")
    if snap is None or not snap.accounts:
        expander.info("No accounts loaded — sync first to see detected acctIds.")
        return

    expander.caption("Changes take effect on next sync.")
    _type_options = ["trad_ira", "roth_ira", "brokerage", "hsa", "403b"]
    _owner_options = ["you", "spouse"]
    overrides: dict[str, str | dict[str, str]] = st.session_state.get("account_type_overrides") or {}

    seen: set[str] = set()
    for acct in snap.accounts:
        acct_id = acct.account_name
        if acct_id in seen:
            continue
        seen.add(acct_id)

        auto_type, _ = _classify_account(acct_id)
        existing = overrides.get(acct_id)
        if existing is not None:
            current_type, current_owner = _resolve_override(existing)
            if not current_type:
                current_type = auto_type
        else:
            current_type, current_owner = auto_type, "you"
        try:
            type_idx = _type_options.index(current_type)
        except ValueError:
            type_idx = 0
        try:
            owner_idx = _owner_options.index(current_owner)
        except ValueError:
            owner_idx = 0

        col_id, col_auto, col_type, col_owner = expander.columns([3, 2, 2, 2])
        col_id.text(acct_id)
        col_auto.caption(f"auto: {auto_type}")
        chosen_type = col_type.selectbox(
            "Type",
            _type_options,
            index=type_idx,
            key=f"_override_type_{acct_id}",
            label_visibility="collapsed",
        )
        chosen_owner = col_owner.selectbox(
            "Owner",
            _owner_options,
            index=owner_idx,
            key=f"_override_owner_{acct_id}",
            label_visibility="collapsed",
        )
        # Write through the nested form so owner is persisted alongside type.
        if "account_type_overrides" not in st.session_state:
            st.session_state["account_type_overrides"] = {}
        st.session_state["account_type_overrides"][acct_id] = {
            "type": chosen_type,
            "owner": chosen_owner,
        }


def render_portfolio_partial(hh: Household, container) -> None:
    """Render the Portfolio tab's FinExtract sync button, read-only
    accounts/holdings tables, and the Account Type Overrides expander.

    Moved verbatim from ``views/setup/portfolio.py:render_portfolio_tab``
    (Task 6 of the ui-shell-theme-toggle plan) — the rest of that function's
    body (the equity-grants table + stock-price widget) was already
    extracted into ``render_options_partial`` in Task 5.

    None of these fields are Command-Center-governed sourced fields (no
    ``HOUSEHOLD_SCALAR_FIELDS`` entry), so unlike ``render_accounts_partial``/
    ``render_options_partial`` there is no inline trust/manual/confirm
    governance card here: the accounts/holdings tables are pure read-only
    display, and the balances the sync button fetches get their own
    governance cards elsewhere (``render_accounts_partial``/
    ``render_options_partial``) on the NEXT render, once
    ``app.get_household()`` records the freshly-saved snapshot as
    FINEXTRACT_LIVE candidates — unchanged from pre-Task-6 behavior.

    ``sync_portfolio_from_finextract`` is imported locally (deferred, not at
    module level) to avoid a circular import: ``views/setup/portfolio.py``
    imports ``render_portfolio_partial``/``render_options_partial`` from this
    module at module level, so a module-level import back from here would
    form an import cycle. Mirrors the same deferred-import pattern already
    used by ``views/_shared.py:_sync_portfolio_source``.
    """
    snap: PortfolioSnapshot | None = st.session_state.get("portfolio_snapshot")

    # FinExtract availability note — local install only (not available on Pyodide/stlite)
    if is_pyodide():
        container.info(
            "FinExtract sync isn't available on the public site — browsers block "
            "the HTTPS page from reaching your local FinExtract server "
            "(http://127.0.0.1:7890), no matter how it's running. "
            "Bring in real data instead via ⚙️ Setup → \U0001f517 Data Bridge "
            "(encrypted upload from a local install)."
        )
    else:
        container.caption(
            "FinExtract sync works here because the app is running locally "
            "(`pixi run app`), with your local FinExtract server running."
        )

    if not is_pyodide():
        from views.setup.portfolio import sync_portfolio_from_finextract

        _sync = container.button(
            "Sync from FinExtract", help="Pull live holdings from ingestion server"
        )
        if snap is not None:
            container.caption(
                f"Loaded: {len(snap.accounts)} accounts, {len(snap.equity_grants)} grants"
            )

        if _sync:
            # NOTE: synced balances (your_ira/spouse_ira/your_roth/spouse_roth)
            # are deliberately NOT written to session_state here. get_household()
            # records this snapshot as FINEXTRACT_LIVE candidates and arbitrates
            # them through the freeze-until-confirm gate (Setup ▸ Command
            # Center) — a direct write here bypassed that gate (audit defect).
            outcome = sync_portfolio_from_finextract(hh)
            snap = outcome.snap
            if snap.server_available:
                container.success(
                    f"Synced: {len(snap.accounts)} accounts, "
                    f"{len(snap.equity_grants)} active grants"
                    + (", YTD income" if outcome.ytd_synced else "")
                    + (", dividend history" if outcome.dividend_history_synced else "")
                    + (", option exercises" if outcome.option_exercises_synced else "")
                )
            else:
                container.error(f"Server unavailable: {snap.error}")
                snap = st.session_state.get("portfolio_snapshot")

    _render_portfolio_sub_tabs(snap, container)
    _render_account_type_overrides(snap, container)
