"""Storage backends behind :class:`engine.store.core.Store`.

A backend keeps opaque :class:`Row` values by ``(namespace, key)`` and offers
``begin``/``commit``/``rollback``. JSON decoding, schema versions and the
corrupt-row rules live in the Store, so every backend behaves the same.
``SqliteBackend`` lives in :mod:`engine.store.sqlite_backend` because it imports
``sqlite3``, which a Pyodide build may lack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Row:
    """One stored document: JSON text plus its namespace schema version."""

    payload: str
    schema_version: int
    updated_at: str


class Backend(Protocol):
    def begin(self) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def get(self, namespace: str, key: str) -> Row | None: ...
    def put(self, namespace: str, key: str, row: Row) -> None: ...
    def delete(self, namespace: str, key: str) -> None: ...
    def keys(self, namespace: str) -> list[str]: ...
    def close(self) -> None: ...


class DictBackend:
    """In-memory backend; a transaction snapshots the rows and restores them on rollback."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], Row] = {}
        self._snapshot: dict[tuple[str, str], Row] | None = None

    def begin(self) -> None:
        self._snapshot = dict(self._rows)

    def commit(self) -> None:
        self._snapshot = None

    def rollback(self) -> None:
        if self._snapshot is not None:
            self._rows = self._snapshot
            self._snapshot = None

    def get(self, namespace: str, key: str) -> Row | None:
        return self._rows.get((namespace, key))

    def put(self, namespace: str, key: str, row: Row) -> None:
        self._rows[(namespace, key)] = row

    def delete(self, namespace: str, key: str) -> None:
        self._rows.pop((namespace, key), None)

    def keys(self, namespace: str) -> list[str]:
        return sorted(key for ns, key in self._rows if ns == namespace)

    def close(self) -> None:
        self._rows = {}


class NullBackend:
    """Demo mode: every read is absent and every write is dropped."""

    def begin(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def get(self, namespace: str, key: str) -> Row | None:
        return None

    def put(self, namespace: str, key: str, row: Row) -> None:
        pass

    def delete(self, namespace: str, key: str) -> None:
        pass

    def keys(self, namespace: str) -> list[str]:
        return []

    def close(self) -> None:
        pass
