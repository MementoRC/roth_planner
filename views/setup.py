"""Setup page — household parameters, FinExtract sync, V2 data bridge."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from engine.data_bridge_browser import (
    BROWSER_PRIVKEY_LS_KEY,
    is_pyodide,
    local_storage_get,
    local_storage_remove,
    local_storage_set,
)
from engine.data_bridge_keys import (
    decode_keymaterial,
    load_privkey,
    load_pubkey,
)
from engine.portfolio_sync import (
    AccountSummary,
    EquityGrant,
    Holding,
    PortfolioSnapshot,
    fetch_portfolio,
    fetch_tax_return,
    fetch_ytd_snapshot,
    merge_snapshots,
    save_snapshot,
    save_tax_snapshot,
    save_ytd_snapshot,
)
from engine.upload_merge import build_user_defaults_session_updates, derive_ira_balances
from models.household import Household


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
        "living_expenses",
        "stock_price_now",
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
        "living_expenses",
        "txn_price",
    ]
    for k in keys_to_clear:
        st.session_state.pop(k, None)
    st.session_state.pop("_seeded", None)  # force re-seed from synthetic


def _resolved_pubkey() -> bytes | None:
    """Resolve V2 public key for encryption.

    Order: env/dotfile (:func:`load_pubkey`), then derive from the
    session-state private key (browser paste flow). Returns ``None`` if
    no key is available from any source.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import derive_pubkey

    pk = load_pubkey()
    if pk is not None:
        return pk
    priv_b64 = st.session_state.get("data_bridge_privkey_b64")
    if not priv_b64:
        return None
    try:
        priv_raw = decode_keymaterial(priv_b64)
    except ValueError:
        return None
    return derive_pubkey(priv_raw)


def _resolve_privkey_bytes() -> bytes | None:
    """Resolve V2 private key for decryption.

    Order: session-state pasted key, then disk dotfile/env via
    :func:`load_privkey`. Returns ``None`` if no key is available.
    """
    priv_b64 = st.session_state.get("data_bridge_privkey_b64")
    if priv_b64:
        try:
            return decode_keymaterial(priv_b64)
        except ValueError:
            pass
    return load_privkey()


def _handle_v2_privkey() -> None:
    """Widget for entering and caching the V2 data-bridge private key.

    On stlite/Pyodide, the key is cached in ``localStorage`` under
    ``roth_planner.data_bridge.priv_b64`` so it survives page reloads.
    In all environments, the key lives in ``st.session_state`` under
    ``data_bridge_privkey_b64`` for the duration of the session.
    """
    # Hydrate session_state from localStorage on first paint.
    if "data_bridge_privkey_b64" not in st.session_state:
        cached = local_storage_get(BROWSER_PRIVKEY_LS_KEY)
        if cached:
            st.session_state["data_bridge_privkey_b64"] = cached

    has_key = "data_bridge_privkey_b64" in st.session_state
    # Auto-expand on the public site when no key is set — user needs to act.
    expand = is_pyodide() and not has_key

    with st.expander("\U0001f511 V2 private key", expanded=expand):
        if has_key:
            st.caption("\U0001f510 Private key loaded for this session.")
            if st.button("Clear", key="clear_v2_privkey"):
                st.session_state.pop("data_bridge_privkey_b64", None)
                local_storage_remove(BROWSER_PRIVKEY_LS_KEY)
                st.rerun()
            return
        st.caption(
            "Paste your data-bridge private key (base64). Required to decrypt "
            "uploaded `.json.enc` files and to encrypt exports on the public site."
        )
        key_input = st.text_input(
            "Private key (base64)",
            type="password",
            key="_v2_privkey_input",
            help="From `~/.finextract/data-bridge.priv` on your local host.",
        )
        if st.button("Save", key="save_v2_privkey") and key_input:
            try:
                decode_keymaterial(key_input)
            except ValueError as e:
                st.error(f"Invalid key: {e}")
                return
            val = key_input.strip()
            st.session_state["data_bridge_privkey_b64"] = val
            local_storage_set(BROWSER_PRIVKEY_LS_KEY, val)
            st.success("Private key saved.")
            st.rerun()


def _handle_personal_uploads() -> None:
    """Widget to inject personal defaults + portfolio snapshot from JSON uploads.

    For use in the deployed (stlite) demo where the visitor cannot put files
    next to the app. Local users can ignore this and just keep
    .user_defaults.json + .portfolio_cache.json in cwd.

    Accepts both V1 plaintext ``.json`` and V2 sealed ``.json.enc`` files.
    Encrypted uploads require the V2 private key configured in the
    "\U0001f511 V2 private key" expander (or available on disk).

    Each uploader has a per-file "Whose data?" toggle. "Me" applies the
    payload to the receiver's own slots (current behavior). "Spouse" treats
    the payload as the spouse's planner export from their own perspective,
    cross-maps ``your_*`` fields to the receiver's ``spouse_*`` slots, and
    merges portfolio accounts with ``owner="spouse"`` while preserving the
    receiver's own accounts, grants, and TXN holdings.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import (
        DataBridgeCryptoError,
        open_uploaded_payload,
    )

    with st.expander("\U0001f513 Use my real data (this session)"):
        st.caption(
            "Upload your local files for a personalized session. "
            "Values stay in this browser only; refresh = back to demo. "
            "V2 `.json.enc` files require the private key configured above. "
            'Use the "Whose data?" toggle when uploading your spouse\'s planner export.'
        )
        ud_role = st.radio(
            "Whose .user_defaults.json?",
            ["Me", "Spouse"],
            horizontal=True,
            key="ud_role",
        )
        ud_file = st.file_uploader(
            ".user_defaults.json[.enc] (ages, SS, grant strikes)",
            type=["json", "enc"],
            key="ud_upload",
        )
        pc_role = st.radio(
            "Whose .portfolio_cache.json?",
            ["Me", "Spouse"],
            horizontal=True,
            key="pc_role",
        )
        pc_file = st.file_uploader(
            ".portfolio_cache.json[.enc] (FinExtract holdings + grants)",
            type=["json", "enc"],
            key="pc_upload",
        )
        col_a, col_b = st.columns(2)
        if col_a.button("Apply", key="apply_uploads", use_container_width=True):
            applied: list[str] = []
            privkey = _resolve_privkey_bytes()
            if ud_file is not None:
                try:
                    raw = ud_file.read()
                    plaintext = open_uploaded_payload(raw, privkey)
                    data = json.loads(plaintext.decode("utf-8"))
                    _apply_user_defaults_to_session(data, as_spouse=(ud_role == "Spouse"))
                    applied.append(f"{ud_file.name} ({ud_role.lower()})")
                except (
                    json.JSONDecodeError,
                    ValueError,
                    TypeError,
                    DataBridgeCryptoError,
                ) as e:
                    st.error(f"Invalid {ud_file.name}: {e}")
            if pc_file is not None:
                try:
                    raw = pc_file.read()
                    plaintext = open_uploaded_payload(raw, privkey)
                    data = json.loads(plaintext.decode("utf-8"))
                    snap = _portfolio_snapshot_from_dict(data)
                    _apply_portfolio_snapshot(snap, as_spouse=(pc_role == "Spouse"))
                    applied.append(f"{pc_file.name} ({pc_role.lower()})")
                except (
                    json.JSONDecodeError,
                    ValueError,
                    TypeError,
                    KeyError,
                    DataBridgeCryptoError,
                ) as e:
                    st.error(f"Invalid {pc_file.name}: {e}")
            if applied:
                st.success(f"Applied: {', '.join(applied)}. Rerunning…")
                st.rerun()
        if col_b.button("Reset to demo", key="reset_demo", use_container_width=True):
            _clear_personal_session_state()
            st.success("Reset to demo defaults.")
            st.rerun()


def _handle_personal_exports() -> None:
    """Widget to download local data files for use on the public site.

    When a V2 data-bridge public key is configured (see ``deploy/README.md``),
    exports are sealed with ``crypto_box_seal`` and emitted as ``.json.enc``.
    Otherwise the V1 plaintext export is shown with a deprecation warning.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import seal

    with st.expander("📦 Export my data", expanded=False):
        pubkey = _resolved_pubkey()
        defaults = _user_defaults_from_session()
        cache_path = Path(__file__).resolve().parent.parent / ".portfolio_cache.json"

        if pubkey is not None:
            st.caption("🔐 V2 encrypted export active — files are sealed for your private key.")
            if defaults:
                payload = json.dumps(defaults, indent=2, default=str).encode("utf-8")
                st.download_button(
                    label="⬇️ .user_defaults.json.enc",
                    data=seal(payload, pubkey),
                    file_name=".user_defaults.json.enc",
                    mime="application/octet-stream",
                    key="export_user_defaults_enc",
                )
            else:
                st.caption("(Enter your numbers first to enable export.)")
            if cache_path.exists():
                st.download_button(
                    label="⬇️ .portfolio_cache.json.enc",
                    data=seal(cache_path.read_bytes(), pubkey),
                    file_name=".portfolio_cache.json.enc",
                    mime="application/octet-stream",
                    key="export_portfolio_cache_enc",
                )
            else:
                st.caption("(Run Portfolio Sync first to enable cache export.)")
            return

        # No V2 key. Public site → BLOCK V1 entirely (no plaintext leaves browser).
        if is_pyodide():
            st.caption(
                "\U0001f512 No plaintext export available on the public site. "
                "Paste your private key in the '\U0001f511 V2 private key' widget above "
                "to enable encrypted export."
            )
            return

        # V1 plaintext fallback — local host only, deprecated.
        st.caption(
            "Saves to your browser's default downloads folder. Share with the public site for third-party analysis."
        )
        st.warning(
            "⚠️ Plaintext export is deprecated and will be removed in a future release. "
            "Run `pixi run gen-data-bridge-keypair` to enable encrypted export."
        )
        if defaults:
            st.download_button(
                label="⬇️ .user_defaults.json",
                data=json.dumps(defaults, indent=2, default=str),
                file_name=".user_defaults.json",
                mime="application/json",
                key="export_user_defaults",
            )
        else:
            st.caption("(Enter your numbers first to enable export.)")
        if cache_path.exists():
            st.download_button(
                label="⬇️ .portfolio_cache.json",
                data=cache_path.read_bytes(),
                file_name=".portfolio_cache.json",
                mime="application/json",
                key="export_portfolio_cache",
            )
        else:
            st.caption("(Run Portfolio Sync first to enable cache export.)")


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
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


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
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


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
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


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
    from engine.portfolio_sync import _classify_account  # private; deferred intentionally

    with st.expander("🏷️ Account Type Overrides"):
        if snap is None or not snap.accounts:
            st.info("No accounts loaded — sync first to see detected acctIds.")
            return

        st.caption("Changes take effect on next sync.")
        _type_options = ["trad_ira", "roth_ira", "brokerage", "hsa", "403b"]
        overrides: dict[str, str] = st.session_state.get("account_type_overrides") or {}

        seen: set[str] = set()
        for acct in snap.accounts:
            acct_id = acct.account_name
            if acct_id in seen:
                continue
            seen.add(acct_id)

            auto_type, _ = _classify_account(acct_id)
            current = overrides.get(acct_id, auto_type)
            try:
                default_idx = _type_options.index(current)
            except ValueError:
                default_idx = 0

            col_id, col_type, col_sel = st.columns([3, 2, 2])
            col_id.text(acct_id)
            col_type.caption(f"auto: {auto_type}")
            chosen = col_sel.selectbox(
                "Type",
                _type_options,
                index=default_idx,
                key=f"_override_{acct_id}",
                label_visibility="collapsed",
            )
            # Write through to the canonical overrides dict in session state.
            if "account_type_overrides" not in st.session_state:
                st.session_state["account_type_overrides"] = {}
            st.session_state["account_type_overrides"][acct_id] = chosen


def render(hh: Household) -> None:
    """Render the Setup page — household parameters, sync, and data bridge."""
    st.title("⚙️ Setup")

    tab_params, tab_portfolio, tab_bridge = st.tabs(
        ["📊 Parameters", "💼 Portfolio", "🔗 Data bridge"]
    )

    # ------------------------------------------------------------------
    # TAB 1: Parameters
    # ------------------------------------------------------------------
    with tab_params:
        _synced = bool(st.session_state.get("portfolio_snapshot"))
        me_sub, spouse_sub, joint_sub = st.tabs(["Me", "Spouse", "Joint"])

        with me_sub:
            st.session_state.your_ira = st.number_input(
                "Your Trad IRA" + (" (synced)" if _synced else ""),
                value=st.session_state.your_ira,
                step=50_000,
                format="%d",
                disabled=_synced,
                help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
            )
            st.session_state.your_age = st.number_input(
                "Your Age",
                value=st.session_state.your_age,
                step=1,
                format="%d",
            )
            st.session_state.your_ss_fra = st.number_input(
                "Your SS at FRA 67 ($/mo)",
                value=st.session_state.your_ss_fra,
                step=100,
                format="%d",
            )
            st.session_state.your_aca = st.checkbox(
                "You on ACA Marketplace",
                value=st.session_state.your_aca,
                help="Check if you are enrolled in ACA marketplace (not employer plan)",
            )

        with spouse_sub:
            st.session_state.spouse_ira = st.number_input(
                "Spouse Trad IRA" + (" (synced)" if _synced else ""),
                value=st.session_state.spouse_ira,
                step=50_000,
                format="%d",
                disabled=_synced,
                help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
            )
            st.session_state.spouse_age = st.number_input(
                "Spouse Age",
                value=st.session_state.spouse_age,
                step=1,
                format="%d",
            )
            st.session_state.spouse_ss_fra = st.number_input(
                "Spouse SS at FRA 67 ($/mo)",
                value=st.session_state.spouse_ss_fra,
                step=100,
                format="%d",
            )
            st.session_state.spouse_aca = st.checkbox(
                "Spouse on ACA Marketplace",
                value=st.session_state.spouse_aca,
                help="Check if spouse is enrolled in ACA marketplace",
            )

        with joint_sub:
            st.session_state.growth_rate = st.slider(
                "Growth Rate %", 3.0, 12.0, st.session_state.growth_rate, 0.5
            )
            st.session_state.living_expenses = st.number_input(
                "Annual Living Expenses",
                value=st.session_state.living_expenses,
                step=5_000,
                format="%d",
            )
            st.session_state.txn_price = st.number_input(
                f"{st.session_state.get('_stock_ticker', 'Stock')} Current Price",
                value=st.session_state.txn_price,
                step=5,
                format="%d",
            )

    # ------------------------------------------------------------------
    # TAB 2: Portfolio
    # ------------------------------------------------------------------
    with tab_portfolio:
        snap: PortfolioSnapshot | None = st.session_state.get("portfolio_snapshot")

        # Sync button + status banner
        _sync = st.button(
            "Sync from FinExtract", help="Pull live holdings from ingestion server"
        )
        if snap is not None:
            st.caption(f"Loaded: {len(snap.accounts)} accounts, {len(snap.equity_grants)} grants")

        if _sync:
            snap = fetch_portfolio(
                account_type_overrides=st.session_state.get("account_type_overrides") or None,
            )
            if snap.server_available:
                st.session_state.portfolio_snapshot = snap
                save_snapshot(snap)
                # Push synced balances into session state
                _your_ira, _spouse_ira = derive_ira_balances(snap)
                if _your_ira > 0:
                    st.session_state.your_ira = int(_your_ira)
                if _spouse_ira > 0:
                    st.session_state.spouse_ira = int(_spouse_ira)
                # Also sync tax return data
                tax_snap = fetch_tax_return()
                if tax_snap.server_available:
                    st.session_state.tax_return_snapshot = tax_snap
                    save_tax_snapshot(tax_snap)
                # Also sync YTD income data
                ytd_snap = fetch_ytd_snapshot()
                if ytd_snap.snapshot_date:
                    st.session_state.ytd_snapshot = ytd_snap
                    save_ytd_snapshot(ytd_snap)
                st.success(
                    f"Synced: {len(snap.accounts)} accounts, "
                    f"{len(snap.equity_grants)} active grants"
                    + (", tax return data" if tax_snap.server_available else "")
                    + (", YTD income" if ytd_snap.snapshot_date else "")
                )
            else:
                st.error(f"Server unavailable: {snap.error}")
                snap = st.session_state.get("portfolio_snapshot")

        _render_portfolio_sub_tabs(snap)
        _render_account_type_overrides(snap)

    # ------------------------------------------------------------------
    # TAB 3: Data bridge
    # ------------------------------------------------------------------
    with tab_bridge:
        _handle_v2_privkey()
        _handle_personal_uploads()
        _handle_personal_exports()
