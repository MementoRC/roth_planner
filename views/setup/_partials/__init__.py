"""Composable Setup-domain widget partials — shared by the Classic layout
(``views/setup/parameters.py`` et al.) and the alternate shells
(``views/shells/``, Task 8+).

Each ``render_X_partial(hh, container, ...)`` function renders a slice of
Setup's widgets into whatever Streamlit container the caller hands it (a
tab, an expander, or the top-level ``st`` module itself), so the SAME
widget code can be composed into different page layouts without forking
behavior. Widget shape — ``key=`` presence/absence, ``value=`` sourcing,
labels/help text — must be preserved EXACTLY when moving code here; see
Owner decision 5 in docs/superpowers/plans/2026-07-24-ui-shell-theme-toggle.md
(most widgets are intentionally unkeyed "controlled" widgets, and adding a
``key=`` to one would reintroduce a known Streamlit sync-override bug).

This package replaces the original flat ``views/setup/_partials.py`` module
(split when it grew to ~980 lines — pure mechanical reorganization, no
behavior change) with one module per partial:

- ``_governance.py`` — shared sourced-field trust/manual-override/confirm
  governance-card machinery used by every owning partial.
- ``_household.py`` — Task 3: ``render_household_partial`` +
  ``filing_status_from_label``.
- ``_accounts.py`` — Task 4: ``render_accounts_partial`` + ``_sync_ssa_for``.
- ``_options.py`` — Task 5: ``render_options_partial``.
- ``_portfolio.py`` — Task 6: ``render_portfolio_partial`` + its private
  table/expander helpers.
- ``_assumptions.py`` — Task 7: ``render_assumptions_partial`` + its private
  prior-year-MAGI-anchor/survivor-scenario/inherited-IRAs helpers.

Every name that was importable from the old flat module remains importable
from ``views.setup._partials`` unchanged via the re-exports below — no
caller (production code or tests) needs to change its import statements.
"""

from __future__ import annotations

from ._accounts import _sync_ssa_for, render_accounts_partial
from ._assumptions import render_assumptions_partial
from ._governance import (
    _FIELD_LABELS,
    _MAGI_PREFIX,
    _apply_confirm_to_session,
    _committed_value_and_source,
    _field_label,
    _format_value,
    _handle_confirm_click,
    _render_candidate_row,
    _render_field_card,
    _resolve_confirm_choice,
)
from ._household import (
    _HH_FILING_LABEL_MFJ,
    _HH_FILING_LABEL_SINGLE,
    filing_status_from_label,
    render_household_partial,
)
from ._options import render_options_partial
from ._portfolio import (
    _no_data_msg,
    _render_account_type_overrides,
    _render_accounts_table,
    _render_holdings_table,
    _render_portfolio_sub_tabs,
    render_portfolio_partial,
)

__all__ = [
    "render_household_partial",
    "render_accounts_partial",
    "render_options_partial",
    "render_portfolio_partial",
    "render_assumptions_partial",
    "filing_status_from_label",
    "_sync_ssa_for",
    "_render_field_card",
    "_render_candidate_row",
    "_resolve_confirm_choice",
    "_apply_confirm_to_session",
    "_handle_confirm_click",
    "_committed_value_and_source",
    "_field_label",
    "_format_value",
    "_no_data_msg",
    "_render_accounts_table",
    "_render_holdings_table",
    "_render_portfolio_sub_tabs",
    "_render_account_type_overrides",
    "_HH_FILING_LABEL_MFJ",
    "_HH_FILING_LABEL_SINGLE",
    "_MAGI_PREFIX",
    "_FIELD_LABELS",
]
