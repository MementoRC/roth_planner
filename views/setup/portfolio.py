"""Portfolio tab — accounts/holdings tables, grants, portfolio sub-tabs, account-type overrides."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from engine.data_bridge_browser import (
    is_pyodide,
)
from engine.portfolio_sync import (
    AccountSummary,
    EquityGrant,
    MagiSnapshot,
    PortfolioSnapshot,
    apply_dividends_rollup,
    apply_magi,
    apply_option_exercises,
    fetch_dividends_rollup,
    fetch_magi,
    fetch_option_exercises_with_cache,
    fetch_portfolio,
    fetch_tax_return,
    fetch_ytd_snapshot,
    save_snapshot,
    save_tax_snapshot,
    save_ytd_snapshot,
)
from engine.upload_merge import derive_ira_balances, derive_roth_balances
from models.household import Household


def _render_accounts_table(accounts: list[AccountSummary], *, show_owner: bool) -> None:
    """Render a read-only accounts dataframe, or an info banner when empty."""
    if not accounts:
        st.info("No accounts loaded — click Sync above.")
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
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


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
        st.info("No holdings loaded — click Sync above.")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_grants_section(grants: list[EquityGrant]) -> None:
    """Render equity grants as a dataframe, or an info banner when empty."""
    if not grants:
        st.info("No grants loaded.")
        return
    rows = [
        {
            "grant_id": g.grant_id,
            "type": g.grant_type,
            "grant_date": g.grant_date,
            "shares_granted": g.shares_granted,
            "outstanding": g.outstanding,
            "current_value": g.current_value,
        }
        for g in grants
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_portfolio_sub_tabs(
    snap: PortfolioSnapshot | None,
) -> None:
    """Render Me / Spouse / All sub-tabs for the Portfolio tab."""
    me_tab, spouse_tab, all_tab = st.tabs(["Me", "Spouse", "All"])

    if snap is None:
        for tab in (me_tab, spouse_tab, all_tab):
            with tab:
                st.info("No accounts loaded — click Sync above.")
        return

    me_accounts = [a for a in snap.accounts if a.owner == "you"]
    spouse_accounts = [a for a in snap.accounts if a.owner == "spouse"]

    with me_tab:
        st.subheader("Accounts")
        _render_accounts_table(me_accounts, show_owner=False)
        st.subheader("Holdings")
        _render_holdings_table(me_accounts)
        st.subheader("Grants")
        _render_grants_section(snap.equity_grants)

    with spouse_tab:
        st.subheader("Accounts")
        _render_accounts_table(spouse_accounts, show_owner=False)
        st.subheader("Holdings")
        _render_holdings_table(spouse_accounts)
        st.subheader("Grants")
        _render_grants_section([])

    with all_tab:
        st.subheader("Accounts")
        _render_accounts_table(snap.accounts, show_owner=True)
        st.subheader("Holdings")
        _render_holdings_table(snap.accounts)
        st.subheader("Grants")
        _render_grants_section(snap.equity_grants)


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


def render_portfolio_tab(hh: Household) -> None:
    """Extracted from setup.py render() — portfolio tab body."""
    snap: PortfolioSnapshot | None = st.session_state.get("portfolio_snapshot")

    # Sync button + status banner — local install only (not available on Pyodide/stlite)
    if is_pyodide():
        st.caption(
            "Live sync from FinExtract requires a local install. "
            "Use the V2 sealed upload widget on the Data bridge tab "
            "to bring data prepared from a local install instead."
        )
    else:
        _sync = st.button("Sync from FinExtract", help="Pull live holdings from ingestion server")
        if snap is not None:
            st.caption(f"Loaded: {len(snap.accounts)} accounts, {len(snap.equity_grants)} grants")

        if _sync:
            snap = fetch_portfolio(
                account_type_overrides=st.session_state.get("account_type_overrides") or None,
            )
            if snap.server_available:
                # Push synced balances into session state
                _your_ira, _spouse_ira = derive_ira_balances(snap)
                if _your_ira > 0:
                    st.session_state.your_ira = int(_your_ira)
                if _spouse_ira > 0:
                    st.session_state.spouse_ira = int(_spouse_ira)
                _your_roth, _spouse_roth = derive_roth_balances(snap)
                if _your_roth > 0:
                    st.session_state.your_roth = int(_your_roth)
                if _spouse_roth > 0:
                    st.session_state.spouse_roth = int(_spouse_roth)
                # Merge dividend history into holdings before saving snapshot
                div_rollup = fetch_dividends_rollup()
                if div_rollup.server_available:
                    snap = apply_dividends_rollup(snap, div_rollup)
                save_snapshot(snap)
                st.session_state.portfolio_snapshot = snap
                # Also sync tax return data
                tax_snap = fetch_tax_return()
                if tax_snap.server_available:
                    st.session_state.tax_return_snapshot = tax_snap
                    save_tax_snapshot(tax_snap)
                # A3: MAGI 2-year history from FinExtract (IRMAA lookback anchor)
                try:
                    plan_year = datetime.now(UTC).year
                    magi_snap = MagiSnapshot(fetched_at=datetime.now(UTC))
                    for offset in (
                        1,
                        2,
                    ):  # batchTaxYear-1 and batchTaxYear-2 (2-year coverage shipped)
                        apply_magi(magi_snap, fetch_magi(plan_year - offset))
                    if magi_snap.prior_year_magi:
                        existing = dict(st.session_state.get("prior_year_magi") or {})
                        # Gap-fill only: do NOT override manual entries
                        for yr, val in magi_snap.prior_year_magi.items():
                            if yr not in existing or not existing[yr]:
                                existing[yr] = val
                        st.session_state["prior_year_magi"] = existing
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
                if ytd_snap.snapshot_date:
                    st.session_state.ytd_snapshot = ytd_snap
                    save_ytd_snapshot(ytd_snap)
                st.success(
                    f"Synced: {len(snap.accounts)} accounts, "
                    f"{len(snap.equity_grants)} active grants"
                    + (", tax return data" if tax_snap.server_available else "")
                    + (", YTD income" if ytd_snap.snapshot_date else "")
                    + (", dividend history" if div_rollup.server_available else "")
                    + (", option exercises" if exercises.server_available else "")
                )
            else:
                st.error(f"Server unavailable: {snap.error}")
                snap = st.session_state.get("portfolio_snapshot")

    _render_portfolio_sub_tabs(snap)
    _render_account_type_overrides(snap)
