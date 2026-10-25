"""Registry of the legacy JSON dotfiles, one per store namespace.

Each PR that moves a store onto the database registers that namespace here,
and only registered namespaces are migrated (spec §4). Only this module,
``engine/store/namespaces.py`` and ``migrate_json`` know legacy file names.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.secure_io import read_pii_json
from engine.store.core import SINGLE_KEY, register_namespace

LegacyReader = Callable[[Path], dict[str, Any]]


@dataclass(frozen=True)
class LegacyFile:
    """A repo-root JSON file and how to turn it into ``{key: value}`` rows."""

    namespace: str
    filename: str
    reader: LegacyReader


_REGISTRY: dict[str, LegacyFile] = {}


def register_legacy_file(entry: LegacyFile) -> None:
    existing = _REGISTRY.get(entry.namespace)
    if existing is not None and existing != entry:
        raise ValueError(f"legacy file for namespace {entry.namespace!r} is already registered")
    _REGISTRY[entry.namespace] = entry


def registered() -> tuple[LegacyFile, ...]:
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))


def single_document_reader(path: Path) -> dict[str, Any]:
    """Reader for stores that are one JSON document: the whole file under the default key."""
    return {SINGLE_KEY: read_pii_json(path)}


def per_entry_reader(path: Path) -> dict[str, Any]:
    """Reader for a JSON object stored one row per top-level entry."""
    raw = read_pii_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object, got {type(raw).__name__}")
    return {str(key): value for key, value in raw.items()}


def declare_store(
    namespace: str, filename: str, reader: LegacyReader = single_document_reader
) -> str:
    """Register *namespace* (schema v1) and its legacy JSON *filename*; returns *namespace*."""
    register_namespace(namespace)
    register_legacy_file(LegacyFile(namespace, filename, reader))
    return namespace
