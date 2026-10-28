"""Shared session-state helpers — user defaults, portfolio snapshot, personal-state clear."""

from __future__ import annotations

import streamlit as st

from config.loader import save_user_defaults
from config.scalar_registry import session_key_for_config_key
from engine.demo_mode import enter_demo_mode, leave_demo_mode
from engine.portfolio_sync import merge_snapshots
from engine.portfolio_sync.shapes import portfolio_snapshot_from_dict
from engine.upload_merge import SCALAR_KEYS, build_user_defaults_session_updates


def _build_user_defaults_session_updates(data: dict, *, as_spouse: bool) -> dict:
    """Compute session_state updates from a .user_defaults.json payload.

    Thin wrapper around :func:`engine.upload_merge.build_user_defaults_session_updates`.
    Pure function — returns a ``{session_key: value}`` dict without writing to state.
    See the engine module for full mapping rules.

    On the spouse path it supplies the receiver's RAW persisted overrides, which
    is what lets joint household fields cross when the receiver has never set
    them. Read through :func:`config.loader.load_raw_user_defaults` (not
    ``load_defaults``, which overlays DEFAULTS and would make three joint fields
    look set on a fresh install) and imported lazily so the module attribute can
    be monkeypatched in tests.
    """
    receiver_persisted: dict | None = None
    if as_spouse:
        from config import loader

        receiver_persisted = loader.load_raw_user_defaults()
    return build_user_defaults_session_updates(
        data, as_spouse=as_spouse, receiver_persisted=receiver_persisted
    )


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
        # Reverse the alias: session stores stock_price / _stock_ticker, the JSON
        # schema expects stock_price_now / stock_ticker.
        sess_key = session_key_for_config_key(k)
        if sess_key in st.session_state:
            payload[k] = st.session_state[sess_key]
    # grant_strikes DELIBERATELY keeps a truthy-only guard (unlike the keys
    # below). Strikes are hand-editable in .user_defaults.json and an uploaded
    # bundle carrying `grant_strikes: {}` sets this key to an empty dict
    # (engine/upload_merge.py:95-96). Emitting that empty map would merge
    # `grant_strikes: {}` over the file and drop the hand-edited strikes -- the
    # exact regression config/loader.py:134-139 documents (the dropped 2019
    # grant). Do not "normalise" this to a presence guard.
    strikes = st.session_state.get("_user_grant_strikes")
    if strikes:
        payload["grant_strikes"] = strikes
    # Emit account_type_overrides / prior_year_magi whenever the session knows
    # about them, even when cleared to {}, for the same reason survivor /
    # inherited_iras are emitted below: save_user_defaults merges onto the
    # existing file, so a truthy-only guard lets a stale on-disk value survive a
    # clear and be resurrected on the next startup (audit-0802 F4/F5;
    # audit-0823 state/TRUTHY-GUARD). prior_year_magi is the leg with teeth --
    # "Leave 0 to use projected MAGI" pops both years and writes {}, and the
    # resurrected anchor silently re-anchors the IRMAA 2-year lookback.
    overrides = st.session_state.get("account_type_overrides")
    if overrides is not None:
        payload["account_type_overrides"] = overrides
    prior_magi = st.session_state.get("prior_year_magi")
    if prior_magi is not None:
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


def autosave_user_defaults() -> None:
    """Persist the session's current household/account/assumption fields to
    .user_defaults.json, honouring the same suppress guard
    render_parameters_tab (now deleted) used to have.

    Pulled out of views/setup/parameters.py so every Setup shell can call it
    (audit-0823 models-views/M2) -- Domains/Hub/Wizard compose
    views/setup/_partials/ directly and never routed through the
    now-deleted render_parameters_tab, so they never reached this save at
    all. Session edits made through those three shells vanished on restart.
    Callers should invoke this as the LAST statement of their render(), same
    position render_parameters_tab used to use before it was retired.

    save_user_defaults merges the returned dict onto whatever is already on
    disk, so a partial payload (e.g. the Wizard renders one step's fields per
    run) only updates the keys the current run actually knows about and
    leaves the rest of the file untouched.
    """
    if st.session_state.get("_suppress_snapshot_autoload"):
        return
    save_user_defaults(_user_defaults_from_session())


def _portfolio_snapshot_from_dict(data: dict) -> object:
    """Reconstruct a PortfolioSnapshot from its asdict() JSON form.

    Delegates to the pure engine helper so the bridge v5 parser shares it.
    """
    return portfolio_snapshot_from_dict(data)


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


# Dynamic session_state key families that carry personal data in the key NAME
# itself (an account number, an inherited-IRA row index, or a governance field
# key), so they can never be named in a static clear list.
# See :func:`_clear_personal_session_state`.
_PERSONAL_KEY_PREFIXES = (
    # Conversion Planner per-year conversion / QCD widgets (views/conversion_state.py clear_conversion_widgets).
    "yc_",
    "sc_",
    "qcd_",
    "sp_qcd_",
    # Inherited-IRA rows (views/setup/_partials/_assumptions.py:170-203). The
    # sibling iira_add / iira_remove_<idx> keys are buttons and are not swept.
    "iira_balance_",
    "iira_year_",
    "iira_rate_",
    "iira_owner_",
    # Per-account type/owner overrides (views/setup/_partials/_portfolio.py:179-186).
    # These are the per-widget keys behind the "account_type_overrides" aggregate
    # cleared above, which on its own left them behind.
    "_override_type_",
    "_override_owner_",
    # Per-account attribution controls (views/setup/command_center.py:90,112).
    "attribution_owner_",
    "attribution_clear_",
    # Per-account type confirmation (views/ytd_income/_partials/_sync_scan.py:311).
    "account_type_confirm_",
    # Governance trust-source radio and manual dollar override
    # (views/setup/_partials/_governance.py:195,206). The sibling confirm_<field>
    # key is a button holding no value and is deliberately NOT swept.
    "trust_",
    "manual_",
    # YTD manual-entry widgets, per field and person (redesign PR 1b,
    # views/ytd_income/_partials/_manual_entry.py _widget_key/_as_of_key/_note_key).
    "ytd_w_",
    "ytd_manual_as_of_",
    "ytd_manual_note_",
)


def _clear_session_for_reseed() -> None:
    """Drop every personal session key (and the queued statements) so the next
    run re-seeds from whatever defaults are visible at that time."""
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
        "your_rmd_age_chosen",
        "spouse_rmd_age_chosen",
        "your_fra_age",
        "spouse_fra_age",
        "living_expenses",
        "stock_price",
        "aca_benchmark_premium_annual",
        "aca_enhanced_subsidies_active",
        "advance_aptc_annual",
        "medicare_part_b_base_monthly",
        "cpi_assumption",
        # audit-1018 F-29: the exercise optimizer's terminal tax rate is a
        # registry scalar (Setup > Assumptions); it survived the reset.
        "terminal_ira_tax_rate",
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
        # Redesign PR 1b: unsaved manual-entry drafts and the person toggle.
        # Draft values and notes are personal figures/text the user typed.
        "ytd_draft",
        "ytd_draft_meta",
        "ytd_manual_owner",
        "ytd_manual_owner_choice",
        # audit-0705 ui-5: these keys are seeded via setdefault in app.py but were
        # missing from this list, causing personal values to leak into the demo.
        "your_aca",
        "spouse_aca",
        "your_defer_first_rmd",
        "spouse_defer_first_rmd",
        "growth_rate",
        "stock_price_growth_rate",
        # C35 (audit-0721 W5): workplace-plan/beneficiary keys were missing,
        # so they leaked personal values into a demo-mode reset.
        "your_has_workplace_plan",
        "spouse_has_workplace_plan",
        "spouse_is_sole_beneficiary",
        # audit-0722b: net_inv_income (the NII input, now edited in
        # Setup › Assumptions) was missing from this list, so a
        # personal value survived "Reset to demo" and kept inflating NIIT.
        "net_inv_income",
        # audit-0823 models-views/M4: every key below survived "Reset to demo"
        # and re-infected the demo household. Grouped by the page that writes it.
        #
        # YTD income & headroom. income_events is the filed finding: it is read
        # back at views/ytd_income/_partials/_event_log.py:16 with the session
        # key taking precedence over the (already-cleared) demo snapshot, then
        # summed into ira_conversions_ytd / spouse_ira_conversions_ytd /
        # ira_distributions_ytd at _manual_entry.py:148-168 and written straight
        # back into ytd_snapshot at :179 -- so clearing ytd_snapshot alone was a
        # half-measure that the very next render undid.
        "income_events",
        # audit-1008 F-36: the snapshot events income_events was seeded from.
        "_income_events_seed",
        "statement_by_account",
        "koinly_report",
        "statement_folder_path",
        # Conversion Planner -- the user's real per-year conversion/QCD plan,
        # dict[year -> dollars] (views/planner.py:199-222).
        "conv_plan_your",
        "conv_plan_spouse",
        "conv_plan_qcd",
        "conv_plan_spouse_qcd",
        # Optimizer result and plan editors (redesign PR 3c).
        "_auto_opt_result",
        "_auto_opt_sig",
        "_auto_opt_flash",
        "conv_grid_editor",
        "oe_grid_editor",
        "_oe_shares_state",
        "_oe_grid_layout",
        # Setup -- scanned 1040 cache (views/_shared.py:119), NQO exercise sync
        # metadata, and the widget mirrors of survivor / filing status. Those
        # mirrors are distinct keys from "survivor" / "filing_status" above, so
        # clearing only the aggregates left the widgets holding personal values.
        "_pdf_1040_scanned",
        "exercises_captured_at",
        # Issue #487: pending holder-name-mismatch entries
        # (views/ytd_income/_partials/_sync_scan.py) reference statement
        # data cleared above -- leaving this behind would surface a stale
        # mismatch panel row pointing at an account the demo reset just wiped.
        "holder_name_mismatches",
        "_survivor_who_dies",
        "_survivor_death_year",
        "_hh_filing_status_choice",
        "_stock_ticker",
        # Data bridge -- key material and export/upload state. app.py:237-239
        # already pops the generated keypair, but only when navigating AWAY from
        # Backup & Transfer; "Reset to demo" is itself a Backup & Transfer button
        # (views/setup/data_bridge.py:430), so that teardown never fires on this
        # path. Resolves the generated-keypair half of the old ui-7/ui-8 TODO.
        "_generated_pub_b64",
        "_generated_priv_b64",
        "_export_recipient_pubkey",
        "export_bundle",
        "bundle_upload",
        # Bytes of the uploaded bundle, stashed at upload time so Apply never
        # re-reads a handle at EOF (views/setup/data_bridge.py::_stash_upload).
        "_bundle_upload_bytes",
        "_bundle_upload_id",
        # The decoded upload, cached per (upload, key digest) so the preview and
        # Apply decode once (views/setup/data_bridge.py::_prepared_upload), and
        # the preview's slot choices.
        "_bundle_prepared",
        "import_slot_you",
        "import_slot_spouse",
        "import_v4_slot",
        # Per-slot capture times (plan R12). The persisted copy lives in
        # .user_defaults.json, which demo mode hides from the loader.
        "_slot_captured_at",
        # audit-1008 F-65: accounts the last FinExtract sync refused to overwrite
        # (views/setup/portfolio.py KEPT_NEWER_KEY). They carry the user's account
        # names, and their "Use anyway" button reloaded the personal cache.
        "portfolio_kept_newer",
        # TODO(audit ui-7): the dynamic PDF-cache-prefix keys remain a separate
        # low-severity finding and are still not cleared here.
    ]
    for k in keys_to_clear:
        st.session_state.pop(k, None)
    # Dynamic key families: the account number, row index or governance field
    # name is baked into the key NAME, so no hardcoded list can ever reach them.
    # That is precisely why they survived three prior rounds of patching this
    # function (audit-0705 ui-5, audit-0721 C35, audit-0722b) -- every round
    # added more strings and none changed the mechanism. Same prefix-sweep idiom
    # already used in views/conversion_state.py (clear_conversion_widgets). Iterate over a list copy:
    # deleting from st.session_state while iterating it mutates the mapping.
    for _k in list(st.session_state):
        if _k.startswith(_PERSONAL_KEY_PREFIXES):
            del st.session_state[_k]
    st.session_state.pop("_seeded", None)  # force re-seed
    # audit-1018 F-25: queued SSA / NetBenefits / IRA statements and the scanned-
    # PDF digests are personal too; this also bumps the uploader nonce so the
    # same files can be scanned again. Deferred import: the scan view is heavy.
    from views.ytd_income._partials._sync_scan import clear_queued_statements

    clear_queued_statements(st.session_state)


def _clear_personal_session_state() -> None:
    """Reset to demo: clear the session and enter demo mode (audit-1018 D2).

    Demo mode (engine/demo_mode.py) is what keeps the reset from being undone: the
    personal on-disk caches and ``.user_defaults.json`` are neither read nor
    written while it is on, and they stay on disk untouched until
    :func:`exit_demo_mode`. (The old reset deleted ``.user_defaults.json``;
    that destroyed the user's data and is gone.)
    """
    _clear_session_for_reseed()
    # Also suppress the session-level snapshot autoload; an explicit sync/upload
    # clears this via _apply_portfolio_snapshot().
    st.session_state["_suppress_snapshot_autoload"] = True
    enter_demo_mode()


def exit_demo_mode() -> None:
    """Leave demo mode: the personal on-disk data is read again from the next run."""
    _clear_session_for_reseed()
    st.session_state.pop("_suppress_snapshot_autoload", None)
    leave_demo_mode()
