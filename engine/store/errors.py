"""Errors raised by the document store."""

from __future__ import annotations


class StoreError(Exception):
    """Base class for store errors."""


class CorruptStoreError(StoreError):
    """A stored row cannot be decoded or is newer than this app understands."""

    def __init__(self, namespace: str, key: str, reason: str) -> None:
        super().__init__(f"corrupt store row {namespace}/{key}: {reason}")
        self.namespace = namespace
        self.key = key
        self.reason = reason


class StoreUnavailableError(StoreError):
    """The database cannot be opened, read or written (locked, corrupt, symlinked, newer)."""

    def __init__(self, location: str, reason: str) -> None:
        super().__init__(f"storage unavailable at {location}: {reason}")
        self.location = location
        self.reason = reason
