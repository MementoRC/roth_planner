"""Table layout of the document store; ``PRAGMA user_version`` tracks it."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

_DOCUMENTS_DDL = (
    "CREATE TABLE IF NOT EXISTS documents ("
    " namespace TEXT NOT NULL,"
    " key TEXT NOT NULL,"
    " payload TEXT NOT NULL,"
    " schema_version INTEGER NOT NULL,"
    " updated_at TEXT NOT NULL,"
    " PRIMARY KEY (namespace, key))"
)


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create the layout on a new database; refuse a layout newer than this app.

    Raises ``sqlite3.DatabaseError`` for a newer layout so the backend reports it
    as :class:`engine.store.errors.StoreUnavailableError` like any other open failure.
    """
    (current,) = conn.execute("PRAGMA user_version").fetchone()
    if current > SCHEMA_VERSION:
        raise sqlite3.DatabaseError(
            f"database layout v{current} is newer than this app (v{SCHEMA_VERSION})"
        )
    if current == SCHEMA_VERSION:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(_DOCUMENTS_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
