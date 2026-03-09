"""Roth Conversion Planner — Streamlit Application."""

import streamlit as st

st.set_page_config(
    page_title="Roth Conversion Planner",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Shared state: household parameters
if "your_ira" not in st.session_state:
    st.session_state.your_ira = 1_700_000
    st.session_state.spouse_ira = 1_700_000
    st.session_state.your_age = 61
    st.session_state.spouse_age = 55
    st.session_state.your_ss_fra = 3_800
    st.session_state.spouse_ss_fra = 3_800
    st.session_state.growth_rate = 7.0
    st.session_state.living_expenses = 30_000
    st.session_state.txn_price = 212
    st.session_state.your_aca = False
    st.session_state.spouse_aca = False

st.sidebar.title("🎯 Roth Planner")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigate",
    ["📊 Dashboard", "📋 Conversion Planner", "🎯 Sweet Spot Finder", "📉 RMD Squeeze", "⚖️ Comparator", "🏥 ACA + IRMAA Explorer", "📦 Asset Location", "🔗 Portfolio"],
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
    "TXN Current Price", value=st.session_state.txn_price, step=5, format="%d"
)

st.sidebar.markdown("### Portfolio Sync")

# Load cached snapshot on first run
if "portfolio_snapshot" not in st.session_state:
    from engine.portfolio_sync import load_snapshot

    _cached = load_snapshot()
    if _cached is not None:
        st.session_state.portfolio_snapshot = _cached
        pretax = _cached.pretax_total
        if pretax > 0:
            st.session_state.your_ira = int(pretax)

_sync = st.sidebar.button("Sync from FinExtract", help="Pull live holdings from ingestion server")
if _sync:
    from engine.portfolio_sync import fetch_portfolio, save_snapshot

    snap = fetch_portfolio()
    if snap.server_available:
        st.session_state.portfolio_snapshot = snap
        save_snapshot(snap)
        # Push synced balance into sidebar state
        pretax = snap.pretax_total
        if pretax > 0:
            st.session_state.your_ira = int(pretax)
        st.sidebar.success(
            f"Synced: {len(snap.accounts)} accounts, "
            f"{len(snap.equity_grants)} active grants"
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

# Build household from session state
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

        # Brokerage weighted return
        brok = snap.account_by_type("brokerage")
        if brok and brok.total_value > 0:
            hh.brokerage_growth = GrowthProfile(default_rate=brok.weighted_return)

    return hh


# Route to page
if page == "📊 Dashboard":
    from views.dashboard import render

    render(get_household())
elif page == "📋 Conversion Planner":
    from views.planner import render

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
elif page == "🔗 Portfolio":
    from views.portfolio import render

    render(get_household())
