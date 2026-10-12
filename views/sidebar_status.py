"""Sidebar status controls that apply to every page (redesign PR 2)."""

from __future__ import annotations

import streamlit as st


def render_projection_toggle() -> None:
    """The one home of ``apply_ytd_to_projection``.

    Unkeyed and controlled (value read from, then written back to, the plain
    session key): the sidebar renders every run, and a non-widget key is
    never dropped when a page changes.
    """
    st.session_state["apply_ytd_to_projection"] = st.sidebar.checkbox(
        "Include this year's actuals",
        value=bool(st.session_state.get("apply_ytd_to_projection", False)),
        help=(
            "Use this year's YTD income (This Year's Income page) for the current year in "
            "every projection instead of projecting it from zero."
        ),
    )
