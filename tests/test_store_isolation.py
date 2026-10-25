"""Every test runs on its own empty in-memory store (replaces audit-0809 F18's path tests).

The repo-root database and legacy-folder redirects are pinned in
tests/test_store_selection.py.
"""

from __future__ import annotations

from engine.store import get_store
from engine.store.legacy_files import registered
from engine.store.sqlite_backend import MEMORY, SqliteBackend


def test_get_store_is_this_tests_in_memory_store(store):
    assert get_store() is store
    assert isinstance(store.backend, SqliteBackend)
    assert store.backend.location == MEMORY


def test_every_registered_namespace_starts_empty(store):
    assert {e.namespace: store.keys(e.namespace) for e in registered()} == {
        e.namespace: [] for e in registered()
    }
