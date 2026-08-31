"""Setup ▸ Command Center — the sync-everything trigger and per-field
sourced-value governance gate.

Per-field trust/manual-override/confirm governance cards render HERE, in a
generic loop over every field flagged pending review
(``st.session_state["_pending_review"]``, populated by
``engine.data_sources.resolver.resolve()`` via ``app.py``'s
``get_household()``). This is the ONE AND ONLY renderer of these cards —
the owning partials (``views/setup/_partials/_accounts.py``,
``_options.py``, ``_assumptions.py``) deliberately do NOT render them
inline, reversing the Task-4/5/7 relocation. Reason: Classic mode renders
both this tab's body AND the Parameters tab's body every script run
(``st.tabs()`` executes every branch regardless of which tab is visually
selected), so registering the same ``trust_<field>``/``manual_<field>``/
``confirm_<field>`` widget key from both this loop AND an owning partial in
the same run would raise ``DuplicateWidgetID``. See
``tests/test_setup_shell_characterization.py``'s regression test for the
specific before/after proof.

No committed value changes as a result of any module without passing
through ``engine.data_sources.confirm.confirm_field`` — that mutation path
is exercised via ``_render_field_card`` (``views/setup/_partials/_governance.py``),
called only from this module.
"""

from __future__ import annotations

import streamlit as st

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import CorruptCommittedCacheError, load_committed
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from engine.instance_identity import (
    CorruptInstanceOwnerError,
    load_instance_owner,
    save_instance_owner,
)
from models.household import Household
from views._shared import SyncEverythingResult, sync_everything
from views.setup._partials._governance import _render_field_card


def _format_sync_everything_summary(summary: SyncEverythingResult) -> str:
    """Render a one-line "portfolio: N · SS: N · scan: N files, N errors" summary."""
    portfolio = f"portfolio: {summary.portfolio.candidates_recorded} candidates"
    if summary.portfolio.error:
        portfolio += f" (unavailable: {summary.portfolio.error})"

    ss = f"SS: {summary.ss.candidates_recorded} candidates"
    if summary.ss.warnings:
        ss += f" ({'; '.join(summary.ss.warnings)})"

    if summary.scan.result is not None:
        scan = (
            f"scan: {summary.scan.result.files_scanned} files, "
            f"{len(summary.scan.result.errors)} errors"
        )
    else:
        scan = f"scan: skipped ({summary.scan.error})"

    return " · ".join([portfolio, ss, scan])


def render_command_center(hh: Household) -> None:
    """Render the Setup ▸ Command Center — sync trigger + per-field governance cards.

    Per-field governance cards render here, in a generic loop over every
    pending field (see module docstring) — this tab is the "sync everything"
    action plus the sole reviewer/confirm gate for sourced-value candidates.
    """
    st.header("🎛️ Command Center")

    try:
        instance_owner = st.session_state.get("instance_owner") or load_instance_owner()
    except CorruptInstanceOwnerError:
        instance_owner = None
    identity_set = bool(instance_owner)

    if not identity_set:
        st.warning(
            "This planner instance has no owner set yet. Scanning and "
            "syncing are unavailable until you answer below."
        )
        choice = st.radio(
            "Which person's data does this planner instance hold?",
            ["Me", "Spouse"],
            key="instance_owner_gate_choice",
        )
        if st.button("Save", key="instance_owner_gate_save"):
            resolved_owner = "you" if choice == "Me" else "spouse"
            save_instance_owner(resolved_owner)
            st.session_state["instance_owner"] = resolved_owner
            st.rerun()

    # disabled=True (not hidden) while identity is unset -- a hidden control
    # is indistinguishable from a missing feature (see views/planner.py's
    # column_config disabled=True convention for the same "visible but
    # inert" preference over hiding a widget entirely).
    if st.button("⟳ Sync everything", key="sync_everything_btn", disabled=not identity_set):
        with st.spinner("Syncing all sources…"):
            summary = sync_everything(hh)
        # P4-1: the only other clearer of this Reset-to-demo sentinel
        # (_apply_portfolio_snapshot) has no live caller, so without this an
        # explicit sync would otherwise leave user-defaults autosave
        # (parameters.py/option_exercise.py, gated on the same flag) silently
        # disabled for the rest of the session.
        st.session_state.pop("_suppress_snapshot_autoload", None)
        st.info(_format_sync_everything_summary(summary))

    pending: set[str] = st.session_state.get("_pending_review", set())
    st.metric("Fields awaiting review", len(pending))
    if not pending:
        st.success("All data sources reconciled ✓")
        return

    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    # audit-0809 #11: a corrupt committed cache degrades to {} here (read-time
    # only) — save_committed() is the actual guard against overwriting it.
    try:
        committed_json = load_committed(COMMITTED_PATH) or {}
    except CorruptCommittedCacheError:
        committed_json = {}

    for field_key in sorted(pending):
        with st.container(border=True):
            _render_field_card(field_key, committed_json, store, choices)
