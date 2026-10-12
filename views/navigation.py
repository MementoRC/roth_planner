"""Page registry and grouped sidebar navigation (site redesign, PR 1 / PR 3a).

Every page has a stable ``id`` (the mockup's ids), a display ``label`` and a
``group``. ``nav_page`` in session state holds the ID, the router in
``app.py`` goes through ``route_target``, and jump buttons call
``views._shared._go_to(page_id)``. Labels are display text only.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass

import streamlit as st

from views._shared import NAV_KEY, _go_to
from views.ytd_drafts import DRAFT_KEY, has_unsaved


@dataclass(frozen=True)
class Page:
    id: str
    label: str
    group: str


PAGES: tuple[Page, ...] = (
    Page("room", "🧭 Room This Year", "This Year"),
    Page("income", "💰 This Year's Income", "This Year"),
    Page("overview", "📊 Overview", "Plan"),
    Page("conversions", "📋 Conversion Plan", "Plan"),
    Page("options", "📝 Option Exercises", "Plan"),
    Page("sweet", "🎯 Sweet Spot", "Analyze"),
    Page("rmd", "📉 RMD Squeeze", "Analyze"),
    Page("aca", "🏥 ACA + IRMAA", "Analyze"),
    Page("location", "📦 Asset Location", "Analyze"),
    Page("compare", "⚖️ Compare Scenarios", "Analyze"),
    Page("elig", "✅ Roth Eligibility", "Analyze"),
    Page("household", "👤 Household & Accounts", "Profile"),
    Page("assumptions", "📐 Assumptions", "Profile"),
    Page("holdings", "💼 Holdings", "Profile"),
    Page("import", "📥 Import Statements", "Data"),
    Page("review", "🎛️ Review Changes", "Data"),
    Page("backup", "🔐 Backup & Transfer", "Data"),
)

ALL_PAGE_IDS: tuple[str, ...] = tuple(p.id for p in PAGES)
LABELS: dict[str, str] = {p.id: p.label for p in PAGES}
_GROUPS: dict[str, str] = {p.id: p.group for p in PAGES}

NAV_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (group, tuple(p.id for p in PAGES if p.group == group))
    for group in dict.fromkeys(p.group for p in PAGES)
)

# This year's room is where every analysis starts (redesign PR 4b).
DEFAULT_PAGE = "room"

# The 13 labels the sidebar used before page ids existed. A session opened
# before the deploy may still hold one in ``nav_page``.
LEGACY_LABELS: dict[str, str] = {
    "💰 YTD Income": "income",
    "📊 Dashboard": "overview",
    "📋 Conversion Planner": "conversions",
    "📝 Option Exercise Planner": "options",
    "🧮 Exercise Auto-Optimizer": "conversions",
    "🎯 Sweet Spot Finder": "sweet",
    "📉 RMD Squeeze": "rmd",
    "🏥 ACA + IRMAA Explorer": "aca",
    "📦 Asset Location": "location",
    "⚖️ Comparator": "compare",
    "✅ Roth Eligibility": "elig",
    "⚙️ Setup": "review",
    "🔗 Portfolio": "holdings",
}

# Ids that 3a shipped and 3b/3c retired; an open session or a missed caller lands
# where that page's content went.
RETIRED_IDS: dict[str, str] = {
    "setup": "review",
    "portfolio": "holdings",
    "optimizer": "conversions",
}

# page id -> "module:callable". Resolved lazily so only the visited view is
# imported (matters for the stlite cold start).
ROUTES: dict[str, str] = {
    "room": "views.room_this_year:render",
    "income": "views.ytd_income:render",
    "overview": "views.dashboard:render",
    "conversions": "views.planner:render",
    "options": "views.option_exercise:render",
    "sweet": "views.sweet_spot:render",
    "rmd": "views.rmd_squeeze:render",
    "aca": "views.aca_irmaa:render",
    "location": "views.asset_location:render",
    "compare": "views.comparator:render",
    "elig": "views.roth_eligibility:render",
    "household": "views.profile_pages:render_household",
    "assumptions": "views.profile_pages:render_assumptions",
    "holdings": "views.profile_pages:render_holdings",
    "import": "views.data_pages:render_import",
    "review": "views.data_pages:render_review",
    "backup": "views.data_pages:render_backup",
}


def route_target(page_id: str) -> Callable[..., None]:
    """Import and return the render callable for *page_id*."""
    module_path, attr = ROUTES[page_id].split(":")
    return getattr(importlib.import_module(module_path), attr)


def resolve_page(value: object) -> str:
    """Return a page id for *value*: an id, a retired id, a legacy label, else the default."""
    if not isinstance(value, str):
        return DEFAULT_PAGE
    if value in LABELS:
        return value
    if value in RETIRED_IDS:
        return RETIRED_IDS[value]
    return LEGACY_LABELS.get(value, DEFAULT_PAGE)


def group_of(page_id: str) -> str:
    """Return the group a page id belongs to."""
    return _GROUPS[page_id]


def label_of(page_id: str) -> str:
    """Return the display label of a page id."""
    return LABELS[page_id]


def render_sidebar_nav() -> str:
    """Draw the grouped sidebar and return the current page id.

    Each group is an expander. Only the group holding the current page opens
    by default. Buttons set ``nav_page`` through ``_go_to``, the same path the
    "Edit in ..." jump buttons use.
    """
    current = resolve_page(st.session_state.get(NAV_KEY))
    st.session_state[NAV_KEY] = current
    current_group = group_of(current)
    for group, page_ids in NAV_GROUPS:
        with st.sidebar.expander(group, expanded=group == current_group):
            for page_id in page_ids:
                st.button(
                    LABELS[page_id],
                    key=f"nav_btn_{page_id}",
                    type="primary" if page_id == current else "tertiary",
                    on_click=_go_to(page_id),  # _go_to is a factory: it returns the callback
                    width="stretch",
                )
        if group == "This Year" and has_unsaved(
            st.session_state.get(DRAFT_KEY), st.session_state.get("ytd_snapshot")
        ):
            # Outside the expander: a collapsed expander's children are not
            # rendered. Never on the button label.
            st.sidebar.caption("✎ Unsaved YTD entries — open This Year's Income to save")
    return current
