"""File-level guarantees of SqliteBackend: 0600, no symlinks, clear failures, waiting writers."""

from __future__ import annotations

import sqlite3
import stat
import threading
import time

import pytest

from engine.store.backend import Row
from engine.store.errors import StoreUnavailableError
from engine.store.sqlite_backend import SqliteBackend

ROW = Row('{"a": 1}', 1, "2026-01-01T00:00:00+00:00")


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_new_database_file_is_owner_only(tmp_path):
    path = tmp_path / "store.db"
    path.touch()
    path.chmod(0o644)
    SqliteBackend(path).close()
    assert _mode(path) == 0o600


def test_wal_side_files_are_owner_only_after_a_commit(tmp_path):
    path = tmp_path / "store.db"
    backend = SqliteBackend(path)
    try:
        backend.begin()
        backend.put("ns", "k", ROW)
        backend.commit()
        sides = [path.with_name(path.name + s) for s in ("-wal", "-shm")]
        assert all(p.exists() for p in sides), "WAL mode should leave side files while open"
        for p in sides:
            p.chmod(0o644)
        backend.begin()
        backend.put("ns", "k2", ROW)
        backend.commit()
        assert all(_mode(p) == 0o600 for p in sides)
    finally:
        backend.close()


def test_symlinked_path_is_refused_and_target_untouched(tmp_path):
    target = tmp_path / "elsewhere.db"
    link = tmp_path / "store.db"
    link.symlink_to(target)
    with pytest.raises(StoreUnavailableError) as ei:
        SqliteBackend(link)
    assert ei.value.location == str(link)
    assert not target.exists()


def test_garbage_file_reports_storage_unavailable(tmp_path):
    path = tmp_path / "store.db"
    path.write_bytes(b"this is not a sqlite database " * 50)
    with pytest.raises(StoreUnavailableError) as ei:
        SqliteBackend(path)
    assert ei.value.location == str(path)


def test_newer_layout_reports_storage_unavailable(tmp_path):
    path = tmp_path / "store.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(StoreUnavailableError, match="newer") as ei:
        SqliteBackend(path)
    assert ei.value.location == str(path)


def test_rows_survive_reopening(tmp_path):
    path = tmp_path / "store.db"
    first = SqliteBackend(path)
    first.put("ns", "k", ROW)
    first.close()
    second = SqliteBackend(path)
    assert second.get("ns", "k") == ROW
    second.close()


def test_second_writer_waits_for_the_first(tmp_path):
    path = tmp_path / "store.db"
    first = SqliteBackend(path)
    second = SqliteBackend(path)
    started = threading.Event()
    errors: list[BaseException] = []

    def write_second() -> None:
        started.set()
        try:
            second.begin()
            second.put("ns", "b", ROW)
            second.commit()
        except BaseException as exc:  # surfaced via the assertion below
            errors.append(exc)

    try:
        first.begin()
        first.put("ns", "a", ROW)
        worker = threading.Thread(target=write_second, daemon=True)
        worker.start()
        started.wait()
        time.sleep(0.2)
        assert worker.is_alive(), "second writer should be blocked by the first transaction"
        first.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert not errors
        assert second.get("ns", "a") == ROW
        assert first.get("ns", "b") == ROW
    finally:
        first.close()
        second.close()
