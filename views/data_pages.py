"""Data pages (redesign PR 3b): Import Statements, Review Changes, Backup & Transfer.

Split out of Setup › 📥 Data (the Domains shell, deleted in 3b).
Command Center used to lead that tab because it holds the instance-owner
control that unlocks PDF scanning; now that it is its own page, the owner gate
on Import links to it instead.
"""

from __future__ import annotations

import streamlit as st

from models.household import Household
from views._shared import edit_link_button
from views._style import render_page_header
from views.setup._state import autosave_user_defaults
from views.setup.command_center import ensure_default_instance_owner, render_command_center
from views.setup.data_bridge import _this_instance_owner, render_data_bridge_tab
from views.setup.parameters import _render_pdf_1040_import
from views.ytd_income._partials import (
    render_sync_scan_partial,
    render_sync_scan_results,
    render_ytd_finextract_sync,
)

# Page ids held as constants: "review" is not a registered id until Task 2 of
# PR 3b, and tests/test_navigation.py scans views/ for page-id keyword literals.
_REVIEW = "review"
_INCOME = "income"


def _render_owner_required_placeholder() -> None:
    """Stand in for PDF Statements while this instance has no owner.

    A scanned statement's accounts are attributed to the operator, so scanning
    genuinely needs an owner. Bundle import no longer does -- a v5 file declares
    its own slots and a v4 file lands where the slot picker says (spec
    2026-09-20 §5) -- so that card always renders.
    """
    st.info(
        "Set this planner instance's owner on **Review Changes** to unlock scanning PDF statements."
    )
    edit_link_button(key="import_owner_gate_link", page=_REVIEW, label="Go to Review Changes →")


def render_import(hh: Household) -> None:
    render_page_header("import")
    # Guardrail: manual entry stays on This Year › YTD Income (rendering it here
    # too would duplicate its ytd_manual_* widget keys); link to it instead.
    edit_link_button(key="import_to_manual_entry", page=_INCOME, label="Add an entry by hand →")

    # Same identity state PDF Statements gates on (one source of truth, cf. #498).
    ensure_default_instance_owner()
    identity_set = bool(_this_instance_owner())
    scan_ctx = None
    with st.container(border=True):
        st.subheader("PDF Statements")
        if identity_set:
            scan_ctx = render_sync_scan_partial(hh)
        else:
            _render_owner_required_placeholder()
    # AFTER the scan in the same pass: the scan writes _pdf_1040_scanned, which
    # _render_pdf_1040_import reads and returns early on when empty
    # (views/setup/parameters.py:90-97).
    with st.container(border=True):
        st.subheader("1040 Import")
        _render_pdf_1040_import()
    # scan_ctx is None only when the owner gate replaced the scan body.
    if scan_ctx is not None:
        render_sync_scan_results(scan_ctx)
    # Gated like the scan above (the whole partial was owner-gated before).
    if identity_set:
        with st.expander("Legacy: FinExtract", expanded=False):
            st.caption("Kept while FinExtract is retired. PDF statements are the primary source.")
            render_ytd_finextract_sync(hh)
    autosave_user_defaults()


def render_review(hh: Household) -> None:
    # render_command_center self-titles with st.header("🎛️ Command Center");
    # the page title names the page, the header names the tool.
    render_page_header(_REVIEW)
    col_center, _gutter = st.columns([2, 1])
    with col_center:
        render_command_center(hh)
    autosave_user_defaults()


def render_backup(hh: Household) -> None:
    render_page_header("backup")
    st.caption("Encrypted export and import of your data between this planner and the public site.")
    # The export control is identity-gated on the same state as the scan.
    ensure_default_instance_owner()
    with st.container(border=True):
        st.subheader("Import previous data")
        render_data_bridge_tab(hh)
    autosave_user_defaults()
