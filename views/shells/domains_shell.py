"""Domains Setup shell — Setup regrouped by data-domain (Data / Household /
Accounts / Options / Assumptions / Portfolio) instead of Classic's Command
Center / Parameters / Portfolio / Data bridge grouping (Task 8 of the
ui-shell-theme-toggle plan).

Composes the SAME five partials Tasks 3-7 extracted
(``views/setup/_partials/``) plus
``views/setup/data_bridge.py:render_data_bridge_tab`` unchanged — no widget
``key=``/``value=`` sourcing is forked, only the surrounding tab grouping
differs from Classic (Owner decisions 4/5 in
``docs/superpowers/plans/2026-07-24-ui-shell-theme-toggle.md``).

The Household tab needs 3 partial calls (``"joint"``/``"your"``/``"spouse"``)
since ``render_household_partial`` is per-owner — the same 3-call pattern
``views/setup/parameters.py:render_parameters_tab`` (now deleted) established
in Task 3, just composed into ONE tab (via two side-by-side columns for Me/Spouse)
instead of split across 3 sub-tabs. Accounts similarly needs 2 calls
(``"your"``/``"spouse"``); Options/Assumptions/Portfolio are household-level
(no owner split, 1 call each).

Each partial is called with the tab (or column-within-a-tab) it belongs in
passed explicitly as ``container`` — this partial-composability contract
(rather than relying on ambient ``with tab:`` context) is what
``render_portfolio_partial``'s nested-tabs/expander helpers were fixed to
honor (see ``views/setup/_partials/_portfolio.py``'s module docstring for
the code-quality fix this exercises): a real Streamlit ``st.tabs(...)`` tab
object nested inside the outer "Portfolio" tab.

PR A ("consolidate all data ingest into one 📥 Data tab", relocation only, no
behaviour change) collapsed the four previously-separate ingest entry points
-- Command Center, the YTD page's sync/scan partial, Data bridge, and 1040
Import -- into ONE "📥 Data" tab, FIRST in the tab list since it is the
data-review/sync gate a user should see before editing any domain tab. Each
sub-section keeps its own ``st.subheader`` and is called exactly as it was
before the move:

- ``render_command_center(hh)`` -- was its own tab (added when the UI shell
  collapsed to Domains-only; Classic/Contextual, its only other callers,
  were already deleted at that point).
- ``render_stock_price_widget(st)`` -- added by PR B (2026-09), moved out of
  ``render_options_partial`` (Options tab). ``txn_price_now`` is a
  market-sourced value already refreshed by "Sync everything"'s Yahoo-quote
  leg, so it renders here beside the sync controls instead of under Options
  (the Stock Grants table stays under Options). Renders its own "Stock
  Price" ``st.subheader`` internally.
- ``render_sync_scan_partial(hh)`` -- was called from
  ``views/ytd_income/__init__.py``'s "Update Your Data" tab, which is now
  display-only (manual entry + headroom review). Its CODE was deliberately
  NOT moved (still lives in
  ``views/ytd_income/_partials/_sync_scan.py``) — only the call site moved —
  so tests exercising the partial directly keep working unchanged. It saves
  its own snapshot via ``engine.portfolio_sync.save_ytd_snapshot`` at its own
  internal call sites, so scanning/applying from this tab persists to disk
  without requiring a visit to the YTD page. Its section header reads "PDF
  Statements" (renamed by PR B from "YTD Sync & Scan", vocabulary inherited
  from this partial's previous home on the YTD page that no longer describes
  where it lives).
- ``render_data_bridge_tab(hh)`` -- takes no ``container`` arg, renders via
  bare ``st.*`` calls internally, so it must run inside a ``with`` block for
  Streamlit's ambient "current container" to place it in this tab. Its
  section header reads "Import previous data" (renamed by PR B from "Data
  bridge" for the same user-facing-clarity reason).
- ``_render_pdf_1040_import()`` -- same no-``container``-arg reasoning as
  ``render_data_bridge_tab`` above. Imported directly from
  ``views.setup.parameters`` despite its leading underscore (Python does not
  enforce module privacy, and this codebase already crosses "private" module
  boundaries this way); reuses the exact SAME widget keys
  (``_pdf_1040_filing_status_<year>`` / ``_pdf_1040_save_<year>``) — no new
  keys introduced, per the plan's "no session_state key renames" principle
  (Owner decision 4).
"""

from __future__ import annotations

import streamlit as st

from models.household import Household
from views.setup._partials import (
    render_accounts_partial,
    render_assumptions_partial,
    render_household_partial,
    render_options_partial,
    render_portfolio_partial,
    render_stock_price_widget,
)
from views.setup._state import autosave_user_defaults
from views.setup.command_center import render_command_center
from views.setup.data_bridge import _this_instance_owner, render_data_bridge_tab
from views.setup.parameters import _render_pdf_1040_import
from views.ytd_income._partials import render_sync_scan_partial


def _render_owner_required_placeholder() -> None:
    """Stand in for an import section while this instance has no owner.

    Streamlit has no disabled *container*, so a section whose every control is
    ``disabled=not identity_set`` previously rendered in full and merely inert:
    a permanently greyed button with a small grey caption reads as "busy", not
    "blocked". Replacing the body with one visible sentence says which state
    the page is in and where to leave it.

    This suppresses more than the gated widgets -- the keypair generator and
    the V2 private-key box are not themselves identity-gated -- which is
    deliberate, not incidental: the order a first-time user must follow is
    owner, then key, then import, so surfacing the key box before an owner
    exists invites step two before step one.
    """
    st.info("Set this planner instance's owner in **Command Center**, above, to unlock importing.")


def render(hh: Household) -> None:
    """Render the Domains Setup layout: 6 tabs grouped by data domain."""
    st.title("⚙️ Setup — Domains")

    (
        tab_data,
        tab_household,
        tab_accounts,
        tab_options,
        tab_assumptions,
        tab_portfolio,
    ) = st.tabs(
        [
            "📥 Data",
            "Household",
            "Accounts",
            "Options",
            "Assumptions",
            "Portfolio",
        ]
    )

    with tab_data:
        # PR A (relocation only, no behaviour change): all data-ingest entry
        # points now live in one tab. render_sync_scan_partial's CODE stays
        # in views/ytd_income/_partials/_sync_scan.py (only the call moved
        # here) -- deliberate, so tests that drive it directly keep working
        # unchanged. See this module's docstring for the full rationale.
        #
        # Section order (2026-09): DEPENDENCY order, which supersedes the
        # earlier import-first arrangement (PR C). That arrangement optimised
        # for the returning user -- one whose instance already has an owner and
        # a key, for whom Command Center is maintenance noise to scroll past.
        # But every control in both import sections is GATED on instance
        # identity (`disabled=not identity_set`: _sync_scan.py's "Scan uploaded
        # PDFs" and "Apply to YTD snapshot", data_bridge.py's "Apply" and the
        # export), and the widget that satisfies that gate lives in Command
        # Center. Rendering the gated sections ABOVE the control that unlocks
        # them meant a first-time user met a disabled button first and was told
        # to go somewhere they had already scrolled past -- a permanently
        # greyed-out button reads as "busy", not "blocked".
        #
        # So Command Center leads, and the two import paths sit BESIDE each
        # other beneath it: they are alternatives, not steps -- a spouse
        # restores a bundle, or scans their own statements, or does both in
        # either order -- and stacking them implied a sequence that does not
        # exist. tests/test_data_tab_section_order.py pins the sequence.
        #
        # Command Center is constrained to 2:1 rather than full-span: on a
        # first run its whole body is a warning, an owner radio and Save, which
        # looks lost across the full page. It is NOT narrow enough for a third,
        # though -- once identity is set it renders _render_attribution_table,
        # one st.columns([3, 2, 1]) row per account (caption | owner selectbox
        # | Clear). At a third of the page those sub-columns cramp and the
        # account label wraps. The right column is margin, not a slot.
        #
        # Script order inside the columns IS the render order (left column
        # body executes first) and is also what a narrow viewport sees once
        # Streamlit stacks them: Import previous data, then PDF Statements.
        # No st.subheader here: render_command_center opens with its own
        # st.header("🎛️ Command Center") (views/setup/command_center.py), so a
        # subheader above it rendered the name twice, plain then large. The
        # other three sections need theirs -- render_data_bridge_tab and
        # _render_pdf_1040_import do not self-title, and
        # render_stock_price_widget already renders its own (which is why it
        # never had one here either).
        col_center, _col_center_gutter = st.columns([2, 1])
        with col_center:
            render_command_center(hh)
        # Read (never redefine) the same identity state the import controls
        # already gate on -- _this_instance_owner is data_bridge's own helper,
        # so there is one source of truth for "does this instance have an
        # owner", not a second copy that can drift (cf. #498).
        identity_set = bool(_this_instance_owner())
        # border=True on each column body (the same st.container(border=True)
        # idiom render_command_center uses for its field cards): the two
        # sections hold different amounts of content, so without a frame the
        # shorter one just trails off into whitespace and the pair reads as a
        # broken alignment rather than as two alternatives. The title goes
        # INSIDE the frame so each column is one self-contained card.
        col_import, col_pdf = st.columns(2)
        with col_import, st.container(border=True):
            # "Import previous data": renamed from "Data bridge" (PR B,
            # 2026-09) -- clearer to a user than the internal mechanism name.
            st.subheader("Import previous data")
            if identity_set:
                render_data_bridge_tab(hh)
            else:
                _render_owner_required_placeholder()
        with col_pdf, st.container(border=True):
            # "PDF Statements": renamed from "YTD Sync & Scan" (PR B, 2026-09)
            # -- that name was vocabulary inherited from this partial's
            # previous home on the YTD page and no longer describes where it
            # lives. render_sync_scan_partial no longer emits its own
            # "### YTD Income Entry" under this: two h3s back to back.
            st.subheader("PDF Statements")
            if identity_set:
                render_sync_scan_partial(hh)
            else:
                _render_owner_required_placeholder()
        # txn_price_now is a market-sourced value that "Sync everything"
        # already refreshes via its Yahoo-quote leg, so its widget renders in
        # this tab rather than under Options (PR B, 2026-09) -- see
        # render_stock_price_widget's docstring.
        render_stock_price_widget(st)
        st.subheader("1040 Import")
        _render_pdf_1040_import()

    tab_household.subheader("Filing status")
    _is_single = bool(render_household_partial(hh, tab_household, "joint"))
    col_you, col_spouse = tab_household.columns(2)
    col_you.subheader("Me")
    render_household_partial(hh, col_you, "your")
    col_spouse.subheader("Spouse")
    if _is_single:
        col_spouse.info(
            "Single filer — spouse inputs are disabled and treated as zero. "
            "Switch Filing status to Married filing jointly to re-enable."
        )
    render_household_partial(hh, col_spouse, "spouse")

    col_you, col_spouse = tab_accounts.columns(2)
    col_you.subheader("Me")
    render_accounts_partial(hh, col_you, "your")
    col_spouse.subheader("Spouse")
    render_accounts_partial(hh, col_spouse, "spouse")

    render_options_partial(hh, tab_options)
    render_assumptions_partial(hh, tab_assumptions)
    render_portfolio_partial(hh, tab_portfolio)

    # audit-0823 models-views/M2: this shell composes views/setup/_partials/
    # directly and never routed through render_parameters_tab (now itself
    # deleted), so it never got autosave for free the way the (now-deleted)
    # Classic/Contextual shells did. Must run last, after every partial
    # above has had a chance to mutate session_state.
    autosave_user_defaults()


__all__ = ["render"]
