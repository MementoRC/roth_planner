"""Renamed pages show their new name as the page title (redesign PR 3a)."""

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


@pytest.mark.parametrize(
    ("page_id", "title"),
    [
        ("overview", "📊 Overview — Is Converting Worth It?"),
        ("conversions", "📋 Conversion Plan — 20-Year Grid"),
        ("options", "📝 Option Exercises"),
        ("sweet", "🎯 Sweet Spot"),
        ("aca", "🏥 ACA + IRMAA"),
        ("compare", "⚖️ Compare Scenarios"),
        ("household", "👤 Household & Accounts"),
        ("assumptions", "📐 Assumptions"),
        ("holdings", "💼 Holdings"),
        ("import", "📥 Import Statements"),
        ("review", "🎛️ Review Changes"),
        ("backup", "🔐 Backup & Transfer"),
    ],
)
def test_renamed_page_title(page_id, title):
    at = AppTest.from_file("app.py", default_timeout=60)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = page_id
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert title in [t.value for t in at.title]
