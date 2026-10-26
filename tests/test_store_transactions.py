"""Cross-store transactions (spec §2, store PR 4): what one block spanning several
namespaces does on every backend the app runs on. Synthetic values only."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from engine.store.backend import Backend, DictBackend, NullBackend, RoutedBackend
from engine.store.core import Store
from engine.store.errors import CorruptStoreError, StoreUnavailableError
from engine.store.json_readonly import JsonReadOnlyBackend
from engine.store.sqlite_backend import MEMORY, SqliteBackend


def _backend(kind: str) -> Backend:
    return SqliteBackend(MEMORY) if kind == "sqlite" else DictBackend()


@pytest.fixture(params=["sqlite", "dict"])
def mem_store(request) -> Iterator[Store]:
    store = Store(_backend(request.param))
    yield store
    store.backend.close()


def _write_then_hit_read_only(store: Store) -> None:
    with store.transaction():
        store.put("writable", 1)
        store.put("read_only", 2)


@pytest.mark.parametrize("default", ["sqlite", "dict"])
def test_a_write_to_a_read_only_namespace_rolls_back_the_whole_block(default, tmp_path: Path):
    """A failed JSON import routes its namespaces read-only; a flow touching one writes nothing."""
    read_only = JsonReadOnlyBackend(tmp_path, [], reason="synthetic import failure")
    store = Store(RoutedBackend(_backend(default), {"read_only": read_only}))
    with pytest.raises(StoreUnavailableError, match="saving is disabled"):
        _write_then_hit_read_only(store)
    assert store.get("writable") is None
    store.backend.close()


def _null_block_that_fails(store: Store) -> None:
    with store.transaction():
        store.put("a", 1)
        raise RuntimeError("synthetic body error")


def test_a_null_store_block_drops_its_writes_and_still_propagates_errors():
    """Demo mode: a wrapped flow runs, saves nothing, and a real error is not hidden."""
    store = Store(NullBackend())
    with store.transaction():
        store.put("a", 1)
        store.put("b", 2)
    assert store.get("a") is None
    assert store.get("b") is None
    with pytest.raises(RuntimeError, match="synthetic body error"):
        _null_block_that_fails(store)


def test_an_error_caught_inside_the_block_keeps_the_blocks_other_writes(mem_store):
    """Nested blocks join the outer one: catching a refusal inside does not undo anything."""
    with mem_store.transaction():
        mem_store.put("kept", 1)
        with contextlib.suppress(CorruptStoreError), mem_store.transaction():
            mem_store.put("inner", 2)
            raise CorruptStoreError("synthetic_ns", "_", "synthetic refusal")
    assert mem_store.get("kept") == 1
    assert mem_store.get("inner") == 2


def _uncaught_after_three_namespaces(store: Store) -> None:
    with store.transaction():
        store.put("first", 1)
        store.put("second", {"k": 2}, key="owner")
        store.delete("third")
        raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")


def test_an_uncaught_store_error_undoes_puts_and_deletes_in_every_namespace(mem_store):
    mem_store.put("third", 3)
    with pytest.raises(StoreUnavailableError, match="database is locked"):
        _uncaught_after_three_namespaces(mem_store)
    assert mem_store.get("first") is None
    assert mem_store.get("second", key="owner") is None
    assert mem_store.get("third") == 3
