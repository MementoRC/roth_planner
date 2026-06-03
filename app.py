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

def _apply_user_defaults_to_session(data: dict) -> None:
    """Write JSON user-defaults keys into st.session_state."""
    scalar_keys = [
        "your_age", "spouse_age",
        "your_ira", "spouse_ira",
        "your_ss_fra", "spouse_ss_fra",
        "living_expenses",
        "stock_price_now",
    ]
    for k in scalar_keys:
        if k in data:
            sess_key = "txn_price" if k == "stock_price_now" else k
            st.session_state[sess_key] = data[k]
    # Stash grant_strikes for get_household() to pick up on next rerun.
    # config.loader.load_defaults() reads from disk — in deployed mode we
    # need a session_state pathway. get_household() checks this first.
    if "grant_strikes" in data:
        st.session_state["_user_grant_strikes"] = data["grant_strikes"]


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


def _handle_personal_uploads() -> None:
    """Sidebar widget to inject personal defaults + portfolio snapshot
    from JSON uploads. For use in the deployed (stlite) demo where the
    visitor cannot put files next to the app. Local users can ignore
    this and just keep .user_defaults.json + .portfolio_cache.json in cwd.
    """
    with st.sidebar.expander("\U0001f513 Use my real data (this session)"):
        st.caption(
            "Upload your local files for a personalized session. "
            "Values stay in this browser only; refresh = back to demo."
        )
        ud_file = st.file_uploader(
            ".user_defaults.json (ages, SS, grant strikes)",
            type=["json"], key="ud_upload",
        )
        pc_file = st.file_uploader(
            ".portfolio_cache.json (FinExtract holdings + grants)",
            type=["json"], key="pc_upload",
        )
        col_a, col_b = st.columns(2)
        if col_a.button("Apply", key="apply_uploads", use_container_width=True):
            applied = []
            if ud_file is not None:
                try:
                    data = json.loads(ud_file.read().decode("utf-8"))
                    _apply_user_defaults_to_session(data)
                    applied.append(".user_defaults.json")
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    st.error(f"Invalid .user_defaults.json: {e}")
            if pc_file is not None:
                try:
                    data = json.loads(pc_file.read().decode("utf-8"))
                    snap = _portfolio_snapshot_from_dict(data)
                    st.session_state["portfolio_snapshot"] = snap
                    applied.append(".portfolio_cache.json")
                except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
                    st.error(f"Invalid .portfolio_cache.json: {e}")
            if applied:
                st.success(f"Applied: {', '.join(applied)}. Rerunning…")
                st.rerun()
        if col_b.button("Reset to demo", key="reset_demo", use_container_width=True):
            _clear_personal_session_state()
            st.success("Reset to demo defaults.")
            st.rerun()


def _handle_personal_exports() -> None:
    """Sidebar widget to download local data files for use on the public site."""
    with st.sidebar.expander("📦 Export my data", expanded=False):
        st.caption(
            "Download your local data to share with the public site for third-party analysis."
        )
        defaults = _user_defaults_from_session()
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
        cache_path = Path(__file__).resolve().parent / ".portfolio_cache.json"
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
