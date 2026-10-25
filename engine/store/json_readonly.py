"""Read-only view of the legacy JSON files, for namespaces whose import failed.

When ``migrate_json`` cannot import a file, nothing is imported, and that
run's namespaces are served from their JSON files by this backend for the rest
of the process (spec §1, §4). Reads go through the same registered reader the
import uses, so they see exactly what the import would have stored. Every
write raises :class:`StoreUnavailableError`, which the app shows as a banner.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from engine.store.backend import Row
from engine.store.errors import CorruptStoreError, StoreUnavailableError
from engine.store.legacy_files import LegacyFile


class JsonReadOnlyBackend:
    """Serves each namespace from its legacy file in *legacy_dir*; writes are refused."""

    def __init__(self, legacy_dir: Path, entries: Iterable[LegacyFile], *, reason: str) -> None:
        self._dir = legacy_dir
        self._entries = {entry.namespace: entry for entry in entries}
        self._reason = reason

    def _location(self, namespace: str) -> str:
        entry = self._entries.get(namespace)
        return str(self._dir / entry.filename) if entry is not None else str(self._dir)

    def _rows(self, namespace: str, key: str) -> dict[str, Any]:
        entry = self._entries.get(namespace)
        if entry is None:
            return {}
        try:
            return entry.reader(self._dir / entry.filename)
        except FileNotFoundError:
            return {}
        except ValueError as exc:  # json.JSONDecodeError included
            raise CorruptStoreError(namespace, key, str(exc)) from exc
        except OSError as exc:  # symlink refused, permissions
            raise StoreUnavailableError(self._location(namespace), str(exc)) from exc

    def begin(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def get(self, namespace: str, key: str) -> Row | None:
        rows = self._rows(namespace, key)
        if key not in rows:
            return None
        return Row(json.dumps(rows[key]), 1, "")

    def put(self, namespace: str, key: str, row: Row) -> None:
        raise StoreUnavailableError(
            self._location(namespace), f"saving is disabled: {self._reason}"
        )

    def delete(self, namespace: str, key: str) -> None:
        raise StoreUnavailableError(
            self._location(namespace), f"saving is disabled: {self._reason}"
        )

    def keys(self, namespace: str) -> list[str]:
        return sorted(self._rows(namespace, "*"))

    def close(self) -> None:
        pass
