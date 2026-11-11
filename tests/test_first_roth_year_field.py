"""T1 2c: per-person first-Roth-year is editable on Profile and persisted."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from config.loader import load_defaults
from engine.upload_merge import build_user_defaults_session_updates
from models.household import Household

LABEL = "First Roth IRA contribution/conversion year"
APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _inputs(at: AppTest):
    return [n for n in at.number_input if n.label == LABEL]


def _profile_app(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
    monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.session_state["nav_page"] = "household"
    return at


def test_household_default_is_none():
    hh = Household()
    assert hh.your_first_roth_year is None
    assert hh.spouse_first_roth_year is None


def test_old_saved_file_without_key_loads_as_none(tmp_path, monkeypatch):
    at = _profile_app(tmp_path, monkeypatch)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["your_first_roth_year"] is None
    assert at.session_state["spouse_first_roth_year"] is None
    assert len(_inputs(at)) == 2


def test_edit_round_trips_through_user_defaults(tmp_path, monkeypatch):
    at = _profile_app(tmp_path, monkeypatch)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    you, spouse = _inputs(at)
    you.set_value(2015).run()
    assert not at.exception, [e.value for e in at.exception]
    _inputs(at)[1].set_value(2020).run()
    assert not at.exception, [e.value for e in at.exception]
    saved = load_defaults()
    assert saved["your_first_roth_year"] == 2015
    assert saved["spouse_first_roth_year"] == 2020

    reloaded = AppTest.from_file(APP_PATH, default_timeout=30)
    reloaded.session_state["nav_page"] = "household"
    reloaded.run()
    assert not reloaded.exception, [e.value for e in reloaded.exception]
    assert reloaded.session_state["your_first_roth_year"] == 2015
    assert [n.value for n in _inputs(reloaded)] == [2015, 2020]


def test_out_of_range_stored_value_is_clamped_not_crashing(tmp_path, monkeypatch):
    at = _profile_app(tmp_path, monkeypatch)
    at.session_state["your_first_roth_year"] = 1900
    at.session_state["spouse_first_roth_year"] = 2999
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert [n.value for n in _inputs(at)] == [1998, 2026]


def test_spouse_input_hidden_value_kept_when_single(tmp_path, monkeypatch):
    at = _profile_app(tmp_path, monkeypatch)
    at.session_state["spouse_first_roth_year"] = 2010
    at.session_state["filing_status"] = "Single"
    at.session_state["_hh_filing_status_choice"] = "Single"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["spouse_first_roth_year"] == 2010


def test_import_as_spouse_cross_maps_first_roth_year():
    updates = build_user_defaults_session_updates(
        {"your_first_roth_year": 2012, "spouse_first_roth_year": 1999}, as_spouse=True
    )
    assert updates["spouse_first_roth_year"] == 2012
    assert "your_first_roth_year" not in updates


def test_import_own_file_passes_both_through():
    updates = build_user_defaults_session_updates(
        {"your_first_roth_year": 2012, "spouse_first_roth_year": None}, as_spouse=False
    )
    assert updates["your_first_roth_year"] == 2012
    assert updates["spouse_first_roth_year"] is None
