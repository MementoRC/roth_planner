"""Single persistence seam for the app's stores.

``get_store()`` picks the backend on every call (cheap):

1. demo mode -> a null store: reads absent, writes dropped (audit-1018 D2).
   Checked first so a store installed by a test sees what the app would:
   nothing in demo mode, the untouched store after leaving it;
2. a store installed with :func:`set_store` (tests);
3. Pyodide/stlite -> one in-memory store per session (SQLite if ``sqlite3``
   imports, else a dict), lost on refresh like today's in-memory files;
4. otherwise -> the ``.roth_planner.db`` file (``ROTH_PLANNER_DB`` overrides),
   opened once per process; the first open imports the legacy JSON files
   (see :mod:`engine.store.migrate_json`).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from engine.data_bridge_browser import is_pyodide
from engine.demo_mode import is_demo_mode
from engine.store.backend import Backend, DictBackend, NullBackend, RoutedBackend
from engine.store.core import META_NAMESPACE, SINGLE_KEY, Store, register_namespace
from engine.store.errors import CorruptStoreError, StoreError, StoreUnavailableError

if TYPE_CHECKING:
    from engine.store.migrate_json import MigrationResult

__all__ = [
    "META_NAMESPACE",
    "SINGLE_KEY",
    "CorruptStoreError",
    "Store",
    "StoreError",
    "StoreUnavailableError",
    "db_path",
    "get_store",
    "legacy_dir",
    "migration_notice",
    "register_namespace",
    "set_store",
]

DB_ENV_VAR = "ROTH_PLANNER_DB"
_DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / ".roth_planner.db"
# The legacy dotfiles always lived at the repo root, wherever ROTH_PLANNER_DB points.
_LEGACY_DIR = _DEFAULT_DB_PATH.parent

_lock = threading.Lock()
_override: Store | None = None
_file_store: Store | None = None
_session_store: Store | None = None
_migration_result: MigrationResult | None = None
_null_store = Store(NullBackend())


def db_path() -> Path:
    """Location of the local database file."""
    configured = os.environ.get(DB_ENV_VAR)
    return Path(configured) if configured else _DEFAULT_DB_PATH


def legacy_dir() -> Path:
    """Folder holding the legacy JSON dotfiles the first open imports."""
    return _LEGACY_DIR


def set_store(store: Store | None) -> None:
    """Install *store* for every ``get_store()`` call (tests); ``None`` restores selection."""
    global _override
    _override = store


def get_store() -> Store:
    """The Store for the current environment; see the module docstring."""
    if is_demo_mode():
        return _null_store
    if _override is not None:
        return _override
    if is_pyodide():
        return _get_session_store()
    return _get_file_store()


def _in_memory_backend() -> Backend:
    try:
        from engine.store.sqlite_backend import MEMORY, SqliteBackend
    except ImportError:
        return DictBackend()
    return SqliteBackend(MEMORY)


def _get_session_store() -> Store:
    global _session_store
    with _lock:
        if _session_store is None:
            _session_store = Store(_in_memory_backend())
        return _session_store


def migration_notice() -> MigrationResult | None:
    """This process's JSON import result; ``None`` until the file store has been opened."""
    return _migration_result


def _get_file_store() -> Store:
    global _file_store, _migration_result
    with _lock:
        if _file_store is None:
            _file_store, _migration_result = _open_file_store(db_path())
        return _file_store


def _open_file_store(path: Path) -> tuple[Store, MigrationResult]:
    """Open the database and run the one-time JSON import (caller holds ``_lock``)."""
    import engine.store.namespaces  # noqa: F401 -- complete the registry before importing
    from engine.store.migrate_json import migrate_json, read_only_backend
    from engine.store.sqlite_backend import SqliteBackend, prepare_db_file

    created = prepare_db_file(path)  # atomic: never claim a database another process made
    store: Store | None = None
    try:
        store = Store(SqliteBackend(path))
        result = migrate_json(store, legacy_dir())
    except BaseException:
        if store is not None:
            store.backend.close()
        if created:
            _remove_db_files(path)
        raise
    if not result.read_only:
        return store, result
    json_backend = read_only_backend(result, legacy_dir())
    if created:
        # Nothing was imported, so leave no database behind (spec §4); the next start retries.
        store.backend.close()
        _remove_db_files(path)
        return Store(json_backend), result
    return Store(
        RoutedBackend(store.backend, dict.fromkeys(result.read_only, json_backend))
    ), result


def _remove_db_files(path: Path) -> None:
    for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        candidate.unlink(missing_ok=True)


def _reset_cached_stores() -> None:
    """Close and forget the cached stores and the import result (tests only)."""
    global _file_store, _session_store, _migration_result
    with _lock:
        for store in (_file_store, _session_store):
            if store is not None:
                store.backend.close()
        _file_store = None
        _session_store = None
        _migration_result = None
