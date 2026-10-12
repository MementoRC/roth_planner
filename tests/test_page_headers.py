"""Page titles come from the nav registry: one emoji, the page's own nav icon."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from views._style import page_title_text
from views.navigation import LEGACY_LABELS, PAGES, label_of, resolve_page

REPO_ROOT = Path(__file__).resolve().parent.parent
_ICON = re.compile(r"^(\S+) ")
_HARDCODED_TITLE = re.compile(r"""st\.title\(\s*f?["'][^"']*[^\x00-\x7f]""")


def _icon(page_id: str) -> str:
    match = _ICON.match(label_of(page_id))
    assert match, f"{page_id} label has no leading icon"
    return match.group(1)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.id)
def test_title_text_starts_with_nav_icon(page) -> None:
    assert page_title_text(page.id).startswith(_icon(page.id) + " ")
    assert page_title_text(page.id, "detail").endswith(" — detail")


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.id)
def test_each_page_renders_title_with_its_nav_icon(page) -> None:
    from streamlit.testing.v1 import AppTest

    def _script(page_id: str) -> None:
        import streamlit as st

        from config.scalar_registry import SCALAR_REGISTRY, scalar_default
        from models.household import Household
        from views.navigation import route_target

        for spec in SCALAR_REGISTRY:  # the profile pages read these keys directly
            st.session_state.setdefault(spec.session_key, scalar_default(spec.session_key))
        route_target(page_id)(Household())

    at = AppTest.from_function(_script, args=(page.id,), default_timeout=60).run()
    assert not at.exception
    titles = [t.value for t in at.title]
    assert titles, f"{page.id} renders no st.title"
    assert titles[0].startswith(_icon(page.id) + " ")


def test_no_view_hardcodes_an_emoji_in_st_title() -> None:
    offenders = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in sorted((REPO_ROOT / "views").rglob("*.py"))
        if _HARDCODED_TITLE.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_legacy_labels_still_resolve() -> None:
    for label in LEGACY_LABELS:
        assert resolve_page(label) in {p.id for p in PAGES}
