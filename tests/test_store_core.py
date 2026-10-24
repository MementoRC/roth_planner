"""Store semantics, identical on the SQLite and dict backends."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from engine.store import core
from engine.store.backend import DictBackend, NullBackend, Row
from engine.store.core import META_NAMESPACE, SINGLE_KEY, Store, register_namespace
from engine.store.errors import CorruptStoreError, StoreUnavailableError
from engine.store.sqlite_backend import MEMORY, SqliteBackend


@pytest.fixture(params=["sqlite", "dict"])
def mem_store(request) -> Iterator[Store]:
    backend = SqliteBackend(MEMORY) if request.param == "sqlite" else DictBackend()
    yield Store(backend)
    backend.close()


@pytest.fixture(autouse=True)
def _clean_namespaces(monkeypatch):
    monkeypatch.setattr(core, "_NAMESPACES", {})


def _raw(store: Store, namespace: str, payload: str, version: int = 1, key: str = SINGLE_KEY):
    store.backend.put(namespace, key, Row(payload, version, "2026-01-01T00:00:00+00:00"))


def test_values_round_trip_under_the_default_and_explicit_keys(mem_store):
    mem_store.put("ns", {"a": [1, 2.5, None, "x"]})
    mem_store.put("ns", {"b": 1}, key="owner")
    assert mem_store.get("ns") == {"a": [1, 2.5, None, "x"]}
    assert mem_store.get("ns", key="owner") == {"b": 1}
    assert mem_store.get("missing") is None


def test_has_keys_and_delete(mem_store):
    mem_store.put("ns", 1, key="b")
    mem_store.put("ns", 2, key="a")
    assert mem_store.has("ns", key="a")
    assert mem_store.keys("ns") == ["a", "b"]
    mem_store.delete("ns", key="a")
    assert not mem_store.has("ns", key="a")
    assert mem_store.keys("ns") == ["b"]


def _write_then_fail(store: Store) -> None:
    with store.transaction():
        store.put("a", 2)
        store.put("b", 3)
        raise RuntimeError("boom")


def _nested_write_then_outer_fail(store: Store) -> None:
    with store.transaction():
        with store.transaction():
            store.put("inner", 1)
        raise RuntimeError("outer fails after the inner block finished")


def test_transaction_rolls_back_every_write_on_error(mem_store):
    mem_store.put("a", 1)
    with pytest.raises(RuntimeError):
        _write_then_fail(mem_store)
    assert mem_store.get("a") == 1
    assert mem_store.get("b") is None


def test_nested_transaction_joins_the_outer_one(mem_store):
    with pytest.raises(RuntimeError):
        _nested_write_then_outer_fail(mem_store)
    assert mem_store.get("inner") is None


class _CommitFails(SqliteBackend):
    def commit(self) -> None:
        raise StoreUnavailableError(self.location, "commit failed")


def _put_in_transaction(store: Store) -> None:
    with store.transaction():
        store.put("ns", 1)


def test_failed_commit_rolls_back_and_leaves_the_connection_usable():
    backend = _CommitFails(MEMORY)
    store = Store(backend)
    with pytest.raises(StoreUnavailableError):
        _put_in_transaction(store)
    assert backend.get("ns", SINGLE_KEY) is None
    backend.begin()  # would fail with "within a transaction" if the commit failure left one open
    backend.rollback()
    backend.close()


def test_corrupt_row_raises_with_its_location(mem_store):
    _raw(mem_store, "ns", "{not json", key="k")
    with pytest.raises(CorruptStoreError) as info:
        mem_store.get("ns", key="k")
    assert (info.value.namespace, info.value.key) == ("ns", "k")


def test_put_refuses_to_overwrite_a_corrupt_row_unless_told(mem_store):
    _raw(mem_store, "ns", "{not json")
    with pytest.raises(CorruptStoreError):
        mem_store.put("ns", {"fresh": True})
    mem_store.put("ns", {"fresh": True}, replace_corrupt=True)
    assert mem_store.get("ns") == {"fresh": True}


def test_unserializable_value_writes_nothing(mem_store):
    mem_store.put("ns", {"ok": 1})
    with pytest.raises(TypeError):
        mem_store.put("ns", {"bad": {1, 2}})
    assert mem_store.get("ns") == {"ok": 1}


def test_upgraders_run_on_read_and_write_back(mem_store):
    register_namespace(
        "ns",
        version=3,
        upgraders={1: lambda v: {**v, "two": True}, 2: lambda v: {**v, "three": True}},
    )
    _raw(mem_store, "ns", '{"one": true}', version=1)
    assert mem_store.get("ns") == {"one": True, "two": True, "three": True}
    row = mem_store.backend.get("ns", SINGLE_KEY)
    assert row is not None
    assert row.schema_version == 3


def test_put_stamps_the_namespace_version(mem_store):
    register_namespace("ns", version=2, upgraders={1: lambda v: v})
    mem_store.put("ns", 1)
    row = mem_store.backend.get("ns", SINGLE_KEY)
    assert row is not None
    assert row.schema_version == 2


def test_rows_from_a_newer_app_or_below_version_one_are_corrupt(mem_store):
    _raw(mem_store, "newer", "1", version=5)
    _raw(mem_store, "zero", "1", version=0)
    with pytest.raises(CorruptStoreError, match="newer"):
        mem_store.get("newer")
    with pytest.raises(CorruptStoreError):
        mem_store.get("zero")


def test_register_namespace_validates_its_arguments():
    with pytest.raises(ValueError, match="upgrader"):
        register_namespace("ns", version=3, upgraders={1: lambda v: v})
    with pytest.raises(ValueError, match="reserved"):
        register_namespace(META_NAMESPACE)


def test_null_backend_store_reads_absent_and_drops_writes():
    store = Store(NullBackend())
    with store.transaction():
        store.put("ns", 1)
    assert store.get("ns") is None
    assert store.keys("ns") == []
