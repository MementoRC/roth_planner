"""The "Include this year's actuals" toggle lives in the sidebar (redesign PR 2)."""

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from views.navigation import ALL_PAGE_IDS, resolve_page

TOGGLE = "Include this year's actuals"


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _run(page: str | None = None, **state) -> AppTest:
    at = AppTest.from_file("app.py", default_timeout=30)
    at.session_state["_suppress_snapshot_autoload"] = True
    if page is not None:
        at.session_state["nav_page"] = page
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


def _go(at: AppTest, page: str) -> AppTest:
    at.button(key=f"nav_btn_{resolve_page(page)}").click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _toggle(at):
    found = [c for c in at.sidebar.checkbox if c.label == TOGGLE]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize("page", ALL_PAGE_IDS)
def test_projection_toggle_is_in_the_sidebar_on_every_page(page):
    at = _run(page)
    assert not at.exception, [e.value for e in at.exception]
    _toggle(at)


def test_projection_toggle_sets_the_shared_key_and_survives_navigation():
    at = _run("overview")
    _toggle(at).check().run()
    assert at.session_state["apply_ytd_to_projection"] is True
    _go(at, "conversions")
    assert at.session_state["apply_ytd_to_projection"] is True
    assert _toggle(at).value is True


def test_projection_toggle_seeded_state_is_reflected():
    at = _run("overview", apply_ytd_to_projection=True)
    assert _toggle(at).value is True


def test_ytd_page_has_no_second_projection_toggle():
    # Old label was f"Apply YTD actuals to {base_year} projection". Seed the key True
    # and match loosely so a reworded duplicate toggle is also caught.
    at = _run("income", apply_ytd_to_projection=True)
    assert not at.exception, [e.value for e in at.exception]
    dupes = [
        c.label
        for c in at.main.checkbox
        if "actuals" in c.label.lower() or "projection" in c.label.lower()
    ]
    assert not dupes, dupes


def test_projection_toggle_unticking_is_seen_on_another_page():
    at = _run("overview", apply_ytd_to_projection=True)
    assert _toggle(at).value is True
    _toggle(at).uncheck().run()
    assert at.session_state["apply_ytd_to_projection"] is False
    _go(at, "conversions")
    assert at.session_state["apply_ytd_to_projection"] is False
    assert _toggle(at).value is False


def test_projection_toggle_reset_to_demo_clears_key_and_sidebar_shows_false(monkeypatch):
    import views.setup._state as state_mod

    fake_state = {"apply_ytd_to_projection": True}
    with monkeypatch.context() as m:
        m.setattr(state_mod.st, "session_state", fake_state)
        state_mod._clear_personal_session_state()
    assert "apply_ytd_to_projection" not in fake_state

    at = _run("overview")
    assert _toggle(at).value is False
