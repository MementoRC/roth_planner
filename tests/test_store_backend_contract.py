"""Every backend must store, overwrite, delete, list and roll back rows the same way."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from engine.store.backend import Backend, DictBackend, Row
from engine.store.sqlite_backend import MEMORY, SqliteBackend

ROW_A = Row('{"a": 1}', 1, "2026-01-01T00:00:00+00:00")
ROW_B = Row('{"b": 2}', 1, "2026-01-02T00:00:00+00:00")

BACKEND_KINDS = ["dict", "sqlite-memory", "sqlite-file"]


def _make(kind: str, tmp_path) -> Backend:
    if kind == "dict":
        return DictBackend()
    if kind == "sqlite-memory":
        return SqliteBackend(MEMORY)
    if kind == "sqlite-file":
        return SqliteBackend(tmp_path / "contract.db")
    raise AssertionError(kind)


@pytest.fixture(params=BACKEND_KINDS)
def backend(request, tmp_path) -> Iterator[Backend]:
    b = _make(request.param, tmp_path)
    yield b
    b.close()


def test_get_missing_is_none(backend):
    assert backend.get("ns", "k") is None


def test_put_then_get_round_trips(backend):
    backend.put("ns", "k", ROW_A)
    assert backend.get("ns", "k") == ROW_A


def test_put_overwrites(backend):
    backend.put("ns", "k", ROW_A)
    backend.put("ns", "k", ROW_B)
    assert backend.get("ns", "k") == ROW_B


def test_delete_removes_and_missing_delete_is_a_noop(backend):
    backend.put("ns", "k", ROW_A)
    backend.delete("ns", "k")
    backend.delete("ns", "k")
    assert backend.get("ns", "k") is None


def test_keys_are_sorted_and_scoped_to_the_namespace(backend):
    backend.put("ns", "b", ROW_A)
    backend.put("ns", "a", ROW_A)
    backend.put("other", "c", ROW_A)
    assert backend.keys("ns") == ["a", "b"]
    assert backend.keys("missing") == []


def test_commit_keeps_writes(backend):
    backend.begin()
    backend.put("ns", "k", ROW_A)
    backend.commit()
    assert backend.get("ns", "k") == ROW_A


def test_rollback_discards_every_write_since_begin(backend):
    backend.put("ns", "keep", ROW_A)
    backend.begin()
    backend.put("ns", "keep", ROW_B)
    backend.put("ns", "new", ROW_B)
    backend.delete("ns", "keep")
    backend.rollback()
    assert backend.get("ns", "keep") == ROW_A
    assert backend.get("ns", "new") is None


def test_commit_and_rollback_without_begin_are_noops(backend):
    backend.put("ns", "k", ROW_A)
    backend.commit()
    backend.rollback()
    assert backend.get("ns", "k") == ROW_A


def test_sqlite_transaction_blocks_other_threads_until_commit():
    backend = SqliteBackend(MEMORY)
    done = threading.Event()

    def other_writer() -> None:
        backend.put("ns", "other", ROW_B)
        done.set()

    backend.begin()
    backend.put("ns", "mine", ROW_A)
    worker = threading.Thread(target=other_writer)
    worker.start()
    assert not done.wait(0.2)
    backend.commit()
    assert done.wait(2)
    worker.join(2)
    assert backend.keys("ns") == ["mine", "other"]
    backend.close()
