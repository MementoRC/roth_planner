"""Employer stock price and growth have one editable home: Profile > Assumptions > Stock Price (redesign PR 2)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import engine.data_sources.paths as paths_mod
import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from config.loader import load_defaults
from engine.data_sources.record import record_stock_quote_candidate
from engine.market_quote import QuoteResult
from views.navigation import resolve_page
from views.option_exercise._partials._helpers import handle_stock_quote_fetch

GROWTH_LABEL = "Assumed stock growth (%/yr)"
OE_PAGE = "options"


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _ok_quote(ticker: str = "ACME"):
    return QuoteResult(ticker, 199.5, "USD", datetime.now(UTC), "test")


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


def test_assumptions_has_the_growth_input_and_it_survives_navigation():
    at = _run("assumptions")
    assert not at.exception, [e.value for e in at.exception]
    growth = [n for n in at.number_input if n.label == GROWTH_LABEL]
    assert len(growth) == 1
    growth[0].set_value(9.0).run()
    _go(at, OE_PAGE)
    assert at.session_state["stock_price_growth_rate"] == 9.0


def test_growth_edit_in_assumptions_persists_to_user_defaults(tmp_path, monkeypatch):
    app_path = str(Path(__file__).resolve().parent.parent / "app.py")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
    monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
    at = AppTest.from_file(app_path, default_timeout=30)
    at.session_state["nav_page"] = "assumptions"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    growth = [n for n in at.number_input if n.label == GROWTH_LABEL]
    assert len(growth) == 1
    growth[0].set_value(9.0).run()
    assert not at.exception, [e.value for e in at.exception]
    assert load_defaults()["stock_price_growth_rate"] == 9.0


def test_option_exercise_shows_price_and_growth_read_only():
    at = _run(OE_PAGE, stock_price=150, stock_price_growth_rate=6.5)
    assert not at.exception, [e.value for e in at.exception]
    labels = {n.label for n in at.number_input}
    # No editable growth input of any label lives on this page any more.
    assert not any("growth" in label.lower() for label in labels)
    assert any("6.50%/yr" in m.value for m in at.markdown)
    assert (
        at.button(key="edit_stock_growth").label == "Edit in Profile › Assumptions › Stock Price →"
    )


def test_fetch_records_candidate_but_does_not_touch_session_price(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "session_state", {})
    result = handle_stock_quote_fetch(store_path=tmp_path / "cands.json", fetcher=_ok_quote)
    assert result.ok
    assert "_stock_quote_price" not in st.session_state
    assert (tmp_path / "cands.json").exists()


def test_stale_quote_key_is_ignored_by_the_page():
    at = _run(OE_PAGE, stock_price=150, _stock_quote_price=999.0)
    assert not at.exception, [e.value for e in at.exception]
    assert any("\\$150.00" in m.value for m in at.markdown)
    assert not any("999" in m.value for m in at.markdown)


def test_assumed_cells_reproject_when_confirmed_price_changes():
    at = _run(OE_PAGE, stock_price=150, stock_price_growth_rate=7.0)
    assert not at.exception, [e.value for e in at.exception]
    assert at.number_input(key="oe_price_2028").value == pytest.approx(150 * 1.07**2)
    at.session_state["stock_price"] = 200
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.number_input(key="oe_price_2028").value == pytest.approx(200 * 1.07**2)


def test_assumed_cells_reproject_when_growth_changes():
    at = _run(OE_PAGE, stock_price=150, stock_price_growth_rate=7.0)
    assert not at.exception, [e.value for e in at.exception]
    at.session_state["stock_price_growth_rate"] = 10.0
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.number_input(key="oe_price_2028").value == pytest.approx(150 * 1.10**2)


def test_pending_disappears_once_price_equals_candidate():
    record_stock_quote_candidate(199.5, store_path=paths_mod.CANDIDATE_STORE_PATH)
    at = _run(OE_PAGE, stock_price=199.5)
    assert not at.exception, [e.value for e in at.exception]
    assert not any("Pending" in m.value for m in at.markdown)


def test_page_shows_pending_candidate_and_keeps_confirmed_price():
    record_stock_quote_candidate(199.5, store_path=paths_mod.CANDIDATE_STORE_PATH)
    at = _run(OE_PAGE, stock_price=150)
    assert not at.exception, [e.value for e in at.exception]
    assert any("Pending \\$199.50" in m.value and "Review Changes" in m.value for m in at.markdown)
    assert any("\\$150.00" in m.value for m in at.markdown)
