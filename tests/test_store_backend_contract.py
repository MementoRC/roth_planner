"""Every backend must store, overwrite, delete, list and roll back rows the same way."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from engine.store.backend import Backend, DictBackend, Row

ROW_A = Row('{"a": 1}', 1, "2026-01-01T00:00:00+00:00")
ROW_B = Row('{"b": 2}', 1, "2026-01-02T00:00:00+00:00")

BACKEND_KINDS = ["dict"]


def _make(kind: str, tmp_path) -> Backend:
    if kind == "dict":
        return DictBackend()
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
