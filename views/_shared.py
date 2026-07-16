"""Shared view primitives for the Command Center input-consolidation migration.

Two reusable pieces every later wave depends on:

1. ``command_center_button`` — a button that navigates to the Setup / Command
   Center page on the next rerun by setting the sidebar radio's key. Uses
   ``on_click`` so the nav key is mutated BEFORE the radio widget re-instantiates
   (setting a keyed widget's ``session_state`` slot *after* that widget already
   ran in the same script pass raises Streamlit's "cannot be modified" error;
   callbacks fire before widgets instantiate on the rerun, so they may set it).

2. ``render_canonical_field`` — a read-only display of a canonical ``Household``
   value with a provenance chip (when the value carries one) plus a jump button
   to edit it in the Command Center. Replaces the old "editable local copy that
   silently writes nowhere" widgets.

Pure view helper: imports streamlit only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

# The sidebar radio's key (added in app.py). Kept here so views never hardcode it.
NAV_KEY = "nav_page"
# Exact label of the Setup page option in app.py's sidebar radio.
SETUP_PAGE = "⚙️ Setup"


def _go_to(page_label: str) -> Callable[[], None]:
    def _cb() -> None:
        st.session_state[NAV_KEY] = page_label

    return _cb


def command_center_button(*, key: str, label: str = "Edit in Command Center →") -> None:
    """A button that navigates to the Setup / Command Center page on next rerun.

    Uses ``on_click`` so the nav key is set BEFORE the radio widget re-instantiates
    (the Streamlit-safe pattern for mutating a keyed widget's state).
    """
    st.button(label, key=key, on_click=_go_to(SETUP_PAGE))


def render_canonical_field(
    caption: str, value: Any, *, key: str, fmt: Callable | None = None
) -> None:
    """Read-only display of a canonical ``Household`` value.

    Shows a provenance chip when *value* carries one (``SourcedValue`` has
    ``.prov``; a plain float/int/str does not), plus a jump button to edit it in
    the Command Center. ``getattr(value, "prov", None)`` is mandatory: ages and
    filing status are plain values (never wrapped), only balances are
    ``SourcedValue`` after ``resolve_for_app``.
    """
    prov = getattr(value, "prov", None)
    shown = fmt(value) if fmt else str(value)
    left, right = st.columns([3, 1])
    with left:
        st.caption(caption)
        st.markdown(f"### {shown}")
        if prov is not None:
            st.caption(f"source: **{prov.source}** · {prov.detail or 'no detail'}")
        else:
            st.caption("entered in Setup")
    with right:
        command_center_button(key=f"nav_{key}")
