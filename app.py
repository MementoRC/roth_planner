"""Roth Conversion Planner — Streamlit Application."""

import json
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="Roth Conversion Planner",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)


from config.loader import load_defaults  # noqa: E402
from engine.data_bridge_browser import (  # noqa: E402
    BROWSER_PRIVKEY_LS_KEY,
    is_pyodide,
    local_storage_get,
    local_storage_remove,
    local_storage_set,
)
from engine.data_bridge_crypto import (  # noqa: E402
    DataBridgeCryptoError,
    derive_pubkey,
    open_uploaded_payload,
    seal,
)
from engine.data_bridge_keys import (  # noqa: E402
    decode_keymaterial,
    load_privkey,
    load_pubkey,
)
from engine.portfolio_sync import merge_snapshots  # noqa: E402
from engine.upload_merge import build_user_defaults_session_updates  # noqa: E402


def _seed_session_state() -> None:
    """Seed session state from synthetic defaults (or user overrides)."""
    if st.session_state.get("_seeded"):
        return
    defaults = load_defaults()
    # Map config keys to session_state keys (most are 1:1)
    session_keys = {
        "your_age": "your_age",
        "spouse_age": "spouse_age",
        "your_ira": "your_ira",
        "spouse_ira": "spouse_ira",
        "your_ss_fra": "your_ss_fra",
        "spouse_ss_fra": "spouse_ss_fra",
        "living_expenses": "living_expenses",
        "stock_price_now": "txn_price",  # session uses 'txn_price' even after gate
    }
    for cfg_key, sess_key in session_keys.items():
        if cfg_key in defaults:
            st.session_state.setdefault(sess_key, defaults[cfg_key])
    # Non-config keys: set fixed defaults
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("your_aca", False)
    st.session_state.setdefault("spouse_aca", False)
    # Cache ticker for sidebar label (avoids re-importing config on every render)
    st.session_state.setdefault("_stock_ticker", defaults.get("stock_ticker", "Stock"))
    st.session_state.setdefault("_seeded", True)


# Shared state: household parameters
_seed_session_state()

st.sidebar.title("🎯 Roth Planner")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigate",
    ["📊 Dashboard", "📋 Conversion Planner", "💰 YTD Income", "🎯 Sweet Spot Finder", "📉 RMD Squeeze", "⚖️ Comparator", "🏥 ACA + IRMAA Explorer", "📦 Asset Location", "✅ Roth Eligibility", "🔗 Portfolio"],
    label_visibility="collapsed",
)

# Sidebar: shared inputs
st.sidebar.markdown("### Your Numbers")
_synced = st.session_state.get("portfolio_snapshot") is not None
st.session_state.your_ira = st.sidebar.number_input(
    "Your Trad IRA" + (" (synced)" if _synced else ""),
    value=st.session_state.your_ira, step=50_000, format="%d",
    disabled=_synced,
    help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
)
st.session_state.spouse_ira = st.sidebar.number_input(
    "Spouse Trad IRA", value=st.session_state.spouse_ira, step=50_000, format="%d"
)
st.session_state.growth_rate = st.sidebar.slider(
    "Growth Rate %", 3.0, 12.0, st.session_state.growth_rate, 0.5
)
st.session_state.your_ss_fra = st.sidebar.number_input(
    "Your SS at FRA 67 ($/mo)", value=st.session_state.your_ss_fra, step=100, format="%d"
)
st.session_state.spouse_ss_fra = st.sidebar.number_input(
    "Spouse SS at FRA 67 ($/mo)", value=st.session_state.spouse_ss_fra, step=100, format="%d"
)
st.session_state.living_expenses = st.sidebar.number_input(
    "Annual Living Expenses", value=st.session_state.living_expenses, step=5_000, format="%d"
)
st.session_state.txn_price = st.sidebar.number_input(
    f"{st.session_state.get('_stock_ticker', 'Stock')} Current Price",
    value=st.session_state.txn_price, step=5, format="%d",
)

st.sidebar.markdown("### Portfolio Sync")

# Load cached snapshots on first run
if "portfolio_snapshot" not in st.session_state:
    from engine.portfolio_sync import load_snapshot

    _cached = load_snapshot()
    if _cached is not None:
        st.session_state.portfolio_snapshot = _cached
        pretax = _cached.pretax_total
        if pretax > 0:
            st.session_state.your_ira = int(pretax)

if "tax_return_snapshot" not in st.session_state:
    from engine.portfolio_sync import load_tax_snapshot

    _cached_tax = load_tax_snapshot()
    if _cached_tax is not None:
        st.session_state.tax_return_snapshot = _cached_tax

if "ytd_snapshot" not in st.session_state:
    from engine.portfolio_sync import load_ytd_snapshot

    _cached_ytd = load_ytd_snapshot()
    if _cached_ytd is not None:
        st.session_state.ytd_snapshot = _cached_ytd

_sync = st.sidebar.button("Sync from FinExtract", help="Pull live holdings from ingestion server")
if _sync:
    from engine.portfolio_sync import (
        fetch_portfolio,
        fetch_tax_return,
        fetch_ytd_snapshot,
        save_snapshot,
        save_tax_snapshot,
        save_ytd_snapshot,
    )

    snap = fetch_portfolio()
    if snap.server_available:
        st.session_state.portfolio_snapshot = snap
        save_snapshot(snap)
        # Push synced balance into sidebar state
        pretax = snap.pretax_total
        if pretax > 0:
            st.session_state.your_ira = int(pretax)
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
        st.sidebar.success(
            f"Synced: {len(snap.accounts)} accounts, "
            f"{len(snap.equity_grants)} active grants"
            + (", tax return data" if tax_snap.server_available else "")
            + (", YTD income" if ytd_snap.snapshot_date else "")
        )
    else:
        st.sidebar.error(f"Server unavailable: {snap.error}")

st.sidebar.markdown("### Healthcare")
st.session_state.your_aca = st.sidebar.checkbox(
    "You on ACA Marketplace", value=st.session_state.your_aca,
    help="Check if you are enrolled in ACA marketplace (not employer plan)",
)
st.session_state.spouse_aca = st.sidebar.checkbox(
    "Spouse on ACA Marketplace", value=st.session_state.spouse_aca,
    help="Check if spouse is enrolled in ACA marketplace",
)

# ---------------------------------------------------------------------------
# Personal-data upload widget (for the deployed / stlite demo)
# ---------------------------------------------------------------------------

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
        "your_age", "spouse_age",
        "your_ira", "spouse_ira",
        "your_ss_fra", "spouse_ss_fra",
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
    return payload


def _portfolio_snapshot_from_dict(data: dict) -> object:
    """Reconstruct a PortfolioSnapshot from its asdict() JSON form."""
    from engine.portfolio_sync import (
        AccountSummary,
        EquityGrant,
        Holding,
        PortfolioSnapshot,
    )

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
    from engine.portfolio_sync import PortfolioSnapshot

    existing = st.session_state.get("portfolio_snapshot")
    merged = merge_snapshots(existing, incoming, as_spouse=as_spouse)  # type: ignore[arg-type]
    st.session_state["portfolio_snapshot"] = merged


def _clear_personal_session_state() -> None:
    """Reset personal-mode session state to demo defaults."""
    keys_to_clear = [
        "portfolio_snapshot", "_user_grant_strikes",
        "your_age", "spouse_age",
        "your_ira", "spouse_ira",
        "your_ss_fra", "spouse_ss_fra",
        "living_expenses", "txn_price",
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
    """Sidebar widget for entering and caching the V2 data-bridge private key.

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

    with st.sidebar.expander("\U0001f511 V2 private key", expanded=expand):
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
    """Sidebar widget to inject personal defaults + portfolio snapshot
    from JSON uploads. For use in the deployed (stlite) demo where the
    visitor cannot put files next to the app. Local users can ignore
    this and just keep .user_defaults.json + .portfolio_cache.json in cwd.

    Accepts both V1 plaintext ``.json`` and V2 sealed ``.json.enc`` files.
    Encrypted uploads require the V2 private key configured in the
    "\U0001f511 V2 private key" sidebar widget (or available on disk).

    Each uploader has a per-file "Whose data?" toggle. "Me" applies the
    payload to the receiver's own slots (current behavior). "Spouse" treats
    the payload as the spouse's planner export from their own perspective,
    cross-maps ``your_*`` fields to the receiver's ``spouse_*`` slots, and
    merges portfolio accounts with ``owner="spouse"`` while preserving the
    receiver's own accounts, grants, and TXN holdings.
    """
    with st.sidebar.expander("\U0001f513 Use my real data (this session)"):
        st.caption(
            "Upload your local files for a personalized session. "
            "Values stay in this browser only; refresh = back to demo. "
            "V2 `.json.enc` files require the private key configured above. "
            "Use the \"Whose data?\" toggle when uploading your spouse's planner export."
        )
        ud_role = st.radio(
            "Whose .user_defaults.json?",
            ["Me", "Spouse"],
            horizontal=True,
            key="ud_role",
        )
        ud_file = st.file_uploader(
            ".user_defaults.json[.enc] (ages, SS, grant strikes)",
            type=["json", "enc"], key="ud_upload",
        )
        pc_role = st.radio(
            "Whose .portfolio_cache.json?",
            ["Me", "Spouse"],
            horizontal=True,
            key="pc_role",
        )
        pc_file = st.file_uploader(
            ".portfolio_cache.json[.enc] (FinExtract holdings + grants)",
            type=["json", "enc"], key="pc_upload",
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
    """Sidebar widget to download local data files for use on the public site.

    When a V2 data-bridge public key is configured (see ``deploy/README.md``),
    exports are sealed with ``crypto_box_seal`` and emitted as ``.json.enc``.
    Otherwise the V1 plaintext export is shown with a deprecation warning.
    """
    with st.sidebar.expander("📦 Export my data", expanded=False):
        pubkey = _resolved_pubkey()
        defaults = _user_defaults_from_session()
        cache_path = Path(__file__).resolve().parent / ".portfolio_cache.json"

        if pubkey is not None:
            st.caption(
                "🔐 V2 encrypted export active — files are sealed for your private key."
            )
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


_handle_v2_privkey()
_handle_personal_uploads()
_handle_personal_exports()

# Build household from session state
from engine.dividend_forecast import forecast_portfolio  # noqa: E402
from engine.portfolio_sync import positions_for_forecast  # noqa: E402
from models.household import GrowthProfile, Household  # noqa: E402


def get_household() -> Household:
    hh = Household(
        your_age=st.session_state.your_age,
        spouse_age=st.session_state.spouse_age,
        your_ira=st.session_state.your_ira,
        spouse_ira=st.session_state.spouse_ira,
        your_ss_fra=st.session_state.your_ss_fra,
        spouse_ss_fra=st.session_state.spouse_ss_fra,
        growth_rate=st.session_state.growth_rate / 100,
        living_expenses=st.session_state.living_expenses,
        txn_price_now=st.session_state.txn_price,
        your_aca_enrolled=st.session_state.your_aca,
        spouse_aca_enrolled=st.session_state.spouse_aca,
    )

    # If portfolio was synced, derive per-account growth and balances
    snap = st.session_state.get("portfolio_snapshot")
    if snap and snap.server_available:
        # Your pre-tax accounts (Rollover IRA + 403b) → your_ira balance & growth
        pretax = snap.pretax_total
        if pretax > 0:
            hh.your_ira = pretax
            hh.your_ira_growth = GrowthProfile(
                default_rate=snap.pretax_weighted_return,
            )

        # Brokerage weighted return + dividend forecast
        brok = snap.account_by_type("brokerage")
        if brok and brok.total_value > 0:
            _fcst = forecast_portfolio(
                positions_for_forecast(brok),
                total_balance=brok.total_value,
            )
            hh.brokerage_growth = GrowthProfile(
                default_rate=brok.weighted_return,
                yield_rate=_fcst.yield_rate,
                qualified_fraction=_fcst.qualified_fraction,
            )

        # Auto-derive current stock price from TXN shares value/count
        if snap.txn_shares_held > 0 and snap.txn_shares_value > 0:
            hh.txn_price_now = snap.txn_shares_value / snap.txn_shares_held

    # Merge FinExtract equity grants with user-supplied strike prices.
    # FinExtract is the source of truth for which grants exist + outstanding
    # shares; the user JSON only supplies strike per grant year.
    if snap and snap.server_available and snap.equity_grants:
        from models.grants import StockGrant

        strikes = (
            st.session_state.get("_user_grant_strikes")
            or load_defaults().get("grant_strikes", {})
        )
        merged_grants = []
        for g in snap.equity_grants:
            year = int(g.grant_date.split("-")[0]) if g.grant_date else 0
            strike = float(strikes.get(str(year), 0.0))
            if strike <= 0 or g.outstanding <= 0:
                continue  # skip grants without a known strike or fully exercised
            # NQO typically expires 10 years from grant date
            expires = year + 10
            merged_grants.append(StockGrant(
                year=year,
                strike=strike,
                shares=g.outstanding,
                expiry_year=expires,
            ))
        if merged_grants:
            hh.grants = merged_grants

    return hh


# Route to page
if page == "📊 Dashboard":
    from views.dashboard import render

    render(get_household())
elif page == "📋 Conversion Planner":
    from views.planner import render

    render(get_household())
elif page == "💰 YTD Income":
    from views.ytd_income import render

    render(get_household())
elif page == "🎯 Sweet Spot Finder":
    from views.sweet_spot import render

    render(get_household())
elif page == "📉 RMD Squeeze":
    from views.rmd_squeeze import render

    render(get_household())
elif page == "⚖️ Comparator":
    from views.comparator import render

    render(get_household())
elif page == "🏥 ACA + IRMAA Explorer":
    from views.aca_irmaa import render

    render(get_household())
elif page == "📦 Asset Location":
    from views.asset_location import render

    render(get_household())
elif page == "✅ Roth Eligibility":
    from views.roth_eligibility import render

    render(get_household())
elif page == "🔗 Portfolio":
    from views.portfolio import render

    render(get_household())
