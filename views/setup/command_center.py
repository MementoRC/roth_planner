"""Setup ▸ Command Center — the sync-everything trigger and review-gate status.

Per-field trust/manual-override/confirm governance cards, previously
rendered here generically for every field flagged pending review
(``st.session_state["_pending_review"]``, populated by
``engine.data_sources.resolver.resolve()`` via ``app.py``'s
``get_household()``), now render INLINE within each field's owning partial
instead (``views/setup/_partials/_accounts.py``: Accounts —
``render_accounts_partial`` — as of Task 4 of the ui-shell-theme-toggle
plan; Options in Task 5; Assumptions in Task 7).

This module's old generic ``for field_key in sorted(pending):`` loop was
REMOVED (not filtered) in Task 4: Classic mode renders both this tab's body
AND the Parameters tab's body every script run (``st.tabs()`` executes
every branch regardless of which tab is visually selected), so registering
the same ``trust_<field>``/``manual_<field>``/``confirm_<field>`` widget key
from both that loop AND an owning partial in the same run would raise
``DuplicateWidgetID``. See ``tests/test_setup_shell_characterization.py``'s
Task-4 regression test for the specific before/after proof.

No committed value changes as a result of THIS module without passing
through ``engine.data_sources.confirm.confirm_field`` — that mutation path
now lives in ``views/setup/_partials/`` (exercised by the owning
partials), not here.
"""

from __future__ import annotations

import streamlit as st

from models.household import Household
from views._shared import SyncEverythingResult, sync_everything


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
    """Render the Setup ▸ Command Center — sync trigger + pending-review status.

    Per-field governance cards live in each field's owning partial now (see
    module docstring); this tab is the "sync everything" action plus an
    at-a-glance pending-count/reconciled indicator.
    """
    st.header("🎛️ Command Center")

    if st.button("⟳ Sync everything", key="sync_everything_btn"):
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
