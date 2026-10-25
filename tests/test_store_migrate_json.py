"""migrate_json: the one-time JSON → database import (spec §4)."""

from __future__ import annotations

import json
import stat

import pytest

from engine.store import legacy_files
from engine.store.core import META_NAMESPACE, SINGLE_KEY, Store, register_namespace
from engine.store.legacy_files import (
    LegacyFile,
    declare_store,
    per_entry_reader,
    register_legacy_file,
    single_document_reader,
)
from engine.store.migrate_json import BACKUP_DIR_NAME, marker_key, migrate_json
from engine.store.sqlite_backend import MEMORY, SqliteBackend


@pytest.fixture(autouse=True)
def _only_this_tests_namespaces(monkeypatch):
    """Migrate only what the test declares; conftest restores the real registry afterwards."""
    monkeypatch.setattr(legacy_files, "_REGISTRY", {})


@pytest.fixture
def mem_store():
    store = Store(SqliteBackend(MEMORY))
    yield store
    store.backend.close()


def _backed_up(tmp_path) -> list[str]:
    return sorted(p.name for p in (tmp_path / BACKUP_DIR_NAME).glob("*/*"))


def test_imports_a_document_marks_it_and_moves_the_file(mem_store, tmp_path):
    declare_store("t_one", ".t_one.json")
    (tmp_path / ".t_one.json").write_text(json.dumps({"a": 1}))

    result = migrate_json(mem_store, tmp_path)

    assert mem_store.get("t_one") == {"a": 1}
    assert mem_store.has(META_NAMESPACE, key=marker_key("t_one"))
    assert not (tmp_path / ".t_one.json").exists()
    assert _backed_up(tmp_path) == [".t_one.json"]
    assert result.imported == (".t_one.json",)
    assert result.backup_dir is not None
    assert stat.S_IMODE((tmp_path / BACKUP_DIR_NAME).stat().st_mode) == 0o700
    assert stat.S_IMODE(result.backup_dir.stat().st_mode) == 0o700
    assert (result.error, result.read_only, result.unmoved) == (None, (), ())


def test_a_namespace_without_a_file_is_marked_and_nothing_moves(mem_store, tmp_path):
    declare_store("t_one", ".t_one.json")
    result = migrate_json(mem_store, tmp_path)
    assert mem_store.has(META_NAMESPACE, key=marker_key("t_one"))
    assert result.imported == ()
    assert not (tmp_path / BACKUP_DIR_NAME).exists()


def test_a_marked_namespace_is_never_imported_again(mem_store, tmp_path):
    declare_store("t_one", ".t_one.json")
    migrate_json(mem_store, tmp_path)
    (tmp_path / ".t_one.json").write_text(json.dumps({"stray": 1}))

    result = migrate_json(mem_store, tmp_path)

    assert mem_store.get("t_one") is None
    assert (tmp_path / ".t_one.json").exists()
    assert result.imported == ()


def test_an_unreadable_file_rolls_back_the_whole_run_and_moves_nothing(mem_store, tmp_path):
    # Sorted order imports a_good first, so the rollback is what removes it.
    declare_store("a_good", ".a_good.json")
    declare_store("z_bad", ".z_bad.json")
    (tmp_path / ".a_good.json").write_text(json.dumps({"a": 1}))
    (tmp_path / ".z_bad.json").write_text("{not json")

    result = migrate_json(mem_store, tmp_path)

    assert mem_store.get("a_good") is None
    assert mem_store.keys(META_NAMESPACE) == []
    assert (tmp_path / ".a_good.json").exists()
    assert (tmp_path / ".z_bad.json").exists()
    assert not (tmp_path / BACKUP_DIR_NAME).exists()
    assert result.failed_file == ".z_bad.json"
    assert "JSONDecodeError" in (result.error or "")
    assert result.read_only == ("a_good", "z_bad")
    assert result.imported == ()


def test_namespaces_marked_earlier_are_not_part_of_a_failed_run(mem_store, tmp_path):
    declare_store("a_done", ".a_done.json")
    migrate_json(mem_store, tmp_path)
    declare_store("z_bad", ".z_bad.json")
    (tmp_path / ".z_bad.json").write_text("{not json")

    result = migrate_json(mem_store, tmp_path)

    assert result.read_only == ("z_bad",)
    assert mem_store.has(META_NAMESPACE, key=marker_key("a_done"))


def test_a_symlinked_legacy_file_is_refused(mem_store, tmp_path):
    declare_store("t_one", ".t_one.json")
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"a": 1}))
    (tmp_path / ".t_one.json").symlink_to(target)

    result = migrate_json(mem_store, tmp_path)

    assert result.failed_file == ".t_one.json"
    assert mem_store.get("t_one") is None
    assert (tmp_path / ".t_one.json").is_symlink()


def test_unregistered_files_are_never_touched_or_marked(mem_store, tmp_path):
    (tmp_path / ".stray.json").write_text("{}")
    declare_store("t_one", ".t_one.json")
    migrate_json(mem_store, tmp_path)
    assert (tmp_path / ".stray.json").exists()
    assert mem_store.keys(META_NAMESPACE) == [marker_key("t_one")]


def test_a_keyed_file_becomes_one_row_per_entry(mem_store, tmp_path):
    declare_store("t_keyed", ".t_keyed.json", per_entry_reader)
    (tmp_path / ".t_keyed.json").write_text(json.dumps({"k1": {"v": 1}, "k2": {"v": 2}}))
    migrate_json(mem_store, tmp_path)
    assert mem_store.keys("t_keyed") == ["k1", "k2"]
    assert mem_store.get("t_keyed", key="k2") == {"v": 2}


def test_legacy_rows_are_v1_so_a_newer_namespace_upgrades_them_on_read(mem_store, tmp_path):
    register_namespace("t_v2", version=2, upgraders={1: lambda v: {**v, "upgraded": True}})
    register_legacy_file(LegacyFile("t_v2", ".t_v2.json", single_document_reader))
    (tmp_path / ".t_v2.json").write_text(json.dumps({"a": 1}))

    migrate_json(mem_store, tmp_path)

    row = mem_store.backend.get("t_v2", SINGLE_KEY)
    assert row is not None
    assert row.schema_version == 1
    assert mem_store.get("t_v2") == {"a": 1, "upgraded": True}


def test_a_file_that_cannot_be_moved_is_reported_and_stays_imported(
    mem_store, tmp_path, monkeypatch
):
    declare_store("t_one", ".t_one.json")
    (tmp_path / ".t_one.json").write_text(json.dumps({"a": 1}))

    def refuse(src, dst):
        raise OSError("read-only folder")

    monkeypatch.setattr("engine.store.migrate_json.os.replace", refuse)
    result = migrate_json(mem_store, tmp_path)

    assert mem_store.get("t_one") == {"a": 1}
    assert result.unmoved == (".t_one.json",)
    assert (tmp_path / ".t_one.json").exists()
