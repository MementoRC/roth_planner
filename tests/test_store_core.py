"""Store semantics, identical on the SQLite and dict backends."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize(("version", "payload"), [(5, "1"), (0, "1"), (-1, "1")])
def test_put_refuses_to_overwrite_unreadable_versions_unless_told(mem_store, version, payload):
    _raw(mem_store, "ns", payload, version=version)
    with pytest.raises(CorruptStoreError):
        mem_store.put("ns", 2)
    row = mem_store.backend.get("ns", SINGLE_KEY)
    assert row is not None
    assert (row.payload, row.schema_version) == (payload, version)
    mem_store.put("ns", 2, replace_corrupt=True)
    assert mem_store.get("ns") == 2


def test_unserializable_value_writes_nothing(mem_store):
    mem_store.put("ns", {"ok": 1})
    with pytest.raises(TypeError):
        mem_store.put("ns", {"bad": {1, 2}})
    assert mem_store.get("ns") == {"ok": 1}


def _rename_a_to_b(value):
    return {"b": value["a"]}


def _derive_c_from_b(value):
    return {**value, "c": value["b"] * 10}


def test_upgraders_run_on_read_and_write_back(mem_store):
    register_namespace("ns", version=3, upgraders={1: _rename_a_to_b, 2: _derive_c_from_b})
    _raw(mem_store, "ns", '{"a": 4}', version=1)
    assert mem_store.get("ns") == {"b": 4, "c": 40}
    row = mem_store.backend.get("ns", SINGLE_KEY)
    assert row is not None
    assert row.schema_version == 3
    assert json.loads(row.payload) == {"b": 4, "c": 40}


class _PutFails(DictBackend):
    armed = False

    def put(self, namespace: str, key: str, row: Row) -> None:
        if self.armed:
            raise StoreUnavailableError("test", "database is locked")
        super().put(namespace, key, row)


def _old_version_store() -> tuple[Store, _PutFails]:
    backend = _PutFails()
    store = Store(backend)
    register_namespace("ns", version=2, upgraders={1: lambda v: v + 1})
    _raw(store, "ns", "1", version=1)
    backend.armed = True
    return store, backend


def test_read_survives_a_failing_upgrade_write_back():
    store, backend = _old_version_store()
    assert store.get("ns") == 2
    row = backend.get("ns", SINGLE_KEY)
    assert row is not None
    assert (row.payload, row.schema_version) == ("1", 1)


def test_failing_upgrade_write_back_inside_an_outer_transaction_propagates():
    store, _ = _old_version_store()
    with pytest.raises(StoreUnavailableError), store.transaction():
        store.get("ns")


class _TracingBackend(DictBackend):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def begin(self) -> None:
        self.calls.append("begin")
        super().begin()

    def commit(self) -> None:
        self.calls.append("commit")
        super().commit()

    def rollback(self) -> None:
        self.calls.append("rollback")
        super().rollback()


def test_upgrade_write_back_happens_inside_one_transaction():
    backend = _TracingBackend()
    store = Store(backend)
    register_namespace("ns", version=2, upgraders={1: lambda v: v + 1})
    _raw(store, "ns", "1", version=1)
    assert store.get("ns") == 2
    assert backend.calls == ["begin", "commit"]


def test_upgrade_does_not_clobber_a_row_that_became_current_meanwhile():
    class _RacingBackend(DictBackend):
        raced = False

        def get(self, namespace: str, key: str) -> Row | None:
            row = super().get(namespace, key)
            if not self.raced:
                self.raced = True  # a concurrent writer lands right after our first read
                super().put(namespace, key, Row("100", 2, "2026-01-02T00:00:00+00:00"))
            return row

    backend = _RacingBackend()
    store = Store(backend)
    register_namespace("ns", version=2, upgraders={1: lambda v: v + 1})
    _raw(store, "ns", "1", version=1)
    assert store.get("ns") == 100
    row = backend.get("ns", SINGLE_KEY)
    assert row is not None
    assert row.payload == "100"


def test_failing_upgrader_is_wrapped_and_writes_nothing(mem_store):
    def boom(value):
        raise KeyError("a")

    register_namespace("ns", version=3, upgraders={1: lambda v: v, 2: boom})
    _raw(mem_store, "ns", '{"x": 1}', version=1)
    with pytest.raises(CorruptStoreError, match=r"upgrade v2→v3 failed") as info:
        mem_store.get("ns")
    assert isinstance(info.value.__cause__, KeyError)
    row = mem_store.backend.get("ns", SINGLE_KEY)
    assert row is not None
    assert (row.payload, row.schema_version) == ('{"x": 1}', 1)


def test_depth_returns_to_zero_after_a_failed_transaction():
    backend = _TracingBackend()
    store = Store(backend)
    with pytest.raises(RuntimeError):
        _write_then_fail(store)
    backend.calls.clear()
    with pytest.raises(RuntimeError):
        _write_then_fail(store)
    assert backend.calls == ["begin", "rollback"]
    assert store.get("a") is None
    with store.transaction():
        store.put("a", 1)
    assert backend.calls[-2:] == ["begin", "commit"]
    assert store.get("a") == 1


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
    with pytest.raises(ValueError, match="version"):
        register_namespace("ns", version=0)


def test_re_registering_is_idempotent_only_when_identical():
    step = _rename_a_to_b
    register_namespace("ns", version=2, upgraders={1: step})
    register_namespace("ns", version=2, upgraders={1: step})
    with pytest.raises(ValueError, match="already registered"):
        register_namespace("ns", version=3, upgraders={1: step, 2: _derive_c_from_b})
    with pytest.raises(ValueError, match="already registered"):
        register_namespace("ns", version=2, upgraders={1: _derive_c_from_b})
    register_namespace("plain")
    register_namespace("plain")


def test_null_backend_store_reads_absent_and_drops_writes():
    store = Store(NullBackend())
    with store.transaction():
        store.put("ns", 1)
    assert store.get("ns") is None
    assert store.keys("ns") == []
