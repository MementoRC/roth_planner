"""Import writes grant strikes to disk with the snapshot they price. Synthetic data only."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import streamlit as st

import config.loader as loader
import engine.demo_mode as demo_mode
import views.setup._state as state_mod
import views.setup.data_bridge as db


@pytest.fixture
def defaults_file(tmp_path, monkeypatch):
    path = tmp_path / ".user_defaults.json"
    monkeypatch.setattr(loader, "_USER_DEFAULTS_PATH", path)
    monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
    monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
    monkeypatch.setattr(demo_mode, "DEMO_MODE_PATH", tmp_path / ".demo_mode")
    return path


def _run_import(monkeypatch, strikes) -> None:
    scalar_updates = {"your_ira": 654_321}
    if strikes is not None:
        scalar_updates["_user_grant_strikes"] = strikes
    result = SimpleNamespace(
        scalar_updates=scalar_updates,
        joint_conflicts=(),
        account_type_overrides={},
        magi_candidates={},
        slot_captured_at={},
        snapshot=None,
        ytd=None,
        ledger=None,
    )
    ready = SimpleNamespace(
        bundle=None,
        plan=None,
        local=SimpleNamespace(account_type_overrides={}, slot_captured_at={}),
        selected=frozenset(),
        operator=None,
        data={"sections": {"setup_scalars": {}}},
        version="v5",
    )
    monkeypatch.setattr(db, "apply_import", lambda *a, **k: result)
    monkeypatch.setattr(db, "commit_import", lambda *a, **k: None)
    monkeypatch.setattr(db, "_stamp_bundle_scalars_in_baseline", lambda *a: None)
    monkeypatch.setattr(db, "slots_with_replaced_accounts", lambda *a: [])
    monkeypatch.setattr(db, "discard_pdf_balance_candidates", lambda *a: None)
    monkeypatch.setattr(db, "_settle_ytd", lambda *a: None)
    monkeypatch.setattr(st, "rerun", lambda: None)
    st.session_state.pop("_pending_defaults", None)
    db._apply_ready_import(ready, "synthetic.enc")


def test_import_strikes_survive_demo_exit_reload(defaults_file, monkeypatch):
    _run_import(monkeypatch, {"2019": 11.0, "2020": 22.0})

    assert json.loads(defaults_file.read_text())["grant_strikes"]["2019"] == 11.0
    state_mod._clear_session_for_reseed()
    assert loader.load_defaults()["grant_strikes"]["2019"] == 11.0


def test_empty_strikes_do_not_clobber_hand_edited(defaults_file, monkeypatch):
    defaults_file.write_text(json.dumps({"grant_strikes": {"2019": 33.0}}))
    _run_import(monkeypatch, {})

    assert json.loads(defaults_file.read_text())["grant_strikes"] == {"2019": 33.0}


def test_no_strikes_key_leaves_file_untouched(defaults_file, monkeypatch):
    _run_import(monkeypatch, None)

    assert not defaults_file.exists() or "grant_strikes" not in json.loads(
        defaults_file.read_text()
    )


def test_demo_mode_does_not_write_strikes(defaults_file, monkeypatch):
    demo_mode.enter_demo_mode()
    _run_import(monkeypatch, {"2019": 11.0})

    assert not defaults_file.exists()
