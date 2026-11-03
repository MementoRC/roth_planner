"""Issue #109: the employer stock ticker is editable on Profile > Assumptions."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from config.loader import load_defaults
from config.scalar_registry import normalize_ticker, scalar_default

TICKER_LABEL = "Stock ticker"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" foo ", "FOO"), ("acme", "ACME"), ("", None), ("   ", None), (None, None)],
)
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == (expected or scalar_default("_stock_ticker"))


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _ticker_input(at: AppTest):
    found = [t for t in at.text_input if t.label == TICKER_LABEL]
    assert len(found) == 1
    return found[0]


def test_field_renders_and_updates_price_label():
    at = AppTest.from_file("app.py", default_timeout=30)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = "assumptions"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    _ticker_input(at).set_value(" foo ").run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["_stock_ticker"] == "FOO"
    assert any(n.label == "FOO Current Price" for n in at.number_input)


def test_ticker_edit_persists_to_user_defaults(tmp_path, monkeypatch):
    app_path = str(Path(__file__).resolve().parent.parent / "app.py")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
    monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
    at = AppTest.from_file(app_path, default_timeout=30)
    at.session_state["nav_page"] = "assumptions"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    _ticker_input(at).set_value("foo").run()
    assert not at.exception, [e.value for e in at.exception]
    assert load_defaults()["stock_ticker"] == "FOO"
