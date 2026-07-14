"""Roth Conversion Planner — Streamlit Application."""

import streamlit as st

st.set_page_config(
    page_title="Roth Conversion Planner",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)


from config.loader import load_defaults  # noqa: E402
from engine.irmaa import BASE_PART_B  # noqa: E402
from engine.tax_return_pdf import load_pdf_tax_records, merge_pdf_magi  # noqa: E402
from engine.upload_merge import SCALAR_KEYS  # noqa: E402


def _seed_session_state() -> None:
    """Seed session state from synthetic defaults (or user overrides)."""
    if st.session_state.get("_seeded"):
        return
    defaults = load_defaults()
    # Map config keys to session_state keys (most are 1:1; stock_price_now is
    # aliased to txn_price because session_state uses that name even after
    # the gate). Driven by engine.upload_merge.SCALAR_KEYS — the canonical
    # list of persisted scalar setup fields — so seeding stays in sync with
    # what export/import round-trips. (Audit 2026-07-13: this used to be a
    # hand-list covering only ~10 of the 27 scalars; filing_status,
    # growth_rate, and 15 others were hardcoded below and silently discarded
    # any persisted value on every fresh session.)
    session_keys = {k: ("txn_price" if k == "stock_price_now" else k) for k in SCALAR_KEYS}
    for cfg_key, sess_key in session_keys.items():
        if cfg_key in defaults:
            st.session_state.setdefault(sess_key, defaults[cfg_key])
    # Fallback defaults for scalars with no persisted value (first-run demo).
    # setdefault() below is a no-op for any key already seeded from `defaults` above.
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("your_aca", False)
    st.session_state.setdefault("spouse_aca", False)
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("aca_enhanced_subsidies_active", False)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("your_ss_start_age", 70)
    st.session_state.setdefault("spouse_ss_start_age", 70)
    st.session_state.setdefault("your_rmd_start_age", 75)
    st.session_state.setdefault("spouse_rmd_start_age", 75)
    st.session_state.setdefault("your_defer_first_rmd", False)
    st.session_state.setdefault("spouse_defer_first_rmd", False)
    st.session_state.setdefault("your_fra_age", 67)
    st.session_state.setdefault("spouse_fra_age", 67)
    st.session_state.setdefault("prior_year_magi", {})
    st.session_state.setdefault("survivor", None)
    st.session_state.setdefault("inherited_iras", [])
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("filing_status", "MFJ")
    # Cache ticker for sidebar label (avoids re-importing config on every render)
    st.session_state.setdefault("_stock_ticker", defaults.get("stock_ticker", "Stock"))
    st.session_state.setdefault("_seeded", True)


# Shared state: household parameters
_seed_session_state()

# Load cached snapshots on first run (silently — Setup page shows status)
if "portfolio_snapshot" not in st.session_state and not st.session_state.get("_suppress_snapshot_autoload"):
    from engine.portfolio_sync import load_snapshot

    _cached = load_snapshot()
    if _cached is not None:
        st.session_state.portfolio_snapshot = _cached
        from engine.upload_merge import derive_ira_balances as _derive_ira
        from engine.upload_merge import derive_roth_balances as _derive_roth

        _your_ira, _spouse_ira = _derive_ira(_cached)
        if _your_ira > 0:
            st.session_state.your_ira = int(_your_ira)
        if _spouse_ira > 0:
            st.session_state.spouse_ira = int(_spouse_ira)
        _your_roth, _spouse_roth = _derive_roth(_cached)
        if _your_roth > 0:
            st.session_state.your_roth = int(_your_roth)
        if _spouse_roth > 0:
            st.session_state.spouse_roth = int(_spouse_roth)

if "ytd_snapshot" not in st.session_state:
    from engine.portfolio_sync import load_ytd_snapshot

    _cached_ytd = load_ytd_snapshot()
    if _cached_ytd is not None:
        st.session_state.ytd_snapshot = _cached_ytd

if "ssa_snapshot_you" not in st.session_state:
    from engine.portfolio_sync import load_ssa_snapshot, match_fra_estimate

    _cached_ssa_you = load_ssa_snapshot(owner="you")
    if _cached_ssa_you is not None:
        st.session_state.ssa_snapshot_you = _cached_ssa_you
        _your_fra_age = st.session_state.get("your_fra_age")
        if _your_fra_age is not None:
            _your_fra_match = match_fra_estimate(_cached_ssa_you.estimates, _your_fra_age)
            if _your_fra_match is not None:
                st.session_state.your_ss_fra = int(round(_your_fra_match.monthly_amount))

if "ssa_snapshot_spouse" not in st.session_state:
    from engine.portfolio_sync import load_ssa_snapshot, match_fra_estimate

    _cached_ssa_spouse = load_ssa_snapshot(owner="spouse")
    if _cached_ssa_spouse is not None:
        st.session_state.ssa_snapshot_spouse = _cached_ssa_spouse
        _spouse_fra_age = st.session_state.get("spouse_fra_age")
        if _spouse_fra_age is not None:
            _spouse_fra_match = match_fra_estimate(_cached_ssa_spouse.estimates, _spouse_fra_age)
            if _spouse_fra_match is not None:
                st.session_state.spouse_ss_fra = int(round(_spouse_fra_match.monthly_amount))

# Hydrate prior_year_magi from PDF cache (FinExtract wins; PDF fills historical gaps).
# merge_pdf_magi only fills absent/zero years so manual edits are preserved.

_pdf_records = load_pdf_tax_records()
if _pdf_records:
    st.session_state["prior_year_magi"] = merge_pdf_magi(
        st.session_state.get("prior_year_magi") or {},
        _pdf_records,
    )

st.sidebar.title("🎯 Roth Planner")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigate",
    [
        "⚙️ Setup",
        "📊 Dashboard",
        "📋 Conversion Planner",
        "💰 YTD Income",
        "📝 Option Exercise Planner",
        "🎯 Sweet Spot Finder",
        "📉 RMD Squeeze",
        "⚖️ Comparator",
        "🏥 ACA + IRMAA Explorer",
        "📦 Asset Location",
        "✅ Roth Eligibility",
        "🔗 Portfolio",
    ],
    label_visibility="collapsed",
)

# L6 (audit 0702): the generated V2 keypair is displayed only on the Setup page
# and must not linger in session_state after the user navigates away. data_bridge.py
# cannot self-clear at render-end — Streamlit reruns top-to-bottom and needs the key
# to survive until the user acts on it — so teardown happens here at the router.
if page != "⚙️ Setup":
    st.session_state.pop("_generated_pub_b64", None)
    st.session_state.pop("_generated_priv_b64", None)

# Build household from session state
from engine.dividend_forecast import forecast_portfolio  # noqa: E402
from engine.portfolio_sync import positions_for_forecast_multi  # noqa: E402
from models.household import GrowthProfile, Household, InheritedIRA, SurvivorScenario  # noqa: E402
from views.setup.parameters import apply_single_filer  # noqa: E402


def _build_survivor_scenario() -> SurvivorScenario | None:
    """Reconstruct SurvivorScenario from session_state dict (JSON-friendly storage)."""
    survivor_dict = st.session_state.get("survivor")
    if not survivor_dict or not isinstance(survivor_dict, dict):
        return None
    death_year = survivor_dict.get("death_year")
    if not death_year:
        return None
    return SurvivorScenario(
        who_dies=survivor_dict.get("who_dies", "you"),
        death_year=int(death_year),
    )


def get_household() -> Household:
    hh = Household(
        your_age=st.session_state.your_age,
        spouse_age=st.session_state.spouse_age,
        your_ira=st.session_state.your_ira,
        spouse_ira=st.session_state.spouse_ira,
        your_roth=st.session_state.get("your_roth", 0),
        spouse_roth=st.session_state.get("spouse_roth", 0),
        your_ss_fra=st.session_state.your_ss_fra,
        spouse_ss_fra=st.session_state.spouse_ss_fra,
        growth_rate=st.session_state.growth_rate / 100,
        living_expenses=st.session_state.living_expenses,
        txn_price_now=st.session_state.txn_price,
        your_aca_enrolled=st.session_state.your_aca,
        spouse_aca_enrolled=st.session_state.spouse_aca,
        aca_benchmark_premium_annual=st.session_state.get("aca_benchmark_premium_annual", 21_600.0),
        aca_enhanced_subsidies_active=st.session_state.get("aca_enhanced_subsidies_active", False),
        advance_aptc_annual=float(st.session_state.get("advance_aptc_annual", 0)),
        medicare_part_b_base_monthly=st.session_state.get(
            "medicare_part_b_base_monthly", BASE_PART_B / 12
        ),
        your_ss_start_age=st.session_state.get(
            "your_ss_start_age",
            st.session_state.get("ss_start_age", 70),
        ),
        spouse_ss_start_age=st.session_state.get(
            "spouse_ss_start_age",
            st.session_state.get("ss_start_age", 70),
        ),
        your_rmd_start_age=st.session_state.get(
            "your_rmd_start_age",
            st.session_state.get("rmd_start_age", 75),
        ),
        spouse_rmd_start_age=st.session_state.get(
            "spouse_rmd_start_age",
            st.session_state.get("rmd_start_age", 75),
        ),
        your_defer_first_rmd=st.session_state.get("your_defer_first_rmd", False),
        spouse_defer_first_rmd=st.session_state.get("spouse_defer_first_rmd", False),
        your_fra_age=st.session_state.get("your_fra_age", 67),
        spouse_fra_age=st.session_state.get("spouse_fra_age", 67),
        prior_year_magi={
            int(k): float(v) for k, v in st.session_state.get("prior_year_magi", {}).items() if v is not None and v != ""
        },
        cpi_assumption=float(st.session_state.get("cpi_assumption", 0.025)),
        filing_status=st.session_state.get("filing_status", "MFJ"),
        survivor=_build_survivor_scenario(),
        inherited_iras=[
            InheritedIRA(
                balance=float(e["balance"]),
                inherited_year=int(e["inherited_year"]),
                owner=str(e["owner"]),
                growth_rate=float(e.get("growth_rate", 0.07)),
            )
            for e in st.session_state.get("inherited_iras", [])
            if e.get("balance", 0) > 0
        ],
    )

    # If portfolio was synced, derive per-account growth and balances
    snap = st.session_state.get("portfolio_snapshot")
    if snap and snap.server_available:
        # Your pre-tax accounts (Rollover IRA + 403b) → your_ira balance & growth
        from engine.upload_merge import derive_ira_balances as _derive_ira
        from engine.upload_merge import derive_roth_balances as _derive_roth

        _your_pretax, _spouse_pretax = _derive_ira(snap)
        if _your_pretax > 0:
            hh.your_ira = _your_pretax
            hh.your_ira_growth = GrowthProfile(
                default_rate=snap.pretax_weighted_return_for("you"),
            )
        if _spouse_pretax > 0:
            hh.spouse_ira = _spouse_pretax
            hh.spouse_ira_growth = GrowthProfile(
                default_rate=snap.pretax_weighted_return_for("spouse"),
            )

        # Roth IRA accounts → your_roth / spouse_roth balance & growth.
        # PortfolioSnapshot has no roth_weighted_return property (only pretax_ and brokerage_),
        # so we compute the weighted return inline from filtered account lists.
        # If no Roth accounts exist in the snapshot, balances stay as session_state values.
        _your_roth_bal, _spouse_roth_bal = _derive_roth(snap)
        if _your_roth_bal > 0:
            hh.your_roth = _your_roth_bal
            _your_roth_accounts = [a for a in snap.accounts if a.owner == "you" and a.is_roth]
            _your_roth_return = (
                sum(a.total_value * a.weighted_return for a in _your_roth_accounts) / _your_roth_bal
                if _your_roth_accounts
                else hh.growth_rate
            )
            hh.your_roth_growth = GrowthProfile(default_rate=_your_roth_return)
        if _spouse_roth_bal > 0:
            hh.spouse_roth = _spouse_roth_bal
            _spouse_roth_accounts = [a for a in snap.accounts if a.owner == "spouse" and a.is_roth]
            _spouse_roth_return = (
                sum(a.total_value * a.weighted_return for a in _spouse_roth_accounts)
                / _spouse_roth_bal
                if _spouse_roth_accounts
                else hh.growth_rate
            )
            hh.spouse_roth_growth = GrowthProfile(default_rate=_spouse_roth_return)

        # Brokerage weighted return + dividend forecast (aggregate across all owners)
        brokerage_accounts = snap.brokerage_accounts
        brokerage_total = snap.brokerage_total
        if brokerage_accounts and brokerage_total > 0:
            _fcst = forecast_portfolio(
                positions_for_forecast_multi(brokerage_accounts),
                total_balance=brokerage_total,
            )
            hh.brokerage_growth = GrowthProfile(
                default_rate=snap.brokerage_weighted_return,
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

        strikes = st.session_state.get("_user_grant_strikes") or load_defaults().get(
            "grant_strikes", {}
        )
        merged_grants = []
        for g in snap.equity_grants:
            year = int(g.grant_date.split("-")[0]) if g.grant_date else 0
            strike = float(strikes.get(str(year), 0.0))
            if strike <= 0 or g.outstanding <= 0:
                continue  # skip grants without a known strike or fully exercised
            # NQO typically expires 10 years from grant date
            expires = year + 10
            merged_grants.append(
                StockGrant(
                    year=year,
                    strike=strike,
                    shares=g.outstanding,
                    expiry_year=expires,
                    grant_id=g.grant_id,
                )
            )
        if merged_grants:
            hh.grants = merged_grants

    from engine.exercise_schedule_store import load_exercise_schedule

    hh.exercise_schedule = load_exercise_schedule()

    return apply_single_filer(hh)


# Route to page
if page == "⚙️ Setup":
    from views import setup

    setup.render(get_household())
elif page == "📊 Dashboard":
    from views.dashboard import render

    render(get_household())
elif page == "📋 Conversion Planner":
    from views.planner import render

    render(get_household())
elif page == "💰 YTD Income":
    from views.ytd_income import render

    render(get_household())
elif page == "📝 Option Exercise Planner":
    from views.option_exercise import render

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
