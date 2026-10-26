"""Roth Conversion Planner — Streamlit Application."""

import streamlit as st

st.set_page_config(
    page_title="Roth Conversion Planner",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

from views._style import inject_app_css  # noqa: E402

inject_app_css()


from collections.abc import Callable  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import TypeVar  # noqa: E402

from config.loader import load_defaults  # noqa: E402
from config.scalar_registry import (  # noqa: E402
    SCALAR_REGISTRY,
    scalar_default,
    session_key_for_config_key,
)
from engine.data_sources.candidate_store import CorruptCandidateStoreError  # noqa: E402
from engine.data_sources.record import record_magi_candidates, record_ss_fra_candidate  # noqa: E402
from engine.demo_mode import is_demo_mode  # noqa: E402
from engine.instance_identity import CorruptInstanceOwnerError, load_instance_owner  # noqa: E402
from engine.store import StoreError, StoreUnavailableError, get_store, saving_disabled  # noqa: E402
from engine.tax_return_pdf import compute_irmaa_magi, load_pdf_tax_records  # noqa: E402
from engine.upload_merge import SCALAR_KEYS, sanitize_living_expenses  # noqa: E402
from models.sourced import Source  # noqa: E402
from views.setup._state import _drain_pending_defaults  # noqa: E402
from views.store_notice import render_store_notice, store_error_message  # noqa: E402

_T = TypeVar("_T")
# Store errors met by this run's boot-time reads; only the first gets a banner.
_boot_store_errors: list[StoreError] = []


def _boot_read(read: Callable[[], _T], default: _T) -> _T:
    """Run a boot-time store read; a store error falls back to *default* with one banner."""
    try:
        return read()
    except StoreError as exc:
        if not _boot_store_errors:
            st.error(store_error_message(exc))
        _boot_store_errors.append(exc)
        return default


def _report_refused_save(exc: StoreUnavailableError) -> None:
    """A refused save: quiet while a failed import disabled saving, else the one banner."""
    if saving_disabled():
        return  # render_store_notice() already shows the failed-import banner
    if not _boot_store_errors:
        st.error(store_error_message(exc))
    _boot_store_errors.append(exc)


_CORRUPT_CANDIDATE_STORE_WARNING = (
    "⚠️ Your candidate store is unreadable (corrupt or truncated) and could "
    "contain data with no other copy. It has been left untouched and will NOT be "
    "overwritten this session — restore it from a backup if you have one, or "
    "contact support before deleting it."
)
# Set once this run has shown the corrupt-candidate-store warning.
_candidate_store_warned: list[bool] = []


def _warn_corrupt_candidate_store() -> None:
    """Warn that the saved candidate store is unreadable, at most once per run."""
    if not _candidate_store_warned:
        st.warning(_CORRUPT_CANDIDATE_STORE_WARNING)
        _candidate_store_warned.append(True)


def _boot_record(record: Callable[[], object]) -> None:
    """Record a boot-time candidate; a refused save is dropped (see _report_refused_save).

    A corrupt saved candidate store refuses the save too: the row is kept and
    the user gets the corrupt-candidate-store warning instead of a traceback.
    """
    try:
        record()
    except StoreUnavailableError as exc:
        _report_refused_save(exc)
    except CorruptCandidateStoreError:
        _warn_corrupt_candidate_store()


def _seed_session_state() -> None:
    """Seed session state from synthetic defaults (or user overrides)."""
    if st.session_state.get("_seeded"):
        return
    defaults = load_defaults()
    # Map config keys to session_state keys (most are 1:1; stock_price_now is
    # aliased to stock_price because session_state uses that name even after
    # the gate). Driven by engine.upload_merge.SCALAR_KEYS — the canonical
    # list of persisted scalar setup fields — so seeding stays in sync with
    # what export/import round-trips. (Audit 2026-07-13: this used to be a
    # hand-list covering only ~10 of the 27 scalars; filing_status,
    # growth_rate, and 15 others were hardcoded below and silently discarded
    # any persisted value on every fresh session.)
    session_keys = {k: session_key_for_config_key(k) for k in SCALAR_KEYS}
    for cfg_key, sess_key in session_keys.items():
        if cfg_key in defaults:
            st.session_state.setdefault(sess_key, defaults[cfg_key])
    # Fallback defaults for scalars with no persisted value (first-run demo).
    # setdefault() below is a no-op for any key already seeded from `defaults`
    # above. Values/notes (e.g. aca_benchmark_premium_annual's None="derive"
    # semantics) live in config.scalar_registry.SCALAR_REGISTRY (issue #498),
    # the single source of truth for every scalar's default -- this loop
    # covers all of SCALAR_KEYS plus prior_year_magi (seeded here, but not a
    # SCALAR_KEYS/persisted-export field; see that registry entry's note).
    for _spec in SCALAR_REGISTRY:
        st.session_state.setdefault(_spec.session_key, scalar_default(_spec.session_key))
    # A persisted file can carry NaN/negative expenses (json.loads accepts NaN);
    # sanitize at the source so no page ever computes on them (audit-1018 F-33).
    st.session_state["living_expenses"] = sanitize_living_expenses(
        st.session_state["living_expenses"]
    )
    # Complex (non-scalar) persisted keys: seed from `defaults` so a saved
    # value survives a fresh session. (Audit 2026-07-22: these were hardcoded
    # to empty — survivor=None, inherited_iras=[], account_type_overrides
    # unseeded — so a restart silently reverted them to default, dropping the
    # survivor single-filer model, inherited-IRA 10-yr-rule income, and manual
    # account-type corrections.) prior_year_magi is seeded by the registry
    # loop above, not here — it is governed via Source.PDF/BUNDLE candidates,
    # not user-defaults seeding.
    st.session_state.setdefault("survivor", defaults.get("survivor"))
    st.session_state.setdefault("inherited_iras", defaults.get("inherited_iras", []))
    st.session_state.setdefault(
        "account_type_overrides", defaults.get("account_type_overrides", {})
    )
    # The employer ticker (``_stock_ticker``) is a registry scalar since
    # audit-1018 F-34, so the SCALAR_KEYS loop above seeds it and export/import
    # carries it.
    # 2026-08-29: instance identity is set once per machine/install (see
    # engine.instance_identity) and is never silently re-derived on a
    # corrupt cache -- a corrupt file degrades to None here (same "unset"
    # treatment as a first-run install) so Command Center's identity gate
    # (views/setup/command_center.py) can re-prompt instead of the whole
    # app crashing on session start.
    try:
        st.session_state.setdefault("instance_owner", load_instance_owner())
    except CorruptInstanceOwnerError:
        st.session_state.setdefault("instance_owner", None)
    except StoreUnavailableError as exc:
        # Saving disabled by a failed import: its banner is already shown.
        # Any other unavailable store gets the storage banner (once).
        _report_refused_save(exc)
        st.session_state.setdefault("instance_owner", None)
    # Issue #43: remember what the governed fields were seeded with so the
    # first resolve does not read an untouched seed as a manual edit.
    from engine.data_sources.orchestrator import SEED_VALUES_KEY, SOURCED_SESSION_KEYS

    st.session_state[SEED_VALUES_KEY] = {
        attr: st.session_state[key]
        for attr, key in SOURCED_SESSION_KEYS.items()
        if attr != "prior_year_magi" and isinstance(st.session_state.get(key), (int, float))
    }
    st.session_state.setdefault("_seeded", True)


# Open the store before any cached snapshot is read: the first open imports the
# legacy JSON files, and an unavailable database stops here with one banner.
render_store_notice()

# Shared state: household parameters
_seed_session_state()

# Drain any user-defaults writes deferred by _apply_user_defaults_to_session
# (data-bridge upload handler) on the PRIOR script run. Must run before any
# st.* widget is instantiated — see views/setup/_state.py::_drain_pending_defaults
# for why (Streamlit forbids assigning to a widget-backed session_state key
# after that widget already exists in the run).
_drain_pending_defaults()

# Demo mode (audit-1018 D2, set by "Reset to demo"): every disk autoload of a
# personal cache below is skipped, so the reset cannot be silently undone and
# the personal files stay parked on disk until the user leaves demo mode.
_DEMO_MODE = is_demo_mode()

# Load cached snapshots on first run (silently — Setup page shows status)
if (
    not _DEMO_MODE
    and "portfolio_snapshot" not in st.session_state
    and not st.session_state.get("_suppress_snapshot_autoload")
):
    from engine.portfolio_sync import load_snapshot

    _cached = _boot_read(load_snapshot, None)
    if _cached is not None:
        # NOTE: sourced balance fields (your_ira/spouse_ira/your_roth/spouse_roth)
        # are deliberately NOT written to session_state here. get_household()
        # records this same snapshot as FINEXTRACT_LIVE candidates (Wave 3.1b)
        # and arbitrates them through the freeze-until-confirm gate; a direct
        # write here made reconcile_manual_edits see a "manual edit" diff
        # against the committed baseline and silently bypass the gate (audit
        # defect: FinExtract sync/autoload bypassed the candidate gate).
        st.session_state.portfolio_snapshot = _cached

if (
    not _DEMO_MODE
    and "ytd_snapshot" not in st.session_state
    and not st.session_state.get("_suppress_snapshot_autoload")
):
    from engine.portfolio_sync import load_ytd_snapshot

    _cached_ytd = _boot_read(load_ytd_snapshot, None)
    if _cached_ytd is not None:
        st.session_state.ytd_snapshot = _cached_ytd

if not _DEMO_MODE and "ssa_snapshot_you" not in st.session_state:
    from engine.portfolio_sync import load_ssa_snapshot, match_fra_estimate

    _cached_ssa_you = _boot_read(lambda: load_ssa_snapshot(owner="you"), None)
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
                _boot_record(
                    lambda: record_ss_fra_candidate(
                        "your_ss_fra",
                        _your_fra_match.monthly_amount,
                        Source.FINEXTRACT_LIVE,
                        "SSA statement (cached)",
                        datetime.now(),
                    )
                )

if not _DEMO_MODE and "ssa_snapshot_spouse" not in st.session_state:
    from engine.portfolio_sync import load_ssa_snapshot, match_fra_estimate

    _cached_ssa_spouse = _boot_read(lambda: load_ssa_snapshot(owner="spouse"), None)
    if _cached_ssa_spouse is not None:
        st.session_state.ssa_snapshot_spouse = _cached_ssa_spouse
        _spouse_fra_age = st.session_state.get("spouse_fra_age")
        if _spouse_fra_age is not None:
            _spouse_fra_match = match_fra_estimate(_cached_ssa_spouse.estimates, _spouse_fra_age)
            if _spouse_fra_match is not None:
                _boot_record(
                    lambda: record_ss_fra_candidate(
                        "spouse_ss_fra",
                        _spouse_fra_match.monthly_amount,
                        Source.FINEXTRACT_LIVE,
                        "SSA statement (cached)",
                        datetime.now(),
                    )
                )

# Record the on-disk 1040 PDF cache as Source.PDF candidates for Command
# Center review (Wave 5 — replaces the old merge_pdf_magi gap-fill directly
# into session_state["prior_year_magi"], which the reconcile step would have
# wrongly promoted to a MANUAL entry; audit defect #2).
# prior_year_magi is the IRMAA-scoped slot, so the recorded value is
# compute_irmaa_magi(agi, tax_exempt_interest, feie) — 42 U.S.C. §1395r(i)(4)
# adds the FEIE back (audit-1008 F-59).
_pdf_records = {} if _DEMO_MODE else _boot_read(load_pdf_tax_records, {})
if _pdf_records:
    _boot_record(
        lambda: record_magi_candidates(
            {
                yr: compute_irmaa_magi(rec.agi, rec.tax_exempt_interest, rec.feie)
                for yr, rec in _pdf_records.items()
            },
            Source.PDF,
            "1040 PDF cache",
            datetime.now(),
        )
    )

st.sidebar.title("🎯 Roth Planner")
st.sidebar.markdown("---")

from views.navigation import label_of, render_sidebar_nav, route_target  # noqa: E402

page = render_sidebar_nav()

# Wave 4: pending-review badge for the Setup / Command Center gate. The slot
# is reserved here so the sidebar order is unchanged, but it is filled only
# AFTER the page body has run (see the routing block at the bottom), so the
# count reflects candidates the page body recorded or confirmed in this same
# run (audit-1008 browser pass, #602).
_pending_badge_slot = st.sidebar.empty()

from views.sidebar_status import render_projection_toggle  # noqa: E402

render_projection_toggle()

# L6 (audit 0702): the generated V2 keypair is displayed only on the Backup & Transfer
# page and must not linger in session_state after the user navigates away. data_bridge.py
# cannot self-clear at render-end — Streamlit reruns top-to-bottom and needs the key
# to survive until the user acts on it — so teardown happens here at the router.
if page != "backup":
    st.session_state.pop("_generated_pub_b64", None)
    st.session_state.pop("_generated_priv_b64", None)

# Build household from session state
from engine.data_sources.candidate_store import CandidateStore  # noqa: E402
from engine.data_sources.choices import ChoiceMap, CorruptChoiceMapError  # noqa: E402
from engine.data_sources.committed import (  # noqa: E402
    CorruptCommittedCacheError,
    apply_committed,
    load_committed,
    save_committed,
)
from engine.data_sources.orchestrator import (  # noqa: E402
    BUNDLE_SCALARS_KEY,
    MAGI_CLEARED_KEY,
    SEED_VALUES_KEY,
    AppResolveResult,
    resolve_for_app,
    session_keys_for_writeback,
)
from engine.data_sources.resolver import resolve  # noqa: E402
from engine.data_sources.snapshot_ingest import (  # noqa: E402
    derive_snapshot_growth,
    snapshot_carries_portfolio_data,
)
from engine.exercise_schedule_store import load_exercise_schedule  # noqa: E402
from models.household import GrowthProfile, Household, InheritedIRA, SurvivorScenario  # noqa: E402
from views.option_exercise._partials._helpers import configured_ticker  # noqa: E402
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


_run_state: dict[str, Household] = {}  # per script run (Streamlit re-executes this module)


def _current_pending_review() -> set[str]:
    """Pending-review field keys as of NOW (after the page body ran).

    Re-runs the pure ``resolve()`` against the saved committed baseline,
    candidate store and trust choices, so candidates recorded or confirmed by
    the page body in this run are counted. Falls back to the value
    get_household() published when state is missing or unreadable.
    """
    published = set(st.session_state.get("_pending_review") or ())
    base = _run_state.get("baseline_hh")
    if base is None or _DEMO_MODE:
        return published
    try:
        committed_json = load_committed()
        store = CandidateStore.load()
        choices = ChoiceMap.load()
    except CorruptCommittedCacheError:
        # The candidate/choice loaders turn a corrupt row into a flag; a corrupt
        # committed baseline is already warned about by get_household().
        return published
    except StoreUnavailableError as exc:
        # Runs in _route_and_badge's finally, outside its StoreError catch, so
        # the refusal goes through the one-banner rule (quiet while saving is
        # disabled by a failed import).
        _report_refused_save(exc)
        return published
    if committed_json is not None:
        apply_committed(base, committed_json)
    return set(resolve(base, store, choices).pending_review)


def _render_pending_badge(pending: set[str]) -> None:
    """Fill the sidebar review badge slot reserved before the page body."""
    if pending:
        _pending_badge_slot.warning(
            f"⚠️ {len(pending)} data field(s) awaiting review — see {label_of('review')}"
        )
    else:
        _pending_badge_slot.empty()


def _persist_resolution(
    app_res: AppResolveResult,
    store: CandidateStore,
    choices: ChoiceMap,
    committed_unreadable: bool,
) -> None:
    """Save the committed baseline, candidate store and trust choices to the store.

    Skipped in demo mode (audit-1018 F-28): the state is built from demo seeds,
    and saving it would stamp them MANUAL. When a failed JSON import has
    disabled saving, the refusal is quiet, because render_store_notice()
    already shows that banner on every run. Any other unavailable-storage
    error gets the one storage banner (shared with the boot-time reads).
    """
    if _DEMO_MODE:
        return
    try:
        _save_resolution(app_res, store, choices, committed_unreadable)
    except StoreUnavailableError as exc:
        _report_refused_save(exc)


def _save_resolution(
    app_res: AppResolveResult,
    store: CandidateStore,
    choices: ChoiceMap,
    committed_unreadable: bool,
) -> None:
    # One transaction (spec §2, the autosave): the committed baseline, the
    # candidate store and the trust choices commit together. The two
    # corrupt-row refusals below are caught INSIDE it, so the other saves still
    # commit, as before; any other store error rolls all three back and
    # _persist_resolution reports it.
    with get_store().transaction():
        # Persist: write the migrated committed baseline only on first migration
        # (or when none existed), and always persist the candidate/choice stores.
        # audit-0809 #11: if the saved committed baseline was corrupt (see above),
        # app_res.committed_changed is still True here (resolve_for_app treated
        # committed_json=None as "first load" and freshly migrated one) — but
        # saving now would overwrite the still-intact corrupt file with the
        # in-memory migration, destroying whatever data was in it. save_committed()
        # itself now refuses this write unconditionally (the authoritative guard —
        # see its docstring), so this early skip is defence in depth / an earlier,
        # more specific warning message rather than the only thing preventing the
        # clobber; kept so the app.py-level warning below still fires.
        if app_res.committed_changed and not committed_unreadable:
            save_committed(app_res.committed_json)
        elif committed_unreadable:
            st.warning(
                "⚠️ Your committed baseline is unreadable (corrupt or truncated) and could "
                "contain data with no other copy. It has been left untouched and will NOT be "
                "overwritten this session — restore it from a backup if you have one, or "
                "contact support before deleting it."
            )
        # audit-0823: mirror the committed-baseline guard above — a corrupt
        # saved candidate store must not be silently clobbered by the
        # in-memory store (which, per CandidateStore.load's resilience, would
        # be EMPTY on a corrupt read). store.save() itself refuses this write
        # (see CorruptCandidateStoreError's docstring); this except is what
        # surfaces that refusal to the user instead of it failing silently.
        try:
            store.save()
        except CorruptCandidateStoreError:
            _warn_corrupt_candidate_store()
        # audit-1008 F-45: same guard for the trust-choice map.
        try:
            choices.save()
        except CorruptChoiceMapError:
            st.warning(
                "⚠️ Your saved source choices are unreadable (corrupt or truncated). They "
                "have been left untouched and will NOT be overwritten this session — restore "
                "them from a backup if you have one."
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
        stock_price_now=st.session_state.stock_price,
        stock_price_growth=GrowthProfile(
            default_rate=float(
                st.session_state.get(
                    "stock_price_growth_rate", scalar_default("stock_price_growth_rate")
                )
            )
            / 100
        ),
        your_aca_enrolled=st.session_state.your_aca,
        spouse_aca_enrolled=st.session_state.spouse_aca,
        # None-vs-0.0 must use `is not None`, not truthiness (0.0 is a legitimate
        # override); .get() with no default already returns None when unset.
        aca_benchmark_premium_annual=st.session_state.get("aca_benchmark_premium_annual"),
        aca_enhanced_subsidies_active=st.session_state.get(
            "aca_enhanced_subsidies_active", scalar_default("aca_enhanced_subsidies_active")
        ),
        advance_aptc_annual=float(
            st.session_state.get("advance_aptc_annual", scalar_default("advance_aptc_annual"))
        ),
        medicare_part_b_base_monthly=st.session_state.get(
            "medicare_part_b_base_monthly", scalar_default("medicare_part_b_base_monthly")
        ),
        your_ss_start_age=st.session_state.get(
            "your_ss_start_age",
            st.session_state.get("ss_start_age", scalar_default("your_ss_start_age")),
        ),
        spouse_ss_start_age=st.session_state.get(
            "spouse_ss_start_age",
            st.session_state.get("ss_start_age", scalar_default("spouse_ss_start_age")),
        ),
        your_rmd_start_age=st.session_state.get(
            "your_rmd_start_age",
            st.session_state.get("rmd_start_age", scalar_default("your_rmd_start_age")),
        ),
        spouse_rmd_start_age=st.session_state.get(
            "spouse_rmd_start_age",
            st.session_state.get("rmd_start_age", scalar_default("spouse_rmd_start_age")),
        ),
        your_defer_first_rmd=st.session_state.get(
            "your_defer_first_rmd", scalar_default("your_defer_first_rmd")
        ),
        spouse_defer_first_rmd=st.session_state.get(
            "spouse_defer_first_rmd", scalar_default("spouse_defer_first_rmd")
        ),
        spouse_is_sole_beneficiary=st.session_state.get(
            "spouse_is_sole_beneficiary", scalar_default("spouse_is_sole_beneficiary")
        ),
        your_fra_age=st.session_state.get("your_fra_age", scalar_default("your_fra_age")),
        spouse_fra_age=st.session_state.get("spouse_fra_age", scalar_default("spouse_fra_age")),
        prior_year_magi={
            int(k): float(v)
            for k, v in st.session_state.get(
                "prior_year_magi", scalar_default("prior_year_magi")
            ).items()
            if v is not None and v != ""
        },
        cpi_assumption=float(
            st.session_state.get("cpi_assumption", scalar_default("cpi_assumption"))
        ),
        filing_status=st.session_state.get("filing_status", scalar_default("filing_status")),
        survivor=_build_survivor_scenario(),
        inherited_iras=[
            iira
            for e in st.session_state.get("inherited_iras", [])
            if (iira := InheritedIRA.from_dict(e)) is not None
        ],
    )

    # Setup / Command Center: resolve sourced fields (your_ira, spouse_ira,
    # your_roth, spouse_roth, stock_price_now, your_ss_fra, spouse_ss_fra,
    # grants) against a frozen committed baseline instead of clobbering them
    # from the FinExtract snapshot on every render (Wave 3.1b — see
    # engine/data_sources/orchestrator.py; SS added in Wave 2 Part C).
    snap = st.session_state.get("portfolio_snapshot")
    strikes = st.session_state.get("_user_grant_strikes") or load_defaults().get(
        "grant_strikes", {}
    )

    # Demo mode (audit-1018 F-28): the personal committed baseline, candidate
    # store and trust choices stay parked in the real store. Resolve against empty
    # in-memory ones (the "first load" path, so the demo seeds are the baseline)
    # and write none of them back -- demo values must never overwrite personal
    # files or be stamped MANUAL there.
    store = CandidateStore() if _DEMO_MODE else CandidateStore.load()
    choices = ChoiceMap() if _DEMO_MODE else ChoiceMap.load()
    # audit-0809 #11: a corrupt (e.g. truncated mid-write) committed baseline
    # must NOT be treated the same as "nothing committed yet" — see
    # CorruptCommittedCacheError's docstring. On a corrupt baseline we proceed
    # with committed_json=None (in-memory first-load path, so the app still
    # renders) but set _committed_unreadable so the save below is
    # suppressed for this run and the user is warned; the unreadable row is
    # left untouched.
    _committed_unreadable = False
    committed_json: dict | None = None
    if not _DEMO_MODE:
        try:
            committed_json = load_committed()
        except CorruptCommittedCacheError:
            _committed_unreadable = True

    # audit-1008 F-64: years the Setup MAGI-anchor form set back to 0.
    _cleared_magi = st.session_state.get(MAGI_CLEARED_KEY) or ()
    app_res = resolve_for_app(
        session_hh,
        snap,
        strikes,
        store,
        choices,
        committed_json,
        recorded_at=datetime.now(),
        cleared_magi_years=_cleared_magi,
        unedited_seed=st.session_state.get(SEED_VALUES_KEY),
        bundle_scalars=st.session_state.get(BUNDLE_SCALARS_KEY),
    )
    hh = app_res.result.household
    # Committed-baseline household (post apply_committed) kept for the
    # post-page-body pending recompute; resolve() does not mutate it.
    _run_state["baseline_hh"] = session_hh
    # The clear only sticks once the baseline is saved; on a corrupt baseline
    # (save suppressed below) keep the flag so it applies on a later render.
    if not _committed_unreadable:
        st.session_state.pop(MAGI_CLEARED_KEY, None)
        st.session_state.pop(SEED_VALUES_KEY, None)
        st.session_state.pop(BUNDLE_SCALARS_KEY, None)

    # Growth profiles are NOT a sourced field — still derive them live from
    # the snapshot every load. A data-bridge import counts too (audit-1008
    # F-01): it never sets server_available, which left imported accounts on
    # the flat default growth with no dividend yield.
    if snapshot_carries_portfolio_data(snap):
        derive_snapshot_growth(hh, snap, employer_ticker=configured_ticker())

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

    _persist_resolution(app_res, store, choices, _committed_unreadable)

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


def _route_and_badge() -> None:
    """Route to the page (lazy import; see views/navigation.ROUTES) and badge."""
    hh = get_household()
    # Provisional badge (this run's resolve) so it is drawn even if the page
    # body halts the script; overwritten with the post-body count below.
    _render_pending_badge(set(st.session_state.get("_pending_review") or ()))
    try:
        route_target(page)(hh)
    except StoreError as exc:
        st.error(store_error_message(exc))
        # Counted as this run's banner, so the pending recompute below adds none.
        _boot_store_errors.append(exc)
    finally:
        pending = _current_pending_review()
        st.session_state["_pending_review"] = pending
        _render_pending_badge(pending)


_route_and_badge()
