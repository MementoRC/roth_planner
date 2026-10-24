"""The document Store: JSON values by ``(namespace, key)`` over a backend.

The Store owns what every backend must agree on: JSON encoding, per-namespace
schema versions and upgraders, the corrupt-row rules and transactions.

Transactions: ``with store.transaction():`` begins on the outermost entry and
commits when that block exits, or rolls back if it raises. Nested blocks join
the outer one (no savepoints), so an exception caught inside an outer block
does not undo the inner block's writes. A thread holds the Store's lock for the
whole transaction, so concurrent Streamlit sessions serialise here.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from engine.store.backend import Backend, Row
from engine.store.errors import CorruptStoreError, StoreError

META_NAMESPACE = "_meta"
SINGLE_KEY = "_"

Upgrader = Callable[[Any], Any]


@dataclass(frozen=True)
class NamespaceSpec:
    """Current schema version of a namespace and how to reach it from older rows."""

    version: int = 1
    upgraders: dict[int, Upgrader] = field(default_factory=dict)


_NAMESPACES: dict[str, NamespaceSpec] = {}


def register_namespace(
    name: str, *, version: int = 1, upgraders: dict[int, Upgrader] | None = None
) -> None:
    """Declare *name*'s schema *version*; ``upgraders[v]`` turns a v payload into v+1."""
    if name == META_NAMESPACE:
        raise ValueError(f"{META_NAMESPACE!r} is reserved for store bookkeeping")
    if version < 1:
        raise ValueError(f"{name}: version must be >= 1, got {version}")
    steps = dict(upgraders or {})
    missing = [v for v in range(1, version) if v not in steps]
    if missing:
        raise ValueError(f"{name}: no upgrader from version(s) {missing}")
    spec = NamespaceSpec(version, steps)
    existing = _NAMESPACES.get(name)
    if existing is not None and existing != spec:
        raise ValueError(f"{name}: already registered with a different version or upgraders")
    _NAMESPACES[name] = spec


def namespace_spec(name: str) -> NamespaceSpec:
    return _NAMESPACES.get(name, NamespaceSpec())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read(namespace: str, key: str, row: Row) -> Any:
    """Decode *row*, raising :class:`CorruptStoreError` if this app cannot read it."""
    version = namespace_spec(namespace).version
    if row.schema_version > version:
        raise CorruptStoreError(
            namespace,
            key,
            f"schema_version {row.schema_version} is newer than this app ({version})",
        )
    if row.schema_version < 1:
        raise CorruptStoreError(namespace, key, f"invalid schema_version {row.schema_version}")
    try:
        return json.loads(row.payload)
    except ValueError as exc:
        raise CorruptStoreError(namespace, key, str(exc)) from exc


def _upgrade(namespace: str, key: str, row: Row, value: Any) -> Any:
    spec = namespace_spec(namespace)
    for version in range(row.schema_version, spec.version):
        try:
            value = spec.upgraders[version](value)
        except Exception as exc:
            raise CorruptStoreError(
                namespace, key, f"upgrade v{version}→v{version + 1} failed: {exc}"
            ) from exc
    return value


class Store:
    """JSON documents by ``(namespace, key)``; see the module docstring."""

    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self._lock = threading.RLock()
        self._depth = 0

    @contextmanager
    def transaction(self) -> Iterator[Store]:
        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self.backend.begin()
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outermost:
                    self.backend.rollback()
                raise
            self._depth -= 1
            if outermost:
                try:
                    self.backend.commit()
                except BaseException:
                    # Don't leave the connection inside an open transaction.
                    with suppress(StoreError):
                        self.backend.rollback()
                    raise

    def get(self, namespace: str, *, key: str = SINGLE_KEY) -> Any:
        """The stored value, upgraded to the namespace's version; ``None`` if absent."""
        with self._lock:
            row = self.backend.get(namespace, key)
            if row is None:
                return None
            value = _read(namespace, key, row)
            if row.schema_version == namespace_spec(namespace).version:
                return value
            return self._upgrade_in_place(namespace, key)

    def _upgrade_in_place(self, namespace: str, key: str) -> Any:
        """Read, upgrade and write back one row in a single transaction."""
        version = namespace_spec(namespace).version
        with self.transaction():
            row = self.backend.get(namespace, key)  # re-read: a writer may have got in first
            if row is None:
                return None
            value = _read(namespace, key, row)
            if row.schema_version == version:
                return value
            value = _upgrade(namespace, key, row, value)
            self.backend.put(namespace, key, Row(json.dumps(value), version, _now()))
            return value

    def has(self, namespace: str, *, key: str = SINGLE_KEY) -> bool:
        with self._lock:
            return self.backend.get(namespace, key) is not None

    def keys(self, namespace: str) -> list[str]:
        with self._lock:
            return self.backend.keys(namespace)

    def put(
        self, namespace: str, value: Any, *, key: str = SINGLE_KEY, replace_corrupt: bool = False
    ) -> None:
        """Store *value*; refuse to overwrite an unreadable row unless *replace_corrupt*."""
        payload = json.dumps(value)  # TypeError here, before anything is written
        with self.transaction():
            if not replace_corrupt:
                existing = self.backend.get(namespace, key)
                if existing is not None:
                    _read(namespace, key, existing)
            row = Row(payload, namespace_spec(namespace).version, _now())
            self.backend.put(namespace, key, row)

    def delete(self, namespace: str, *, key: str = SINGLE_KEY) -> None:
        with self.transaction():
            self.backend.delete(namespace, key)
