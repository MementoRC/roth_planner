"""Demo-loaded data can never be saved to real storage (demo-exit write leak).

The 2026-11-02 browser pass left demo mode and the personal settings file was
rewritten: demo mode was checked when a save ran, not when its values were
loaded, so a run (or a stored object) that loaded demo values and saved after
the shared marker went away wrote them into the real file and database.

Also covers the dated backup taken on leaving demo mode, the dollar axis tick
stops ("$500m" on an all-$0 axis), and the demo caption. All data is synthetic
and every path is a tmp path (tests/conftest.py).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import plotly.graph_objects as go
import pytest

import config.loader as loader
import engine.demo_mode as demo_mod
import engine.store as store_pkg
import views.setup._state as state_mod
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.store.backend import DictBackend
from engine.store.core import Store
from engine.store.namespaces import CANDIDATE_STORE, TRUST_CHOICES
from views import _format

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def session(monkeypatch):
    fake: dict = {}
    monkeypatch.setattr(state_mod.st, "session_state", fake)
    monkeypatch.setattr(state_mod, "load_instance_owner", lambda: None)
    return fake


@pytest.fixture
def demo_store():
    session_store = Store(DictBackend())
    store_pkg.set_demo_store_provider(lambda: session_store)
    yield session_store
    store_pkg.set_demo_store_provider(None)
    demo_mod.leave_demo_mode()


@pytest.fixture
def real_settings():
    path = loader._USER_DEFAULTS_PATH  # tmp (conftest)
    path.write_text(json.dumps({"your_age": 61, "stock_ticker": "REAL"}, indent=2))
    return path


def _bytes(path: Path) -> bytes:
    return path.read_bytes()


# --- (a) a run that loaded in demo mode never saves to the real file ----------


def test_demo_seeded_run_does_not_save_settings_after_marker_removed(
    session, demo_store, real_settings
):
    """Another tab leaves demo mode while this run holds demo values."""
    before = _bytes(real_settings)
    demo_mod.enter_demo_mode()
    session[state_mod.STORAGE_MODE_KEY] = True  # seeded in demo mode
    state_mod.bind_run_storage_mode()
    demo_mod.leave_demo_mode()  # the other tab
    loader.save_user_defaults({"your_age": 55, "stock_ticker": "ACME"})
    assert _bytes(real_settings) == before


def test_mode_mismatch_clears_session_and_reruns(session, demo_store, real_settings, monkeypatch):
    """A session seeded in demo mode is cleared for reseed once the marker is gone."""
    reruns: list[bool] = []
    monkeypatch.setattr(state_mod.st, "rerun", lambda: reruns.append(True))
    session[state_mod.STORAGE_MODE_KEY] = True
    session["_seeded"] = True
    state_mod.bind_run_storage_mode()
    assert reruns == [True]
    assert "_seeded" not in session
    assert session[state_mod.STORAGE_MODE_KEY] is False
    assert demo_mod.demo_for_run()  # the clearing run stays demo-bound


# --- (b) objects loaded in demo mode never save to the real store -------------


def test_candidate_store_loaded_in_demo_never_saves_to_real_store(store, demo_store):
    demo_mod.enter_demo_mode()
    loaded = CandidateStore.load()
    demo_mod.leave_demo_mode()
    loaded.save()
    assert store.get(CANDIDATE_STORE) is None
    assert demo_store.get(CANDIDATE_STORE) is not None


def test_choice_map_loaded_in_demo_never_saves_to_real_store(store, demo_store):
    demo_mod.enter_demo_mode()
    loaded = ChoiceMap.load()
    demo_mod.leave_demo_mode()
    loaded.save()
    assert store.get(TRUST_CHOICES) is None
    assert demo_store.get(TRUST_CHOICES) is not None


# --- (c) real mode is unchanged ------------------------------------------------


def test_real_mode_load_and_save_still_reach_real_storage(store, real_settings):
    CandidateStore.load().save()
    ChoiceMap.load().save()
    assert store.get(CANDIDATE_STORE) is not None
    assert store.get(TRUST_CHOICES) is not None
    loader.save_user_defaults({"your_age": 62})
    assert json.loads(real_settings.read_text())["your_age"] == 62


# --- (d) the "Leave demo mode" click run writes nothing real -----------------


def test_exit_demo_click_run_makes_no_real_write(session, store, demo_store, real_settings):
    before = _bytes(real_settings)
    demo_mod.enter_demo_mode()
    session["your_age"] = 55  # demo values still in the session
    session["_stock_ticker"] = "ACME"
    state_mod.exit_demo_mode()
    state_mod.autosave_user_defaults()  # the rest of the click run
    CandidateStore().save()
    ChoiceMap().save()
    assert _bytes(real_settings) == before
    assert store.get(CANDIDATE_STORE) is None
    assert store.get(TRUST_CHOICES) is None


def test_exit_demo_still_reads_the_real_owner(session, store, demo_store, monkeypatch):
    """audit-1026 F-21: the owner re-read reaches the real store from a demo-bound run."""
    from engine import instance_identity

    instance_identity.save_instance_owner("you")  # real store
    monkeypatch.setattr(state_mod, "load_instance_owner", instance_identity.load_instance_owner)
    demo_mod.enter_demo_mode()
    session["instance_owner"] = "spouse"
    state_mod.exit_demo_mode()
    assert session["instance_owner"] == "you"


# --- safety net: dated backup on leaving demo mode --------------------------


def test_exit_demo_backs_up_settings_and_database(
    session, demo_store, real_settings, tmp_path, monkeypatch
):
    monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
    db = tmp_path / "personal.db"
    conn = sqlite3.connect(db)
    conn.execute("create table t (v text)")
    conn.execute("insert into t values ('synthetic')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(state_mod, "personal_db_file", lambda: db)
    demo_mod.enter_demo_mode()
    state_mod.exit_demo_mode()

    settings_copies = list(
        real_settings.parent.glob(".json_backup/*-demo-exit/.user_defaults.json")
    )
    db_copies = list(db.parent.glob(".json_backup/*-demo-exit/personal.db"))
    assert len(settings_copies) == 1
    assert settings_copies[0].read_bytes() == real_settings.read_bytes()
    assert len(db_copies) == 1
    copy = sqlite3.connect(db_copies[0])
    assert copy.execute("select v from t").fetchall() == [("synthetic",)]
    copy.close()


def test_backup_skips_missing_files(tmp_path):
    from datetime import datetime

    from engine.store.backup import backup_files

    assert backup_files([tmp_path / "absent.json"], label="x", now=datetime(2026, 1, 2)) == []


# --- "$500m" ticks -------------------------------------------------------------


def test_dollar_tick_stops_use_plain_dollars_below_one_dollar_steps():
    stops = _format.DOLLAR_TICKFORMATSTOPS
    assert stops == [{"dtickrange": [None, 1], "value": "$,.2~f"}]
    fig = go.Figure(go.Scatter(x=[0, 1], y=[0, 0]))
    fig.update_layout(yaxis_tickformat=_format.DOLLAR_TICKFORMAT, yaxis_tickformatstops=stops)
    stop = fig.layout.yaxis.tickformatstops[0]
    assert stop.value == "$,.2~f"
    assert "s" not in stop.value  # never an SI (milli) prefix


@pytest.mark.parametrize(
    "view",
    [
        "aca_irmaa",
        "asset_location",
        "comparator",
        "dashboard",
        "planner",
        "rmd_squeeze",
        "sweet_spot",
    ],
)
def test_every_dollar_axis_passes_the_tick_stops(view):
    src = (REPO / "views" / f"{view}.py").read_text()
    axes = src.count("tickformat=DOLLAR_TICKFORMAT") + src.count("tickformat=DOLLAR_TICKS,")
    stops = src.count("tickformatstops=DOLLAR_TICKFORMATSTOPS")
    assert axes > 0
    assert stops == axes


# --- demo caption ---------------------------------------------------------------


def test_demo_caption_says_every_change_is_session_only():
    src = (REPO / "views" / "setup" / "data_bridge.py").read_text()
    assert "Every change you make now" in src
    assert "is never saved" in src
    assert "What you import or confirm" not in src
