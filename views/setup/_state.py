"""Shared session-state helpers — user defaults, portfolio snapshot, personal-state clear."""

from __future__ import annotations

import streamlit as st

from engine.portfolio_sync import (
    AccountSummary,
    EquityGrant,
    Holding,
    PortfolioSnapshot,
    merge_snapshots,
)
from engine.upload_merge import build_user_defaults_session_updates


def _build_user_defaults_session_updates(data: dict, *, as_spouse: bool) -> dict:
    """Compute session_state updates from a .user_defaults.json payload.

    Thin wrapper around :func:`engine.upload_merge.build_user_defaults_session_updates`.
    Pure function — returns a ``{session_key: value}`` dict without writing to state.
    See the engine module for full mapping rules.
    """
    return build_user_defaults_session_updates(data, as_spouse=as_spouse)


def _apply_user_defaults_to_session(data: dict, *, as_spouse: bool = False) -> None:
    """Write JSON user-defaults keys into st.session_state.

    When ``as_spouse=True``, cross-maps the file's ``your_*`` fields to the
    receiver's ``spouse_*`` slots and ignores joint / grant fields.
    See :func:`_build_user_defaults_session_updates` for the mapping rules.

    Note for the spouse path: ``get_household()`` reads grant_strikes via
    ``_user_grant_strikes`` from session_state; ``as_spouse=True`` deliberately
    skips that key so the receiver's own grants stay authoritative.
    """
    for key, val in _build_user_defaults_session_updates(data, as_spouse=as_spouse).items():
        st.session_state[key] = val


def _user_defaults_from_session() -> dict:
    """Inverse of _apply_user_defaults_to_session: read session_state → JSON dict."""
    # Mirror of _apply_user_defaults_to_session scalar_keys
    scalar_keys = [
        "your_age",
        "spouse_age",
        "your_ira",
        "spouse_ira",
        "your_ss_fra",
        "spouse_ss_fra",
        "your_ss_start_age",
        "spouse_ss_start_age",
        "your_rmd_start_age",
        "spouse_rmd_start_age",
        "your_fra_age",
        "spouse_fra_age",
        "living_expenses",
        "stock_price_now",
        "aca_benchmark_premium_annual",
        "aca_enhanced_subsidies_active",
        "advance_aptc_annual",
        "medicare_part_b_base_monthly",
        "cpi_assumption",
    ]
    payload: dict = {}
    for k in scalar_keys:
        # Reverse the alias: session stores txn_price, JSON schema expects stock_price_now
        sess_key = "txn_price" if k == "stock_price_now" else k
        if sess_key in st.session_state:
            payload[k] = st.session_state[sess_key]
    strikes = st.session_state.get("_user_grant_strikes")
    if strikes:
        payload["grant_strikes"] = strikes
    overrides = st.session_state.get("account_type_overrides")
    if overrides:
        payload["account_type_overrides"] = overrides
    prior_magi = st.session_state.get("prior_year_magi")
    if prior_magi:
        payload["prior_year_magi"] = {str(k): v for k, v in prior_magi.items()}
    survivor = st.session_state.get("survivor")
    if survivor:
        payload["survivor"] = survivor
    inherited_iras = st.session_state.get("inherited_iras")
    if inherited_iras:
        payload["inherited_iras"] = inherited_iras
    return payload


def _portfolio_snapshot_from_dict(data: dict) -> object:
    """Reconstruct a PortfolioSnapshot from its asdict() JSON form."""
    accounts = []
    for acc_d in data.get("accounts", []):
        holdings = [Holding(**h) for h in acc_d.get("holdings", [])]
        acc_d_clean = {k: v for k, v in acc_d.items() if k != "holdings"}
        accounts.append(AccountSummary(holdings=holdings, **acc_d_clean))
    grants = [EquityGrant(**g) for g in data.get("equity_grants", [])]
    return PortfolioSnapshot(
        accounts=accounts,
        equity_grants=grants,
        txn_shares_held=data.get("txn_shares_held", 0),
        txn_shares_value=data.get("txn_shares_value", 0.0),
        server_available=data.get("server_available", False),
        error=data.get("error"),
    )


def _apply_portfolio_snapshot(incoming: object, *, as_spouse: bool) -> None:
    """Merge a freshly-parsed portfolio snapshot into the session.

    Thin wrapper around :func:`engine.portfolio_sync.merge_snapshots` that
    reads / writes ``st.session_state['portfolio_snapshot']``.
    """
    existing = st.session_state.get("portfolio_snapshot")
    merged = merge_snapshots(existing, incoming, as_spouse=as_spouse)  # type: ignore[arg-type]
    st.session_state["portfolio_snapshot"] = merged


def _clear_personal_session_state() -> None:
    """Reset personal-mode session state to demo defaults."""
    keys_to_clear = [
        "portfolio_snapshot",
        "_user_grant_strikes",
        "your_age",
        "spouse_age",
        "your_ira",
        "spouse_ira",
        "your_ss_fra",
        "spouse_ss_fra",
        "your_ss_start_age",
        "spouse_ss_start_age",
        "your_rmd_start_age",
        "spouse_rmd_start_age",
        "your_fra_age",
        "spouse_fra_age",
        "living_expenses",
        "txn_price",
        "aca_benchmark_premium_annual",
        "aca_enhanced_subsidies_active",
        "advance_aptc_annual",
        "medicare_part_b_base_monthly",
        "cpi_assumption",
        "prior_year_magi",
        "survivor",
        "inherited_iras",
    ]
    for k in keys_to_clear:
        st.session_state.pop(k, None)
    st.session_state.pop("_seeded", None)  # force re-seed from synthetic
