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
``views/setup/parameters.py:render_parameters_tab`` established in Task 3,
just composed into ONE tab (via two side-by-side columns for Me/Spouse)
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
- ``render_sync_scan_partial(hh)`` -- was called from
  ``views/ytd_income/__init__.py``'s "Update Your Data" tab, which is now
  display-only (manual entry + headroom review). Its CODE was deliberately
  NOT moved (still lives in
  ``views/ytd_income/_partials/_sync_scan.py``) — only the call site moved —
  so tests exercising the partial directly keep working unchanged. It saves
  its own snapshot via ``engine.portfolio_sync.save_ytd_snapshot`` at its own
  internal call sites, so scanning/applying from this tab persists to disk
  without requiring a visit to the YTD page.
- ``render_data_bridge_tab(hh)`` -- takes no ``container`` arg, renders via
  bare ``st.*`` calls internally, so it must run inside a ``with`` block for
  Streamlit's ambient "current container" to place it in this tab.
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
)
from views.setup._state import autosave_user_defaults
from views.setup.command_center import render_command_center
from views.setup.data_bridge import render_data_bridge_tab
from views.setup.parameters import _render_pdf_1040_import
from views.ytd_income._partials import render_sync_scan_partial


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
        st.subheader("Command Center")
        render_command_center(hh)
        st.subheader("YTD Sync & Scan")
        render_sync_scan_partial(hh)
        st.subheader("Data bridge")
        render_data_bridge_tab(hh)
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
    # directly and never routes through render_parameters_tab, so it never
    # got autosave for free the way the (now-deleted) Classic/Contextual
    # shells did. Must run last, after every partial above has had a chance
    # to mutate session_state.
    autosave_user_defaults()


__all__ = ["render"]
