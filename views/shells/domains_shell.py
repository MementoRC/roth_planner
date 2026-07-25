"""Domains Setup shell — Setup regrouped by data-domain (Household /
Accounts / Options / Assumptions / Portfolio / Data bridge) instead of
Classic's Command Center / Parameters / Portfolio / Data bridge grouping
(Task 8 of the ui-shell-theme-toggle plan).

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

A 7th tab, "1040 Import", was added post-Task-8 (spec-compliance review of
this commit) to close a parity gap: ``views/setup/parameters.py``'s
``_render_pdf_1040_import`` (the "Import 1040 PDF" confirm-and-save
workflow) is a genuine, distinct user-facing feature that Task 8 left
reachable only from Classic mode. It is imported directly from
``views.setup.parameters`` despite its leading underscore — the same
pattern ``views/setup/__init__.py`` already uses for this exact function
(Python does not enforce module privacy, and this codebase already crosses
"private" module boundaries this way). Called with no ``container`` arg,
same as ``render_data_bridge_tab`` — it renders via bare ``st.*`` calls
internally and must run inside a ``with`` block so Streamlit's ambient
"current container" places it in this tab. It reuses the exact SAME widget
keys Classic's copy does (``_pdf_1040_filing_status_<year>`` /
``_pdf_1040_save_<year>``) — no new keys introduced, per the plan's "no
session_state key renames" principle (Owner decision 4).
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
from views.setup.data_bridge import render_data_bridge_tab
from views.setup.parameters import _render_pdf_1040_import


def render(hh: Household) -> None:
    """Render the Domains Setup layout: 7 tabs grouped by data domain."""
    st.title("⚙️ Setup — Domains")

    (
        tab_household,
        tab_accounts,
        tab_options,
        tab_assumptions,
        tab_portfolio,
        tab_bridge,
        tab_1040,
    ) = st.tabs(
        [
            "Household",
            "Accounts",
            "Options",
            "Assumptions",
            "Portfolio",
            "Data bridge",
            "1040 Import",
        ]
    )

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

    with tab_bridge:
        # render_data_bridge_tab(hh) takes no container arg — it renders via
        # bare `st.*` calls internally, so it must run inside a `with` block
        # for Streamlit's ambient "current container" to place it in this
        # tab (same pattern views/setup/__init__.py's Classic composition
        # already uses for this exact function).
        render_data_bridge_tab(hh)

    with tab_1040:
        # _render_pdf_1040_import() takes no container arg — same reason as
        # render_data_bridge_tab above.
        _render_pdf_1040_import()


__all__ = ["render"]
