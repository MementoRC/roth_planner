"""Unit tests for the page registry (redesign PR 3a)."""

import re
from pathlib import Path

import pytest

from views.navigation import (
    ALL_PAGE_IDS,
    DEFAULT_PAGE,
    LABELS,
    LEGACY_LABELS,
    NAV_GROUPS,
    PAGES,
    RETIRED_IDS,
    ROUTES,
    group_of,
    label_of,
    resolve_page,
    route_target,
)

# The exact 13 labels the old sidebar offered (PR 1). A session saved before
# this PR may still hold one of them in ``nav_page``.
LEGACY_PAGES = [
    "⚙️ Setup",
    "📊 Dashboard",
    "📋 Conversion Planner",
    "💰 YTD Income",
    "📝 Option Exercise Planner",
    "🧮 Exercise Auto-Optimizer",
    "🎯 Sweet Spot Finder",
    "📉 RMD Squeeze",
    "⚖️ Comparator",
    "🏥 ACA + IRMAA Explorer",
    "📦 Asset Location",
    "✅ Roth Eligibility",
    "🔗 Portfolio",
]

EXPECTED_IDS = [
    "room",
    "income",
    "overview",
    "conversions",
    "options",
    "sweet",
    "rmd",
    "aca",
    "location",
    "compare",
    "elig",
    "household",
    "assumptions",
    "holdings",
    "import",
    "review",
    "backup",
]


EXPECTED_LABELS = {
    "room": "🧭 Room This Year",
    "income": "💰 This Year's Income",
    "overview": "📊 Overview",
    "conversions": "📋 Conversion Plan",
    "options": "📝 Option Exercises",
    "sweet": "🎯 Sweet Spot",
    "rmd": "📉 RMD Squeeze",
    "aca": "🏥 ACA + IRMAA",
    "location": "📦 Asset Location",
    "compare": "⚖️ Compare Scenarios",
    "elig": "✅ Roth Eligibility",
    "household": "👤 Household & Accounts",
    "assumptions": "📐 Assumptions",
    "holdings": "💼 Holdings",
    "import": "📥 Import Statements",
    "review": "🎛️ Review Changes",
    "backup": "🔐 Backup & Transfer",
}


def test_labels_are_the_mockup_labels():
    assert LABELS == EXPECTED_LABELS


def test_ids_are_the_mockup_ids_in_sidebar_order():
    assert list(ALL_PAGE_IDS) == EXPECTED_IDS
    assert [p.id for p in PAGES] == EXPECTED_IDS
    assert len(set(LABELS.values())) == len(LABELS)


def test_groups_are_in_workflow_order():
    assert [group for group, _ in NAV_GROUPS] == ["This Year", "Plan", "Analyze", "Profile", "Data"]
    assert dict(NAV_GROUPS)["This Year"] == ("room", "income")
    assert dict(NAV_GROUPS)["Plan"] == ("overview", "conversions", "options")
    assert dict(NAV_GROUPS)["Profile"] == ("household", "assumptions", "holdings")
    assert dict(NAV_GROUPS)["Data"] == ("import", "review", "backup")


def test_default_page_is_room_this_year():
    assert DEFAULT_PAGE == "room"
    assert DEFAULT_PAGE in ALL_PAGE_IDS


def test_old_ytd_income_label_still_lands_on_the_income_page():
    assert resolve_page("💰 YTD Income") == "income"


def test_legacy_labels_cover_all_13_old_labels():
    assert sorted(LEGACY_LABELS) == sorted(LEGACY_PAGES)
    assert set(LEGACY_LABELS.values()) <= set(ALL_PAGE_IDS)
    assert LEGACY_LABELS["⚙️ Setup"] == "review"
    assert LEGACY_LABELS["🔗 Portfolio"] == "holdings"


def test_resolve_page_keeps_a_valid_id():
    assert resolve_page("overview") == "overview"


def test_retired_ids_map_to_where_the_content_went():
    assert resolve_page("setup") == "review"
    assert resolve_page("portfolio") == "holdings"
    assert set(RETIRED_IDS).isdisjoint(ALL_PAGE_IDS)


def test_retired_optimizer_id_and_label_land_on_conversion_plan() -> None:
    assert resolve_page("optimizer") == "conversions"
    assert resolve_page("🧮 Exercise Auto-Optimizer") == "conversions"
    assert "optimizer" not in ALL_PAGE_IDS


@pytest.mark.parametrize("label", LEGACY_PAGES)
def test_resolve_page_maps_every_legacy_label(label):
    assert resolve_page(label) == LEGACY_LABELS[label]
    assert resolve_page(label) in ALL_PAGE_IDS


@pytest.mark.parametrize("bad", [None, "", 7, "Conversion Planner", "🗑️ Removed Page"])
def test_resolve_page_falls_back_to_default(bad):
    assert resolve_page(bad) == DEFAULT_PAGE


def test_group_of_and_label_of():
    assert group_of("room") == "This Year"
    assert group_of("income") == "This Year"
    assert group_of("options") == "Plan"
    assert group_of("elig") == "Analyze"
    assert group_of("review") == "Data"
    assert label_of("review") == "🎛️ Review Changes"


@pytest.mark.parametrize("fn", [group_of, label_of])
def test_unknown_id_raises(fn):
    with pytest.raises(KeyError):
        fn("nope")


def test_routes_cover_exactly_the_registered_pages():
    assert set(ROUTES) == set(ALL_PAGE_IDS)


@pytest.mark.parametrize("page_id", EXPECTED_IDS)
def test_every_route_resolves_to_a_callable(page_id):
    assert callable(route_target(page_id))


def test_review_page_constant_is_a_page_id():
    from views._shared import REVIEW_PAGE

    assert REVIEW_PAGE == "review"
    assert REVIEW_PAGE in ALL_PAGE_IDS


def test_jump_buttons_in_views_name_page_ids():
    pattern = re.compile(r"""\bpage=(["'])(.+?)\1""")
    views = Path(__file__).resolve().parents[1] / "views"
    for path in views.rglob("*.py"):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            assert match.group(2) in ALL_PAGE_IDS, (str(path), match.group(2))


def test_tests_write_page_ids_to_nav_page():
    """Legacy labels still resolve at runtime; test code must use ids."""
    pattern = re.compile(r"""nav_page["']\]\s*=\s*(["'])(.+?)\1""")
    tests = Path(__file__).resolve().parent
    for path in tests.glob("test_*.py"):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            assert match.group(2) in ALL_PAGE_IDS, (path.name, match.group(2))
