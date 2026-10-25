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
import threading
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
    """One autocommit connection; :meth:`begin` opens ``BEGIN IMMEDIATE``.

    The connection is shared across threads, so every call holds an RLock and
    a transaction keeps it from :meth:`begin` until :meth:`commit`/:meth:`rollback`.
    """

    def __init__(self, location: Path | str) -> None:
        self.location = str(location)
        self._is_file = self.location != MEMORY
        conn: sqlite3.Connection | None = None
        try:
            if self._is_file:
                prepare_db_file(Path(self.location))
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
        self._lock = threading.RLock()
        self._tx_owner: int | None = None
        try:
            self._tighten_side_files()
        except StoreUnavailableError:
            conn.close()
            raise

    def _run(self, fn: Callable[[], _T]) -> _T:
        try:
            return fn()
        except sqlite3.Error as exc:
            raise StoreUnavailableError(self.location, str(exc)) from exc

    def begin(self) -> None:
        # The lock taken here is held across the whole transaction so other
        # threads sharing this connection cannot interleave with it; commit()
        # or rollback() on the owning thread releases it.
        self._lock.acquire()
        try:
            self._run(lambda: self._conn.execute("BEGIN IMMEDIATE"))
        except BaseException:
            self._lock.release()
            raise
        self._tx_owner = threading.get_ident()

    def commit(self) -> None:
        with self._lock:
            if not self._conn.in_transaction:
                # SQLite may have rolled back on its own; still drop begin()'s hold.
                self._release_transaction()
                return
            try:
                self._run(lambda: self._conn.execute("COMMIT"))
                self._tighten_side_files(strict=False)
            finally:
                # A failed COMMIT that leaves the transaction open keeps the
                # hold for the caller's rollback().
                if not self._conn.in_transaction:
                    self._release_transaction()

    def rollback(self) -> None:
        with self._lock:
            if not self._conn.in_transaction:
                self._release_transaction()
                return
            try:
                self._run(lambda: self._conn.execute("ROLLBACK"))
            finally:
                self._release_transaction()

    def _release_transaction(self) -> None:
        """Drop the lock hold taken by begin(), only if this thread made it."""
        if self._tx_owner == threading.get_ident():
            self._tx_owner = None
            self._lock.release()

    def get(self, namespace: str, key: str) -> Row | None:
        with self._lock:
            found = self._run(
                lambda: self._conn.execute(
                    "SELECT payload, schema_version, updated_at FROM documents"
                    " WHERE namespace = ? AND key = ?",
                    (namespace, key),
                ).fetchone()
            )
        return None if found is None else Row(found[0], found[1], found[2])

    def put(self, namespace: str, key: str, row: Row) -> None:
        with self._lock:
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
        with self._lock:
            self._run(
                lambda: self._conn.execute(
                    "DELETE FROM documents WHERE namespace = ? AND key = ?", (namespace, key)
                )
            )

    def keys(self, namespace: str) -> list[str]:
        with self._lock:
            rows = self._run(
                lambda: self._conn.execute(
                    "SELECT key FROM documents WHERE namespace = ? ORDER BY key", (namespace,)
                ).fetchall()
            )
        return [row[0] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _tighten_side_files(self, *, strict: bool = True) -> None:
        """chmod the WAL side files to 0600.

        A vanished file is fine. Any other OSError is fatal while opening
        (``strict``: the store would be left readable) but best effort after a
        commit, where the data is already durable.
        """
        if not self._is_file:
            return
        base = Path(self.location)
        for suffix in _SIDE_SUFFIXES:
            side = base.with_name(base.name + suffix)
            try:
                if not side.is_symlink():
                    os.chmod(side, 0o600)
            except FileNotFoundError:
                continue
            except OSError as exc:
                if strict:
                    raise StoreUnavailableError(self.location, str(exc)) from exc


def prepare_db_file(path: Path) -> bool:
    """Create *path* with mode 0600, never through a symlink, before sqlite opens it.

    Returns whether this call created the file (decided atomically by ``O_EXCL``).
    """
    if path.is_symlink():
        raise StoreUnavailableError(str(path), "refusing to open a database through a symlink")
    try:
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            created = True
        except FileExistsError:
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            created = False
    except OSError as exc:
        raise StoreUnavailableError(str(path), str(exc)) from exc
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return created
