"""Shared session-state helpers — user defaults, portfolio snapshot, personal-state clear."""

from __future__ import annotations

import streamlit as st

from config.loader import clear_user_defaults
from engine.portfolio_sync import (
    AccountSummary,
    EquityGrant,
    Holding,
    PortfolioSnapshot,
    merge_snapshots,
)
from engine.upload_merge import SCALAR_KEYS, build_user_defaults_session_updates


def _build_user_defaults_session_updates(data: dict, *, as_spouse: bool) -> dict:
    """Compute session_state updates from a .user_defaults.json payload.

    Thin wrapper around :func:`engine.upload_merge.build_user_defaults_session_updates`.
    Pure function — returns a ``{session_key: value}`` dict without writing to state.
    See the engine module for full mapping rules.
    """
    return build_user_defaults_session_updates(data, as_spouse=as_spouse)


def _apply_user_defaults_to_session(data: dict, *, as_spouse: bool = False) -> None:
    """Stage JSON user-defaults keys for a deferred write into st.session_state.

    IMPORTANT — do NOT write directly here. Streamlit raises
    StreamlitAPIException if a session_state key is assigned after a widget
    with that key has already been instantiated in the SAME script run
    (e.g. ``_survivor_enabled``). The data-bridge upload handler that calls
    this runs late in the script — well after such widgets exist — so a
    direct write here reliably crashes the whole page on import. Instead we
    stash the computed updates into the non-widget ``_pending_defaults`` key
    and apply them via :func:`_drain_pending_defaults` at the very top of the
    NEXT script run, before any widget is created. The caller is responsible
    for triggering that next run (st.rerun()).

    When ``as_spouse=True``, cross-maps the file's ``your_*`` fields to the
    receiver's ``spouse_*`` slots and ignores joint / grant fields.
    See :func:`_build_user_defaults_session_updates` for the mapping rules.

    Note for the spouse path: ``get_household()`` reads grant_strikes via
    ``_user_grant_strikes`` from session_state; ``as_spouse=True`` deliberately
    skips that key so the receiver's own grants stay authoritative.
    """
    pending = st.session_state.setdefault("_pending_defaults", {})
    pending.update(_build_user_defaults_session_updates(data, as_spouse=as_spouse))


def _drain_pending_defaults() -> None:
    """Apply deferred user-defaults writes staged by _apply_user_defaults_to_session.

    MUST be called at the very top of the script run — after imports and
    st.set_page_config, but strictly BEFORE any widget is instantiated.
    Calling it late defeats the whole point: the deferral exists precisely
    because Streamlit forbids assigning to a session_state key once a widget
    with that key already exists in the current run.
    """
    pending = st.session_state.pop("_pending_defaults", None)
    if not pending:
        return
    for key, val in pending.items():
        st.session_state[key] = val


def _user_defaults_from_session() -> dict:
    """Inverse of _apply_user_defaults_to_session: read session_state → JSON dict."""
    payload: dict = {}
    for k in SCALAR_KEYS:
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
    # Emit survivor / inherited_iras whenever the session knows about them, even
    # when cleared (None / []). save_user_defaults merges onto the existing file,
    # so a truthy-only guard lets a stale on-disk value survive a clear and be
    # resurrected on the next startup (audit-0802 F4/F5).
    if "survivor" in st.session_state:
        payload["survivor"] = st.session_state["survivor"]
    if "inherited_iras" in st.session_state:
        payload["inherited_iras"] = st.session_state["inherited_iras"]
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

    Also clears ``_suppress_snapshot_autoload`` so that after an explicit
    sync or upload the auto-load guard resumes normal behaviour on future
    sessions.
    """
    existing = st.session_state.get("portfolio_snapshot")
    merged = merge_snapshots(existing, incoming, as_spouse=as_spouse)  # type: ignore[arg-type]
    st.session_state["portfolio_snapshot"] = merged
    st.session_state.pop("_suppress_snapshot_autoload", None)


def _clear_personal_session_state() -> None:
    """Reset personal-mode session state to demo defaults."""
    keys_to_clear = [
        "portfolio_snapshot",
        "_user_grant_strikes",
        "your_age",
        "spouse_age",
        "your_ira",
        "spouse_ira",
        "your_roth",
        "spouse_roth",
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
        "_survivor_enabled",
        "inherited_iras",
        "filing_status",
        "account_type_overrides",
        "data_bridge_privkey_b64",
        "_v2_privkey_input",
        "ytd_snapshot",
        "ssa_snapshot_you",
        "ssa_snapshot_spouse",
        "apply_ytd_to_projection",
        "ytd_manual_entry",
        # audit-0705 ui-5: these keys are seeded via setdefault in app.py but were
        # missing from this list, causing personal values to leak into the demo.
        "your_aca",
        "spouse_aca",
        "your_defer_first_rmd",
        "spouse_defer_first_rmd",
        "growth_rate",
        "txn_price_growth_rate",
        # C35 (audit-0721 W5): workplace-plan/beneficiary keys were missing,
        # so they leaked personal values into a demo-mode reset.
        "your_has_workplace_plan",
        "spouse_has_workplace_plan",
        "spouse_is_sole_beneficiary",
        # audit-0722b: net_inv_income (shared manual-NIIT input key on the
        # ACA+IRMAA and Sweet-Spot pages) was missing from this list, so a
        # personal value survived "Reset to demo" and kept inflating NIIT.
        "net_inv_income",
        # TODO(audit ui-7/ui-8): dynamic PDF-cache-prefix and generated-keypair
        # keys are separate low-severity findings; not cleared here.
    ]
    for k in keys_to_clear:
        st.session_state.pop(k, None)
    st.session_state.pop("_seeded", None)  # force re-seed from synthetic
    # Suppress on-disk cache auto-load for the remainder of this session so
    # the reset is not silently undone by the app.py startup guard.  An
    # explicit sync/upload clears this via _apply_portfolio_snapshot().
    st.session_state["_suppress_snapshot_autoload"] = True
    # Also remove the on-disk personal file. Session-only clearing is undone on
    # the next startup because save_user_defaults merges onto whatever autosave
    # left on disk before the reset (audit-0802 F3).
    clear_user_defaults()
