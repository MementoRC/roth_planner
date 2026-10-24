"""SQLite backend for :class:`engine.store.core.Store` (a file or ``:memory:``).

Imports ``sqlite3`` at module level on purpose: :func:`engine.store.get_store`
imports this module lazily, so a Pyodide build without ``sqlite3`` falls back
to :class:`engine.store.backend.DictBackend`.

File safety mirrors ``engine.secure_io``: the database is created with mode
0600 and never opened through a symlink; the WAL side files are tightened to
0600 too. ``journal_mode=WAL`` + ``synchronous=FULL`` replace the old
fsync-and-replace dance, and ``busy_timeout`` makes a second writer wait.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from engine.store.backend import Row
from engine.store.errors import StoreUnavailableError
from engine.store.schema import apply_schema

MEMORY = ":memory:"
_SIDE_SUFFIXES = ("-wal", "-shm")
_T = TypeVar("_T")


class SqliteBackend:
    """One autocommit connection; :meth:`begin` opens ``BEGIN IMMEDIATE``."""

    def __init__(self, location: Path | str) -> None:
        self.location = str(location)
        self._is_file = self.location != MEMORY
        conn: sqlite3.Connection | None = None
        try:
            if self._is_file:
                _prepare_db_file(Path(self.location))
            conn = sqlite3.connect(self.location, isolation_level=None, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout = 5000")
            if self._is_file:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = FULL")
            apply_schema(conn)
        except (sqlite3.Error, OSError) as exc:
            if conn is not None:
                conn.close()
            raise StoreUnavailableError(self.location, str(exc)) from exc
        self._conn = conn
        self._tighten_side_files()

    def _run(self, fn: Callable[[], _T]) -> _T:
        try:
            return fn()
        except sqlite3.Error as exc:
            raise StoreUnavailableError(self.location, str(exc)) from exc

    def begin(self) -> None:
        self._run(lambda: self._conn.execute("BEGIN IMMEDIATE"))

    def commit(self) -> None:
        self._run(lambda: self._conn.execute("COMMIT"))
        self._tighten_side_files()

    def rollback(self) -> None:
        self._run(lambda: self._conn.execute("ROLLBACK"))

    def get(self, namespace: str, key: str) -> Row | None:
        found = self._run(
            lambda: self._conn.execute(
                "SELECT payload, schema_version, updated_at FROM documents"
                " WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        )
        return None if found is None else Row(found[0], found[1], found[2])

    def put(self, namespace: str, key: str, row: Row) -> None:
        self._run(
            lambda: self._conn.execute(
                "INSERT INTO documents (namespace, key, payload, schema_version, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (namespace, key) DO UPDATE SET payload = excluded.payload,"
                " schema_version = excluded.schema_version, updated_at = excluded.updated_at",
                (namespace, key, row.payload, row.schema_version, row.updated_at),
            )
        )

    def delete(self, namespace: str, key: str) -> None:
        self._run(
            lambda: self._conn.execute(
                "DELETE FROM documents WHERE namespace = ? AND key = ?", (namespace, key)
            )
        )

    def keys(self, namespace: str) -> list[str]:
        rows = self._run(
            lambda: self._conn.execute(
                "SELECT key FROM documents WHERE namespace = ? ORDER BY key", (namespace,)
            ).fetchall()
        )
        return [row[0] for row in rows]

    def close(self) -> None:
        self._conn.close()

    def _tighten_side_files(self) -> None:
        if not self._is_file:
            return
        base = Path(self.location)
        for suffix in _SIDE_SUFFIXES:
            side = base.with_name(base.name + suffix)
            if side.exists() and not side.is_symlink():
                os.chmod(side, 0o600)


def _prepare_db_file(path: Path) -> None:
    """Create *path* with mode 0600, never through a symlink, before sqlite opens it."""
    if path.is_symlink():
        raise StoreUnavailableError(str(path), "refusing to open a database through a symlink")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
