"""Portfolio tab — accounts/holdings tables, portfolio sub-tabs, account-type overrides.

The equity-grants table (and its ``GRANTS_KEY`` governance card) moved into
``views/setup/_partials.py:render_options_partial`` as of Task 5 of the
ui-shell-theme-toggle plan — it now renders once, below the Me/Spouse/All
sub-tabs, instead of being duplicated inside the Me and All sub-tabs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from engine.data_bridge_browser import (
    is_pyodide,
)
from engine.data_sources.record import record_magi_candidates
from engine.portfolio_sync import (
    AccountSummary,
    MagiSnapshot,
    PortfolioSnapshot,
    apply_dividends_rollup,
    apply_magi,
    apply_option_exercises,
    fetch_dividends_rollup,
    fetch_magi,
    fetch_option_exercises_with_cache,
    fetch_portfolio,
    fetch_ytd_snapshot,
    save_snapshot,
    save_ytd_snapshot,
)
from models.household import Household
from models.sourced import Source
from views.setup._partials import render_options_partial


def _no_data_msg(noun: str) -> str:
    """Return an empty-state message adapted for the current runtime environment."""
    if is_pyodide():
        return f"No {noun} loaded — upload a data file in ⚙️ Setup → \U0001f517 Data Bridge."
    return f"No {noun} loaded — use the Sync button below (local install) or upload a data file."


def _render_accounts_table(accounts: list[AccountSummary], *, show_owner: bool) -> None:
    """Render a read-only accounts dataframe, or an info banner when empty."""
    if not accounts:
        st.info(_no_data_msg("accounts"))
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
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "market_value": st.column_config.NumberColumn("Market Value", format="$%,.0f"),
        },
    )


def _render_holdings_table(accounts: list[AccountSummary]) -> None:
    """Render a read-only holdings dataframe across the given accounts."""
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
        st.info(_no_data_msg("holdings"))
        return
    st.dataframe(
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
) -> None:
    """Render Me / Spouse / All sub-tabs for the Portfolio tab."""
    me_tab, spouse_tab, all_tab = st.tabs(["Me", "Spouse", "All"])

    if snap is None:
        for tab in (me_tab, spouse_tab, all_tab):
            with tab:
                st.info(_no_data_msg("accounts"))
        return

    me_accounts = [a for a in snap.accounts if a.owner == "you"]
    spouse_accounts = [a for a in snap.accounts if a.owner == "spouse"]

    with me_tab:
        st.subheader("Accounts")
        _render_accounts_table(me_accounts, show_owner=False)
        st.subheader("Holdings")
        _render_holdings_table(me_accounts)

    with spouse_tab:
        st.subheader("Accounts")
        _render_accounts_table(spouse_accounts, show_owner=False)
        st.subheader("Holdings")
        _render_holdings_table(spouse_accounts)

    with all_tab:
        st.subheader("Accounts")
        _render_accounts_table(snap.accounts, show_owner=True)
        st.subheader("Holdings")
        _render_holdings_table(snap.accounts)


def _render_account_type_overrides(snap: PortfolioSnapshot | None) -> None:
    """Render the Account Type Overrides expander."""
    from engine.portfolio_sync import (
        _classify_account,
        _resolve_override,
    )

    with st.expander("🏷️ Account Type Overrides"):
        if snap is None or not snap.accounts:
            st.info("No accounts loaded — sync first to see detected acctIds.")
            return

        st.caption("Changes take effect on next sync.")
        _type_options = ["trad_ira", "roth_ira", "brokerage", "hsa", "403b"]
        _owner_options = ["you", "spouse"]
        overrides: dict[str, str | dict[str, str]] = (
            st.session_state.get("account_type_overrides") or {}
        )

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

            col_id, col_auto, col_type, col_owner = st.columns([3, 2, 2, 2])
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


@dataclass(frozen=True)
class PortfolioSyncOutcome:
    """Outcome of one FinExtract portfolio+MAGI+YTD sync pass.

    Extracted from the "Sync from FinExtract" button handler (W2 Part B) so
    ``views._shared.sync_everything`` can drive the identical fetch/save/
    candidate-record sequence without duplicating it. Balances
    (your_ira/spouse_ira/your_roth/spouse_roth/txn_price_now/grants) are
    deliberately NOT written to session_state or recorded as candidates
    here — ``app.get_household()`` records the saved snapshot as
    FINEXTRACT_LIVE candidates via ``record_snapshot_candidates`` on the
    next render (unchanged from prior behavior); ``sync_everything`` records
    them immediately via the same helper so they land pending right away.
    """

    snap: PortfolioSnapshot
    magi_candidates_recorded: int
    ytd_synced: bool
    dividend_history_synced: bool
    option_exercises_synced: bool


def sync_portfolio_from_finextract(hh: Household) -> PortfolioSyncOutcome:
    """Fetch + save a FinExtract portfolio snapshot; record MAGI candidates; sync YTD.

    Reproduces exactly what the "Sync from FinExtract" button used to do
    inline. When the server is unavailable, returns immediately with an
    all-false/zero outcome (the caller inspects ``outcome.snap.server_available``
    / ``outcome.snap.error``).
    """
    snap = fetch_portfolio(
        account_type_overrides=st.session_state.get("account_type_overrides") or None,
    )
    if not snap.server_available:
        return PortfolioSyncOutcome(
            snap=snap,
            magi_candidates_recorded=0,
            ytd_synced=False,
            dividend_history_synced=False,
            option_exercises_synced=False,
        )

    # Merge dividend history into holdings before saving snapshot
    div_rollup = fetch_dividends_rollup()
    if div_rollup.server_available:
        snap = apply_dividends_rollup(snap, div_rollup)
    save_snapshot(snap)
    st.session_state.portfolio_snapshot = snap

    # MAGI 2-year history from FinExtract (IRMAA lookback anchor). Records
    # candidates for Command Center review instead of gap-filling
    # session_state directly (audit defect #2).
    magi_recorded = 0
    try:
        plan_year = datetime.now(UTC).year
        magi_snap = MagiSnapshot(fetched_at=datetime.now(UTC))
        for offset in (1, 2):  # batchTaxYear-1 and batchTaxYear-2 (2-year coverage shipped)
            apply_magi(magi_snap, fetch_magi(plan_year - offset))
        if magi_snap.prior_year_magi:
            magi_recorded = record_magi_candidates(
                magi_snap.prior_year_magi,
                Source.FINEXTRACT_LIVE,
                "FinExtract tax return",
                datetime.now(),
            )
    except Exception:  # noqa: BLE001 — sync is best-effort, never block on MAGI failure
        pass

    # Also sync YTD income data
    ytd_snap = fetch_ytd_snapshot()
    # Phase: option exercises — prefer cache equity_sales, fall back to /query
    exercises = fetch_option_exercises_with_cache(snap)
    if exercises.server_available:
        ytd_snap = apply_option_exercises(ytd_snap, exercises, hh)
        if exercises.captured_at:
            st.session_state["exercises_captured_at"] = exercises.captured_at
    ytd_synced = bool(ytd_snap.snapshot_date)
    if ytd_synced:
        st.session_state.ytd_snapshot = ytd_snap
        save_ytd_snapshot(ytd_snap)

    return PortfolioSyncOutcome(
        snap=snap,
        magi_candidates_recorded=magi_recorded,
        ytd_synced=ytd_synced,
        dividend_history_synced=div_rollup.server_available,
        option_exercises_synced=exercises.server_available,
    )


def render_portfolio_tab(hh: Household) -> None:
    """Extracted from setup.py render() — portfolio tab body."""
    snap: PortfolioSnapshot | None = st.session_state.get("portfolio_snapshot")

    # FinExtract availability note — local install only (not available on Pyodide/stlite)
    if is_pyodide():
        st.info(
            "FinExtract sync isn't available on the public site — browsers block "
            "the HTTPS page from reaching your local FinExtract server "
            "(http://127.0.0.1:7890), no matter how it's running. "
            "Bring in real data instead via ⚙️ Setup → \U0001f517 Data Bridge "
            "(encrypted upload from a local install)."
        )
    else:
        st.caption(
            "FinExtract sync works here because the app is running locally "
            "(`pixi run app`), with your local FinExtract server running."
        )

    if not is_pyodide():
        _sync = st.button("Sync from FinExtract", help="Pull live holdings from ingestion server")
        if snap is not None:
            st.caption(f"Loaded: {len(snap.accounts)} accounts, {len(snap.equity_grants)} grants")

        if _sync:
            # NOTE: synced balances (your_ira/spouse_ira/your_roth/spouse_roth)
            # are deliberately NOT written to session_state here. get_household()
            # records this snapshot as FINEXTRACT_LIVE candidates and arbitrates
            # them through the freeze-until-confirm gate (Setup ▸ Command
            # Center) — a direct write here bypassed that gate (audit defect).
            outcome = sync_portfolio_from_finextract(hh)
            snap = outcome.snap
            if snap.server_available:
                st.success(
                    f"Synced: {len(snap.accounts)} accounts, "
                    f"{len(snap.equity_grants)} active grants"
                    + (", YTD income" if outcome.ytd_synced else "")
                    + (", dividend history" if outcome.dividend_history_synced else "")
                    + (", option exercises" if outcome.option_exercises_synced else "")
                )
            else:
                st.error(f"Server unavailable: {snap.error}")
                snap = st.session_state.get("portfolio_snapshot")

    _render_portfolio_sub_tabs(snap)
    _render_account_type_overrides(snap)
    render_options_partial(hh, st)
