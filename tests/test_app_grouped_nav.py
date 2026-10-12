"""AppTest coverage for the grouped sidebar navigation (redesign PR 1)."""

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from views.navigation import ALL_PAGE_IDS, DEFAULT_PAGE, NAV_GROUPS


@pytest.fixture(autouse=True)
def _isolated_app(clean_command_center_caches, monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _run(page: str | None = None) -> AppTest:
    at = AppTest.from_file("app.py", default_timeout=30)
    at.session_state["_suppress_snapshot_autoload"] = True
    if page is not None:
        at.session_state["nav_page"] = page
    at.run()
    return at


def test_sidebar_has_no_radio_anymore():
    at = _run()
    assert len(at.sidebar.radio) == 0


def test_sidebar_has_one_expander_per_group_in_order():
    at = _run()
    assert [e.label for e in at.sidebar.expander] == [g for g, _ in NAV_GROUPS]


def test_fresh_session_lands_on_ytd_income():
    at = _run()
    assert not at.exception
    assert at.session_state["nav_page"] == DEFAULT_PAGE


def test_clicking_a_nav_button_switches_page():
    at = _run()
    key = "nav_btn_overview"
    at.button(key=key).click().run()
    assert not at.exception
    assert at.session_state["nav_page"] == "overview"


def test_every_page_has_a_button_keyed_by_its_id():
    at = _run()
    for page_id in ALL_PAGE_IDS:
        assert at.button(key=f"nav_btn_{page_id}") is not None


def test_stale_nav_value_falls_back_to_default():
    at = _run("Conversion Planner")  # pre-emoji label: no longer a page
    assert not at.exception
    assert at.session_state["nav_page"] == DEFAULT_PAGE


@pytest.mark.parametrize("page", ALL_PAGE_IDS)
def test_every_page_renders_without_exception(page):
    at = _run(page)
    assert not at.exception, [e.value for e in at.exception]
