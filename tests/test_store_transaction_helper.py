"""views._store_transaction: one store transaction plus a session-key restore.

Plain dicts stand in for ``st.session_state``. Synthetic values only.
"""

from __future__ import annotations

import pytest

from engine.store import StoreUnavailableError
from views._store_transaction import store_transaction


def _fail_after_writes(store, session: dict) -> None:
    with store_transaction(session, "snap", "queue", "added"):
        store.put("a", 1)
        session["snap"]["value"] = 2  # in place, as the YTD apply mutates its snapshot
        session["queue"] = []
        session["added"] = "new"
        raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")


def test_a_failed_block_rolls_back_the_store_and_restores_the_listed_keys(store):
    session = {"snap": {"value": 1}, "queue": ["item"], "other": "x"}
    with pytest.raises(StoreUnavailableError, match="database is locked"):
        _fail_after_writes(store, session)
    assert store.get("a") is None
    assert session == {"snap": {"value": 1}, "queue": ["item"], "other": "x"}


def _set_unlisted_then_fail(session: dict) -> None:
    with store_transaction(session, "listed"):
        session["unlisted"] = 1
        raise RuntimeError("synthetic failure")


def test_unlisted_keys_are_not_restored():
    session: dict = {}
    with pytest.raises(RuntimeError, match="synthetic failure"):
        _set_unlisted_then_fail(session)
    assert session == {"unlisted": 1}


def test_a_successful_block_commits_and_keeps_the_new_session_values(store):
    session = {"snap": 1}
    with store_transaction(session, "snap"):
        store.put("a", 1)
        session["snap"] = 2
    assert store.get("a") == 1
    assert session == {"snap": 2}


def _nested_blocks_inner_fails(store, session: dict) -> None:
    with store_transaction(session, "outer"):
        store.put("a", 1)
        session["outer"] = "changed"
        with store_transaction(session, "inner"):
            session["inner"] = "changed"
            raise RuntimeError("synthetic failure")


def test_nested_blocks_join_and_both_restore(store):
    session = {"outer": "kept", "inner": "kept"}
    with pytest.raises(RuntimeError, match="synthetic failure"):
        _nested_blocks_inner_fails(store, session)
    assert store.get("a") is None
    assert session == {"outer": "kept", "inner": "kept"}
