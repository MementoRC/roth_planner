"""Roth Conversion Planner — Streamlit Application."""

import streamlit as st

st.set_page_config(
    page_title="Roth Conversion Planner",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)


from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from config.loader import load_defaults  # noqa: E402
from engine.data_sources.record import record_magi_candidates, record_ss_fra_candidate  # noqa: E402
from engine.irmaa import BASE_PART_B  # noqa: E402
from engine.tax_return_pdf import compute_irmaa_magi, load_pdf_tax_records  # noqa: E402
from engine.upload_merge import SCALAR_KEYS  # noqa: E402
from models.sourced import Source  # noqa: E402
from views import (  # noqa: E402
    shells,  # intentionally eager (not lazy/per-branch like other page views) — shells.THEMES needed by sidebar selectbox before page dispatch
)


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
    st.session_state.setdefault("txn_price_growth_rate", 7.0)
    st.session_state.setdefault("your_aca", False)
    st.session_state.setdefault("spouse_aca", False)
    # None = "derive" (national-average SLCSP, age-rated + CPI-indexed via
    # engine.aca.derive_couple_benchmark_annual); an explicit float (including
    # 0.0) is a household override used verbatim. Fresh installs default to
    # derive, not a hardcoded flat figure -- see models/household.py's
    # aca_benchmark_premium_annual docstring.
    st.session_state.setdefault("aca_benchmark_premium_annual", None)
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
    # Complex (non-scalar) persisted keys: seed from `defaults` so a saved
    # value survives a fresh session. (Audit 2026-07-22: these were hardcoded
    # to empty — survivor=None, inherited_iras=[], account_type_overrides
    # unseeded — so a restart silently reverted them to default, dropping the
    # survivor single-filer model, inherited-IRA 10-yr-rule income, and manual
    # account-type corrections.) prior_year_magi stays hardcoded above — it is
    # governed via Source.PDF/BUNDLE candidates, not user-defaults seeding.
    st.session_state.setdefault("survivor", defaults.get("survivor"))
    st.session_state.setdefault("inherited_iras", defaults.get("inherited_iras", []))
    st.session_state.setdefault("account_type_overrides", defaults.get("account_type_overrides", {}))
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
        # NOTE: sourced balance fields (your_ira/spouse_ira/your_roth/spouse_roth)
        # are deliberately NOT written to session_state here. get_household()
        # records this same snapshot as FINEXTRACT_LIVE candidates (Wave 3.1b)
        # and arbitrates them through the freeze-until-confirm gate; a direct
        # write here made reconcile_manual_edits see a "manual edit" diff
        # against the committed baseline and silently bypass the gate (audit
        # defect: FinExtract sync/autoload bypassed the candidate gate).
        st.session_state.portfolio_snapshot = _cached

if "ytd_snapshot" not in st.session_state and not st.session_state.get("_suppress_snapshot_autoload"):
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
                # Recorded as a FINEXTRACT_LIVE candidate, not a direct write —
                # your_ss_fra is a sourced field (Wave 2 Part C); a direct write
                # here would bypass the freeze-until-confirm gate exactly like
                # the portfolio_snapshot autoload above.
                record_ss_fra_candidate(
                    "your_ss_fra",
                    _your_fra_match.monthly_amount,
                    Source.FINEXTRACT_LIVE,
                    "SSA statement (cached)",
                    datetime.now(),
                )

if "ssa_snapshot_spouse" not in st.session_state:
    from engine.portfolio_sync import load_ssa_snapshot, match_fra_estimate

    _cached_ssa_spouse = load_ssa_snapshot(owner="spouse")
    if _cached_ssa_spouse is not None:
        st.session_state.ssa_snapshot_spouse = _cached_ssa_spouse
        _spouse_fra_age = st.session_state.get("spouse_fra_age")
        if _spouse_fra_age is not None:
            _spouse_fra_match = match_fra_estimate(_cached_ssa_spouse.estimates, _spouse_fra_age)
            if _spouse_fra_match is not None:
                record_ss_fra_candidate(
                    "spouse_ss_fra",
                    _spouse_fra_match.monthly_amount,
                    Source.FINEXTRACT_LIVE,
                    "SSA statement (cached)",
                    datetime.now(),
                )

# Record the on-disk 1040 PDF cache as Source.PDF candidates for Command
# Center review (Wave 5 — replaces the old merge_pdf_magi gap-fill directly
# into session_state["prior_year_magi"], which the reconcile step would have
# wrongly promoted to a MANUAL entry; audit defect #2).
# prior_year_magi is the IRMAA-scoped slot, so the recorded value is
# compute_irmaa_magi(agi, tax_exempt_interest) — NOT rec.magi (the
# FEIE-inclusive Roth/ACA flavor); feeding rec.magi here fabricated an IRMAA
# surcharge for filers with a foreign earned income exclusion (audit HIGH).
_pdf_records = load_pdf_tax_records()
if _pdf_records:
    record_magi_candidates(
        {
            yr: compute_irmaa_magi(rec.agi, rec.tax_exempt_interest)
            for yr, rec in _pdf_records.items()
        },
        Source.PDF,
        "1040 PDF cache",
        datetime.now(),
    )

st.sidebar.title("🎯 Roth Planner")
st.sidebar.markdown("---")

# UI-shell-theme-toggle plan (Task 10): live-swappable Setup-domain layouts.
# Deliberately session-local only, NOT persisted to .user_defaults.json — this
# is a UI display preference, not household financial data (unlike every
# other seeded key in _seed_session_state above, which round-trips through
# config.loader/SCALAR_KEYS). No existing precedent persists a UI-only
# setting, so per the plan's own scope this stays ephemeral; index=0 ("Classic")
# on every fresh session preserves today's exact default behavior.
st.sidebar.selectbox("Layout", shells.THEMES, key="ui_theme", index=0)

page = st.sidebar.radio(
    "Navigate",
    [
        "⚙️ Setup",
        "📊 Dashboard",
        "📋 Conversion Planner",
        "💰 YTD Income",
        "📝 Option Exercise Planner",
        "🧮 Exercise Auto-Optimizer",
        "🎯 Sweet Spot Finder",
        "📉 RMD Squeeze",
        "⚖️ Comparator",
        "🏥 ACA + IRMAA Explorer",
        "📦 Asset Location",
        "✅ Roth Eligibility",
        "🔗 Portfolio",
    ],
    label_visibility="collapsed",
    key="nav_page",
)

# Wave 4: pending-review badge for the Setup / Command Center gate. Reads
# whatever get_household() last populated into "_pending_review" — one
# render lag versus the current page (the sidebar renders before
# get_household() runs for this render), which is acceptable: the count
# simply catches up to the latest resolve() on the following rerun.
_pending_count = len(st.session_state.get("_pending_review") or ())
if _pending_count:
    st.sidebar.warning(
        f"⚠️ {_pending_count} data field(s) awaiting review — see Setup ▸ Command Center"
    )

# L6 (audit 0702): the generated V2 keypair is displayed only on the Setup page
# and must not linger in session_state after the user navigates away. data_bridge.py
# cannot self-clear at render-end — Streamlit reruns top-to-bottom and needs the key
# to survive until the user acts on it — so teardown happens here at the router.
if page != "⚙️ Setup":
    st.session_state.pop("_generated_pub_b64", None)
    st.session_state.pop("_generated_priv_b64", None)

# Build household from session state
from engine.data_sources.candidate_store import CandidateStore  # noqa: E402
from engine.data_sources.choices import ChoiceMap  # noqa: E402
from engine.data_sources.committed import (  # noqa: E402
    CorruptCommittedCacheError,
    load_committed,
    save_committed,
)
from engine.data_sources.orchestrator import (  # noqa: E402
    resolve_for_app,
    session_keys_for_writeback,
)

# Setup / Command Center cache paths (Wave 4: centralized in
# engine/data_sources/paths.py so views/setup/command_center.py can share
# them without importing from app.py).
from engine.data_sources.paths import (  # noqa: E402
    CANDIDATE_STORE_PATH,
    COMMITTED_PATH,
    TRUST_CHOICES_PATH,
)
from engine.data_sources.snapshot_ingest import derive_snapshot_growth  # noqa: E402
from engine.exercise_schedule_store import load_exercise_schedule  # noqa: E402
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
    session_hh = Household(
        your_age=st.session_state.your_age,
        spouse_age=st.session_state.spouse_age,
        your_has_workplace_plan=st.session_state.your_has_workplace_plan,
        spouse_has_workplace_plan=st.session_state.spouse_has_workplace_plan,
        your_ira=st.session_state.your_ira,
        spouse_ira=st.session_state.spouse_ira,
        your_roth=st.session_state.get("your_roth", 0),
        spouse_roth=st.session_state.get("spouse_roth", 0),
        your_ss_fra=st.session_state.your_ss_fra,
        spouse_ss_fra=st.session_state.spouse_ss_fra,
        growth_rate=st.session_state.growth_rate / 100,
        living_expenses=st.session_state.living_expenses,
        txn_price_now=st.session_state.txn_price,
        txn_price_growth=GrowthProfile(
            default_rate=float(st.session_state.get("txn_price_growth_rate", 7.0)) / 100
        ),
        your_aca_enrolled=st.session_state.your_aca,
        spouse_aca_enrolled=st.session_state.spouse_aca,
        # None-vs-0.0 must use `is not None`, not truthiness (0.0 is a legitimate
        # override); .get() with no default already returns None when unset.
        aca_benchmark_premium_annual=st.session_state.get("aca_benchmark_premium_annual"),
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
        spouse_is_sole_beneficiary=st.session_state.get("spouse_is_sole_beneficiary", False),
        your_fra_age=st.session_state.get("your_fra_age", 67),
        spouse_fra_age=st.session_state.get("spouse_fra_age", 67),
        prior_year_magi={
            int(k): float(v) for k, v in st.session_state.get("prior_year_magi", {}).items() if v is not None and v != ""
        },
        cpi_assumption=float(st.session_state.get("cpi_assumption", 0.025)),
        filing_status=st.session_state.get("filing_status", "MFJ"),
        survivor=_build_survivor_scenario(),
        inherited_iras=[
            iira
            for e in st.session_state.get("inherited_iras", [])
            if (iira := InheritedIRA.from_dict(e)) is not None
        ],
    )

    # Setup / Command Center: resolve sourced fields (your_ira, spouse_ira,
    # your_roth, spouse_roth, txn_price_now, your_ss_fra, spouse_ss_fra,
    # grants) against a frozen committed baseline instead of clobbering them
    # from the FinExtract snapshot on every render (Wave 3.1b — see
    # engine/data_sources/orchestrator.py; SS added in Wave 2 Part C).
    snap = st.session_state.get("portfolio_snapshot")
    strikes = st.session_state.get("_user_grant_strikes") or load_defaults().get("grant_strikes", {})

    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    # audit-0809 #11: a corrupt (e.g. truncated mid-write) committed baseline
    # must NOT be treated the same as "nothing committed yet" — see
    # CorruptCommittedCacheError's docstring. On a corrupt file we proceed
    # with committed_json=None (in-memory first-load path, so the app still
    # renders) but set _corrupt_committed_path so the save below is
    # suppressed for this run and the user is warned; the corrupt file on
    # disk is left completely untouched (no delete/rename/overwrite).
    _corrupt_committed_path: Path | None = None
    try:
        committed_json = load_committed(COMMITTED_PATH)
    except CorruptCommittedCacheError:
        committed_json = None
        _corrupt_committed_path = COMMITTED_PATH

    app_res = resolve_for_app(
        session_hh, snap, strikes, store, choices, committed_json, recorded_at=datetime.now()
    )
    hh = app_res.result.household

    # Growth profiles are NOT a sourced field — still derive them live from
    # the snapshot every load, exactly as before.
    if snap is not None and getattr(snap, "server_available", False):
        derive_snapshot_growth(hh, snap)

    # Mirror the resolved/committed sourced values back into session_state so
    # reconcile_manual_edits only ever fires on a genuine user edit (not a
    # stale snapshot-derived value), confirms from the Command Center stick,
    # and freshly-synced-but-not-yet-confirmed values sit pending correctly
    # rather than looking like a manual edit on the next render.
    for _attr, _session_key in session_keys_for_writeback().items():
        _value = getattr(hh, _attr, None)
        if _value is None:
            continue
        if _attr == "prior_year_magi":
            st.session_state[_session_key] = {int(_y): float(_v) for _y, _v in dict(_value).items()}
        else:
            # int, not float: every Setup number_input bound to these keys uses
            # format="%d"/int min_value/step (whole-dollar balances and stock
            # price), and Streamlit's number_input raises
            # StreamlitMixedNumericTypesError if `value` doesn't match those
            # types exactly — matches the int() cast the old direct-write code
            # already used for these same fields.
            st.session_state[_session_key] = int(round(_value))

    # Persist: write the migrated committed baseline only on first migration
    # (or when none existed), and always persist the candidate/choice stores.
    # audit-0809 #11: if the on-disk committed file was corrupt (see above),
    # app_res.committed_changed is still True here (resolve_for_app treated
    # committed_json=None as "first load" and freshly migrated one) — but
    # saving now would overwrite the still-intact corrupt file with the
    # in-memory migration, destroying whatever data was in it. save_committed()
    # itself now refuses this write unconditionally (the authoritative guard —
    # see its docstring), so this early skip is defence in depth / an earlier,
    # more specific warning message rather than the only thing preventing the
    # clobber; kept so the app.py-level warning below still fires.
    if app_res.committed_changed and _corrupt_committed_path is None:
        save_committed(COMMITTED_PATH, app_res.committed_json)
    elif _corrupt_committed_path is not None:
        st.warning(
            f"⚠️ Your committed baseline at `{_corrupt_committed_path}` is unreadable "
            "(corrupt or truncated) and could contain data with no other copy. It has "
            "been left untouched and will NOT be overwritten this session — restore it "
            "from a backup if you have one, or contact support before deleting it."
        )
    store.save(CANDIDATE_STORE_PATH)
    choices.save(TRUST_CHOICES_PATH)

    # Expose the review gate to the sidebar/Command Center.
    st.session_state["_pending_review"] = app_res.result.pending_review

    # Preserve the existing dropped-strike warning (grant with outstanding
    # shares but no configured strike — must not be silently hidden).
    if app_res.dropped_missing_strike:
        detail = ", ".join(f"{yr} ({sh:,} sh)" for yr, sh in sorted(app_res.dropped_missing_strike))
        st.warning(
            f"Ignored {len(app_res.dropped_missing_strike)} option grant(s) with "
            f"outstanding shares but no configured strike price: {detail}. Add a "
            "strike for these grant years (grant_strikes in your data-bridge "
            "upload or .user_defaults.json) so they appear in the planner."
        )

    hh.exercise_schedule = load_exercise_schedule()

    return apply_single_filer(hh)


# Route to page
if page == "⚙️ Setup":
    shells.render_setup(get_household(), st.session_state["ui_theme"])
elif page == "📊 Dashboard":
    from views.dashboard import render

    render(get_household())
elif page == "📋 Conversion Planner":
    from views.planner import render

    render(get_household())
elif page == "💰 YTD Income":
    from views.ytd_income import render

    render(get_household(), st.session_state["ui_theme"])
elif page == "📝 Option Exercise Planner":
    from views.option_exercise import render

    render(get_household(), st.session_state["ui_theme"])
elif page == "🧮 Exercise Auto-Optimizer":
    from views.auto_optimizer import render

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
