"""get_store(): override, demo, Pyodide and the local file store."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

import engine.store as store_pkg
from engine.demo_mode import enter_demo_mode, leave_demo_mode
from engine.store import (
    CorruptStoreError,
    StoreUnavailableError,
    get_store,
    migration_notice,
    set_store,
)
from engine.store.backend import DictBackend, NullBackend
from engine.store.core import Store
from engine.store.legacy_files import declare_store
from engine.store.migrate_json import BACKUP_DIR_NAME
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


def _legacy_dir() -> Path:
    """Where get_store() looks for the dotfiles: beside the (tmp) database."""
    return store_pkg.db_path().parent


def test_first_file_store_imports_registered_json_beside_the_database(no_override):
    declare_store("t_sel", ".t_sel.json")
    legacy = _legacy_dir() / ".t_sel.json"
    legacy.write_text(json.dumps({"a": 1}))

    store = get_store()

    assert store.get("t_sel") == {"a": 1}
    assert not legacy.exists()
    result = migration_notice()
    assert result is not None
    assert ".t_sel.json" in result.imported
    assert result.backup_dir is not None
    assert (result.backup_dir / ".t_sel.json").exists()


def test_failed_import_on_a_new_database_reads_json_refuses_saves_and_leaves_no_db(no_override):
    declare_store("t_good", ".t_good.json")
    declare_store("t_bad", ".t_bad.json")
    good = _legacy_dir() / ".t_good.json"
    good.write_text(json.dumps({"a": 1}))
    (_legacy_dir() / ".t_bad.json").write_text("{not json")

    store = get_store()

    assert store.get("t_good") == {"a": 1}
    with pytest.raises(CorruptStoreError, match="t_bad"):
        store.get("t_bad")
    with pytest.raises(StoreUnavailableError, match="saving is disabled"):
        store.put("t_good", {"a": 2})
    assert json.loads(good.read_text()) == {"a": 1}
    assert not store_pkg.db_path().exists()
    result = migration_notice()
    assert result is not None
    assert result.failed_file == ".t_bad.json"
    assert {"t_good", "t_bad"} <= set(result.read_only)


def test_failed_import_into_an_existing_database_routes_only_that_run(no_override):
    existing = Store(SqliteBackend(store_pkg.db_path()))
    existing.put("not_migrated", {"kept": 1})
    existing.backend.close()
    declare_store("t_good", ".t_good.json")
    declare_store("t_bad", ".t_bad.json")
    (_legacy_dir() / ".t_good.json").write_text(json.dumps({"a": 1}))
    (_legacy_dir() / ".t_bad.json").write_text("{not json")

    store = get_store()

    assert store.get("not_migrated") == {"kept": 1}
    store.put("not_migrated", {"kept": 2})  # the database still serves other namespaces
    assert store.get("not_migrated") == {"kept": 2}
    assert store.get("t_good") == {"a": 1}
    with pytest.raises(StoreUnavailableError, match="saving is disabled"):
        store.put("t_good", {"a": 2})
    assert store_pkg.db_path().exists()


def test_starting_in_demo_mode_then_leaving_still_imports(no_override):
    declare_store("t_sel", ".t_sel.json")
    legacy = _legacy_dir() / ".t_sel.json"
    legacy.write_text(json.dumps({"a": 1}))
    enter_demo_mode()
    try:
        assert get_store().get("t_sel") is None
        assert legacy.exists()
        assert migration_notice() is None
    finally:
        leave_demo_mode()
    assert get_store().get("t_sel") == {"a": 1}
    assert not legacy.exists()


def test_pyodide_never_imports_json(no_override, monkeypatch):
    monkeypatch.setattr(store_pkg, "is_pyodide", lambda: True)
    declare_store("t_sel", ".t_sel.json")
    legacy = _legacy_dir() / ".t_sel.json"
    legacy.write_text(json.dumps({"a": 1}))
    assert get_store().get("t_sel") is None
    assert legacy.exists()
    assert migration_notice() is None


def test_concurrent_first_calls_open_and_import_once(no_override):
    declare_store("t_sel", ".t_sel.json")
    (_legacy_dir() / ".t_sel.json").write_text(json.dumps({"a": 1}))
    barrier = threading.Barrier(2)
    got: list[Store] = []

    def first_call() -> None:
        barrier.wait()
        got.append(get_store())

    threads = [threading.Thread(target=first_call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(got) == 2
    assert got[0] is got[1]
    assert got[0].get("t_sel") == {"a": 1}
    assert len(list((_legacy_dir() / BACKUP_DIR_NAME).glob("*/.t_sel.json"))) == 1
