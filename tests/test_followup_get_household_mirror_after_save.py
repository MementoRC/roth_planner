"""Follow-up: get_household() mirrors resolved values into session_state only
after the autosave commits.

The mirror used to run BEFORE the save, so a refused save left session_state
holding resolved values the committed baseline on disk did not have. Synthetic
figures only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import engine.data_sources.committed as committed_mod
import engine.data_sources.orchestrator as orchestrator_mod
import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from engine.store import StoreUnavailableError

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
SESSION_IRA = 500_000
RESOLVED_IRA = 123_456


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    real_resolve = orchestrator_mod.resolve_for_app

    def resolving_to_other_value(*args, **kwargs):
        res = real_resolve(*args, **kwargs)
        res.result.household.your_ira = RESOLVED_IRA
        return res

    monkeypatch.setattr(orchestrator_mod, "resolve_for_app", resolving_to_other_value)


def _app() -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = "overview"
    at.session_state["your_ira"] = SESSION_IRA
    return at


def test_refused_save_leaves_writeback_keys_unchanged(monkeypatch):
    def refused(*args, **kwargs):
        raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")

    monkeypatch.setattr(committed_mod, "save_committed", refused)
    at = _app()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["your_ira"] == SESSION_IRA


def test_successful_save_mirrors_in_the_same_run():
    at = _app()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["your_ira"] == RESOLVED_IRA


def test_no_change_save_still_mirrors():
    at = _app()
    at.run()  # first load commits the baseline
    at.session_state["your_ira"] = SESSION_IRA
    at.run()  # baseline unchanged: nothing to write, mirror must still happen
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["your_ira"] == RESOLVED_IRA
