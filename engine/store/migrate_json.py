"""One-time import of the legacy JSON dotfiles into the database (spec §4).

``engine.store.get_store()`` runs this once per process, the first time it opens
the database file, while holding its lock. This module never calls
``get_store()`` itself. Only namespaces registered in
:mod:`engine.store.legacy_files` are considered.

The first time a namespace is seen, it gets a ``_meta`` marker
``migrated:<namespace>``, whether or not its file exists. A marked namespace is
never imported again, so a JSON file that reappears later is ignored.

The whole run is one transaction. If any file is unreadable, the run rolls
back and moves nothing, and its namespaces are served read-only from their
JSON files until the next start. After a successful run, the imported files
are moved to ``.json_backup/<YYYY-MM-DD-HHMMSS>/`` (mode 0700).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from engine.store.backend import Row
from engine.store.core import META_NAMESPACE, Store
from engine.store.json_readonly import JsonReadOnlyBackend
from engine.store.legacy_files import LegacyFile, registered

BACKUP_DIR_NAME = ".json_backup"
_LEGACY_SCHEMA_VERSION = 1
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MigrationResult:
    """What one run did; ``views.store_notice`` turns it into the banner."""

    imported: tuple[str, ...] = ()  # legacy file names imported by this run
    backup_dir: Path | None = None
    unmoved: tuple[str, ...] = ()  # imported, but still beside the database
    failed_file: str | None = None
    error: str | None = None
    read_only: tuple[str, ...] = ()  # namespaces served from JSON for this process


class _ImportAbortedError(Exception):
    def __init__(self, filename: str, reason: str) -> None:
        super().__init__(f"{filename}: {reason}")
        self.filename = filename
        self.reason = reason


def marker_key(namespace: str) -> str:
    return f"migrated:{namespace}"


def migrate_json(store: Store, legacy_dir: Path) -> MigrationResult:
    """Import every registered, unmarked namespace's file from *legacy_dir* into *store*."""
    now = datetime.now().astimezone()
    pending: list[LegacyFile] = []
    imported: list[Path] = []
    try:
        with store.transaction():
            # Inside BEGIN IMMEDIATE, so a second process waits here and then sees the markers.
            pending = [
                entry
                for entry in registered()
                if not store.has(META_NAMESPACE, key=marker_key(entry.namespace))
            ]
            for entry in pending:
                path = legacy_dir / entry.filename
                found = path.exists() or path.is_symlink()
                if found:
                    _import_file(store, entry, path, now)
                    imported.append(path)
                store.put(
                    META_NAMESPACE,
                    {"at": now.isoformat(timespec="seconds"), "imported": found},
                    key=marker_key(entry.namespace),
                )
    except _ImportAbortedError as exc:
        return MigrationResult(
            failed_file=exc.filename,
            error=exc.reason,
            read_only=tuple(entry.namespace for entry in pending),
        )
    if not imported:
        return MigrationResult()
    backup_dir, unmoved = _move_to_backup(legacy_dir, imported, now)
    return MigrationResult(
        imported=tuple(path.name for path in imported), backup_dir=backup_dir, unmoved=unmoved
    )


def _import_file(store: Store, entry: LegacyFile, path: Path, now: datetime) -> None:
    try:
        rows = entry.reader(path)
        payloads = {str(key): json.dumps(value) for key, value in rows.items()}
    except (OSError, ValueError) as exc:  # unreadable, symlinked, invalid JSON, wrong shape
        raise _ImportAbortedError(entry.filename, f"{type(exc).__name__}: {exc}") from exc
    updated_at = now.isoformat(timespec="seconds")
    for key, payload in payloads.items():
        # Legacy content is schema v1 by definition; a newer namespace upgrades it on read.
        store.backend.put(entry.namespace, key, Row(payload, _LEGACY_SCHEMA_VERSION, updated_at))


def _move_to_backup(
    legacy_dir: Path, paths: list[Path], now: datetime
) -> tuple[Path | None, tuple[str, ...]]:
    """Move *paths* into a fresh dated backup folder; report any that could not move."""
    root = legacy_dir / BACKUP_DIR_NAME
    target = root / now.strftime("%Y-%m-%d-%H%M%S")
    try:
        root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
        target.mkdir(mode=0o700, exist_ok=True)
        os.chmod(target, 0o700)
    except OSError as exc:
        _log.warning("could not create the JSON backup folder %s: %s", target, exc)
        return None, tuple(path.name for path in paths)
    unmoved: list[str] = []
    for path in paths:
        try:
            os.replace(path, target / path.name)
        except OSError as exc:
            _log.warning("could not move %s to %s: %s", path.name, target, exc)
            unmoved.append(path.name)
    return target, tuple(unmoved)


def read_only_backend(result: MigrationResult, legacy_dir: Path) -> JsonReadOnlyBackend:
    """The backend that serves a failed run's namespaces from their JSON files."""
    entries = [entry for entry in registered() if entry.namespace in result.read_only]
    return JsonReadOnlyBackend(legacy_dir, entries, reason=f"importing {result.failed_file} failed")
