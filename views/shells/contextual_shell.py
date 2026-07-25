"""Contextual Setup shell — Classic's existing 4-tab layout with a
data-completeness status bar rendered above it (Task 9 of the
ui-shell-theme-toggle plan).

Wraps ``views.setup.render`` UNCHANGED (same "wrap Classic, don't
reimplement" pattern ``classic_shell.py`` established in Task 8 — Contextual
never forks the underlying widget keys/data model) and adds exactly ONE new
piece on top: a status bar built from
``engine.data_status.compute_data_status``, flagging each governed field
that is missing, stale, or in conflict with a pending Command Center
candidate.

Governed field set (``_governed_field_keys``): ``HOUSEHOLD_SCALAR_FIELDS``
(the same Household-attribute scalar fields Command Center's per-partial
governance cards already key off — see
``views/setup/_partials/_governance.py``) plus ``GRANTS_KEY`` plus one
``magi_field_key(year)`` per year currently present in
``hh.prior_year_magi``. The ``*_ytd`` fields in
``engine.data_sources.resolver.SOURCED_SCALAR_FIELDS`` are deliberately
excluded: they are not (yet) attributes on ``Household`` (see that module's
own comment), so ``compute_data_status``'s ``hasattr`` check would flag them
"missing" unconditionally — noise, not signal.

``pending_candidates`` is read straight from
``st.session_state["_pending_review"]`` — the exact same set app.py's
sidebar badge and Command Center's "N fields awaiting review" metric already
read (populated once per render by ``engine.data_sources.resolver.resolve()``
via ``app.py``'s ``get_household()``) — reused here rather than recomputed.

Click-to-jump limitation (documented per the plan's own instruction not to
silently under-deliver): Streamlit's ``st.tabs()`` has no supported API for
programmatically selecting a tab from Python, unlike ``st.sidebar.radio``
(which drives page nav via ordinary ``session_state`` mutation, the
mechanism ``views/_shared.py:command_center_button`` already uses). Since
Contextual wraps Classic's 4-tab body unchanged, a chip's jump button can
only navigate to the Setup PAGE — it cannot also pre-select the specific tab
(e.g. Accounts) the flagged field lives in. This is a real, acknowledged gap
rather than an oversight: solving it would require either a Streamlit
version with tab-selection support or reimplementing tabs as a manual
radio/expander construct, out of scope for Task 9 (Domains/Hub already offer
finer-grained navigation as a side effect of their own non-tab layouts).
Reuses ``command_center_button`` as-is rather than inventing a new
navigation primitive for this one shell.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from engine.data_sources.resolver import GRANTS_KEY, HOUSEHOLD_SCALAR_FIELDS, magi_field_key
from engine.data_status import DataStatusItem, compute_data_status
from models.household import Household
from views import setup
from views._shared import command_center_button

_SEVERITY_ICON: dict[str, str] = {
    "missing": "⬜",
    "stale": "⏳",
    "conflict": "⚠️",
}


def _governed_field_keys(hh: Household) -> list[str]:
    """The governed field set Command Center's own governance cards use.

    ``HOUSEHOLD_SCALAR_FIELDS`` + ``GRANTS_KEY`` (the fixed scalar/list
    fields every owning partial's governance card already covers — see
    ``views/setup/_partials/_governance.py``) plus one
    ``magi_field_key(year)`` per year currently tracked in
    ``hh.prior_year_magi``.
    """
    magi_keys = [magi_field_key(year) for year in sorted(hh.prior_year_magi.keys())]
    return [*HOUSEHOLD_SCALAR_FIELDS, GRANTS_KEY, *magi_keys]


def _render_status_chip(item: DataStatusItem) -> None:
    """One status-bar chip: severity icon + label + detail + jump-to-Setup button.

    Follows ``views/_shared.py:render_canonical_field``'s established
    chip-plus-jump-button convention (reuses its exact
    ``command_center_button`` rather than inventing a new one) — see that
    function's docstring for the pattern this mirrors.
    """
    icon = _SEVERITY_ICON.get(item.severity, "•")
    left, right = st.columns([4, 1])
    with left:
        st.warning(f"{icon} **{item.label}** ({item.severity}) — {item.detail}")
    with right:
        command_center_button(key=f"status_jump_{item.field}")


def render(hh: Household) -> None:
    """Render Contextual: a data-completeness status bar above Classic's
    unchanged Setup body.

    One chip per non-``ok`` ``DataStatusItem``. When every governed field is
    ``ok`` (or there simply are none, e.g. a brand-new household with no MAGI
    years recorded yet), a single "All set" affirmation replaces the bar
    instead of rendering nothing at all.
    """
    pending_candidates: set[str] = st.session_state.get("_pending_review", set())
    items = compute_data_status(
        hh, _governed_field_keys(hh), pending_candidates, now=datetime.now()
    )

    st.markdown("**Data status**")
    if not items:
        st.success("✅ All set — every governed field is confirmed and current.")
    else:
        for item in items:
            _render_status_chip(item)

    setup.render(hh)


__all__ = ["render"]
