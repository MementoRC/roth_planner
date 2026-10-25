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
    META_NAMESPACE,
    CorruptStoreError,
    StoreUnavailableError,
    get_store,
    migration_notice,
    set_store,
)
from engine.store.backend import DictBackend, NullBackend
from engine.store.core import Store
from engine.store.legacy_files import declare_store, registered
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
    """Where get_store() looks for the dotfiles (redirected to tmp by conftest)."""
    return store_pkg.legacy_dir()


def test_legacy_dir_is_redirected_away_from_the_repo_root(tmp_path):
    assert store_pkg.legacy_dir() != store_pkg._DEFAULT_DB_PATH.parent
    assert store_pkg.legacy_dir().is_relative_to(tmp_path)


def test_db_elsewhere_still_imports_the_legacy_files(no_override, monkeypatch, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("ROTH_PLANNER_DB", str(elsewhere / "planner.db"))
    declare_store("t_sel", ".t_sel.json")
    legacy = _legacy_dir() / ".t_sel.json"
    legacy.write_text(json.dumps({"a": 1}))

    assert get_store().get("t_sel") == {"a": 1}
    assert not legacy.exists()
    assert (elsewhere / "planner.db").exists()


def test_existing_db_is_never_deleted_when_import_fails_even_if_it_appeared_late(
    no_override, monkeypatch
):
    db = store_pkg.db_path()
    existing = Store(SqliteBackend(db))
    existing.put("not_migrated", {"kept": 1})
    existing.backend.close()
    declare_store("t_bad", ".t_bad.json")
    (_legacy_dir() / ".t_bad.json").write_text("{not json")
    real_exists = Path.exists
    with monkeypatch.context() as patch:
        # Simulates another process creating the database right after an existence check.
        patch.setattr(
            Path,
            "exists",
            lambda self, *a, **k: False if self == db else real_exists(self, *a, **k),
        )
        get_store()
        store_pkg._reset_cached_stores()

    assert db.exists()
    assert Store(SqliteBackend(db)).get("not_migrated") == {"kept": 1}


def test_unexpected_import_error_removes_a_database_this_run_created(no_override, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("synthetic import failure")

    monkeypatch.setattr("engine.store.migrate_json.migrate_json", boom)

    with pytest.raises(RuntimeError, match="synthetic import failure"):
        get_store()
    assert not store_pkg.db_path().exists()


def test_unexpected_import_error_keeps_an_existing_database(no_override, monkeypatch):
    Store(SqliteBackend(store_pkg.db_path())).backend.close()

    def boom(*_args, **_kwargs):
        raise RuntimeError("synthetic import failure")

    monkeypatch.setattr("engine.store.migrate_json.migrate_json", boom)

    with pytest.raises(RuntimeError, match="synthetic import failure"):
        get_store()
    assert store_pkg.db_path().exists()


def test_backend_failure_after_creating_the_file_leaves_no_database(no_override, monkeypatch):
    class _FailingBackend:
        def __init__(self, location: Path) -> None:
            Path(location).touch()
            raise StoreUnavailableError(str(location), "synthetic open failure")

    monkeypatch.setattr("engine.store.sqlite_backend.SqliteBackend", _FailingBackend)

    with pytest.raises(StoreUnavailableError, match="synthetic open failure"):
        get_store()
    assert not store_pkg.db_path().exists()


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


def test_demo_mode_never_serves_the_json_fallback(no_override):
    declare_store("t_good", ".t_good.json")
    declare_store("t_bad", ".t_bad.json")
    (_legacy_dir() / ".t_good.json").write_text(json.dumps({"a": 1}))
    (_legacy_dir() / ".t_bad.json").write_text("{not json")
    assert get_store().get("t_good") == {"a": 1}  # the read-only JSON fallback is live
    enter_demo_mode()
    try:
        assert isinstance(get_store().backend, NullBackend)
        assert get_store().get("t_good") is None
    finally:
        leave_demo_mode()
    assert get_store().get("t_good") == {"a": 1}


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


def _pr3_legacy_cases() -> list:
    """(namespace, synthetic legacy document, public loader, what it reads back) per PR 3 store."""
    from datetime import datetime

    from engine.account_attribution import load_account_overrides
    from engine.account_link import load_link_map
    from engine.data_sources.candidate_store import CandidateStore
    from engine.data_sources.choices import ChoiceMap
    from engine.data_sources.committed import load_committed
    from engine.instance_identity import load_instance_owner
    from engine.pdf_owner import load_owner_map
    from engine.store import namespaces as ns
    from models.sourced import Provenance, Source, SourcedValue

    prov = Provenance(Source.PDF, datetime(2026, 1, 1, 12, 0, 0))
    committed = {"your_ira": SourcedValue(1_000.0, prov).to_json()}
    candidates = {"your_ira": {str(Source.PDF): {"value": 2_000.0, "prov": prov.to_json()}}}
    choices = {"your_ira": {"source": str(Source.PDF), "locked_at": "2026-01-01T12:00:00"}}

    def first_candidate() -> float:
        return CandidateStore.load().candidates_for("your_ira")[0].value

    def choice_source() -> object:
        choice = ChoiceMap.load().get("your_ira")
        return None if choice is None else choice.source

    return [
        pytest.param(ns.COMMITTED, committed, load_committed, committed, id="committed"),
        pytest.param(ns.CANDIDATE_STORE, candidates, first_candidate, 2_000.0, id="candidates"),
        pytest.param(ns.TRUST_CHOICES, choices, choice_source, Source.PDF, id="trust_choices"),
        pytest.param(
            ns.INSTANCE_OWNER,
            {"version": 1, "instance_owner": "spouse"},
            load_instance_owner,
            "spouse",
            id="instance_owner",
        ),
        pytest.param(
            ns.ACCOUNT_ATTRIBUTION,
            {"version": 1, "overrides": {"synthbroker|ACCT-1": "spouse"}},
            load_account_overrides,
            {("synthbroker", "ACCT-1"): "spouse"},
            id="account_attribution",
        ),
        pytest.param(
            ns.PDF_OWNER_MAP,
            {"owner a": "you"},
            load_owner_map,
            {"owner a": "you"},
            id="pdf_owner_map",
        ),
        pytest.param(
            ns.IRA_ACCOUNT_LINKS,
            {"synthbroker|X1234": ["trad_ira", "you", "IRA X1234"]},
            load_link_map,
            {"synthbroker|X1234": ("trad_ira", "you", "IRA X1234")},
            id="ira_account_links",
        ),
    ]


@pytest.mark.parametrize(("namespace", "document", "load", "expected"), _pr3_legacy_cases())
def test_a_pr2_database_imports_only_the_stores_registered_since(
    no_override, namespace, document, load, expected
):
    from engine.store.migrate_json import marker_key
    from engine.store.namespaces import PORTFOLIO

    [entry] = [e for e in registered() if e.namespace == namespace]
    existing = Store(SqliteBackend(store_pkg.db_path()))
    for other in registered():
        if other.namespace != namespace:
            existing.put(META_NAMESPACE, {"imported": False}, key=marker_key(other.namespace))
    existing.backend.close()
    legacy = _legacy_dir() / entry.filename
    legacy.write_text(json.dumps(document))
    stray = _legacy_dir() / ".portfolio_cache.json"
    stray.write_text(json.dumps({"accounts": []}))

    store = get_store()

    assert store.get(namespace) == document
    assert load() == expected
    assert store.get(PORTFOLIO) is None
    assert stray.exists()
    assert not legacy.exists()
    assert len(list((_legacy_dir() / BACKUP_DIR_NAME).glob(f"*/{entry.filename}"))) == 1
    result = migration_notice()
    assert result is not None
    assert result.imported == (entry.filename,)
