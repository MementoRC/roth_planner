"""get_store(): override, demo, Pyodide and the local file store."""

from __future__ import annotations

import sys

import pytest

import engine.store as store_pkg
from engine.demo_mode import enter_demo_mode, leave_demo_mode
from engine.store import get_store, set_store
from engine.store.backend import DictBackend, NullBackend
from engine.store.sqlite_backend import MEMORY, SqliteBackend


@pytest.fixture
def no_override():
    set_store(None)
    yield
    store_pkg._reset_cached_stores()


def test_local_default_is_one_file_store_at_db_path(no_override, tmp_path):
    first = get_store()
    first.put("ns", 1)
    assert get_store() is first
    assert isinstance(first.backend, SqliteBackend)
    assert store_pkg.db_path().exists()
    assert store_pkg.db_path().is_relative_to(tmp_path)


def test_demo_mode_reads_absent_drops_writes_and_spares_the_real_store(no_override):
    get_store().put("ns", {"real": 1})
    enter_demo_mode()
    try:
        demo = get_store()
        assert isinstance(demo.backend, NullBackend)
        assert demo.get("ns") is None
        demo.put("ns", {"demo": 1})
    finally:
        leave_demo_mode()
    assert get_store().get("ns") == {"real": 1}


def test_pyodide_uses_one_in_memory_session_store(no_override, monkeypatch):
    monkeypatch.setattr(store_pkg, "is_pyodide", lambda: True)
    session = get_store()
    session.put("ns", 1)
    assert get_store() is session
    assert isinstance(session.backend, SqliteBackend)
    assert session.backend.location == MEMORY
    assert not store_pkg.db_path().exists()


def test_pyodide_without_sqlite3_falls_back_to_the_dict_backend(no_override, monkeypatch):
    monkeypatch.setattr(store_pkg, "is_pyodide", lambda: True)
    monkeypatch.setitem(sys.modules, "engine.store.sqlite_backend", None)
    assert isinstance(get_store().backend, DictBackend)


def test_tests_never_point_at_the_repo_root_database(tmp_path):
    assert store_pkg.db_path() != store_pkg._DEFAULT_DB_PATH
    assert store_pkg.db_path().is_relative_to(tmp_path)


def test_demo_mode_hides_an_installed_store(store):
    store.put("ns", {"real": 1})
    enter_demo_mode()
    try:
        assert isinstance(get_store().backend, NullBackend)
        assert get_store().get("ns") is None
        get_store().put("ns", {"demo": 1})
    finally:
        leave_demo_mode()
    assert get_store() is store
    assert store.get("ns") == {"real": 1}
