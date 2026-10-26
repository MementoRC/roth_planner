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


class CorruptDocumentError(StoreError):
    """A store module refused a saved document it cannot use; the row is untouched.

    Base of the per-module errors (``CorruptCommittedCacheError``,
    ``CorruptCandidateStoreError``, ...): the document's row is unreadable, or
    decodes to a shape the module cannot use, so the module refuses to read it
    or to save over it. A ``StoreError``, so one that escapes a page shows the
    route-level storage banner instead of a traceback. Deliberately NOT a
    ``CorruptStoreError``: engine code catches that around raw ``Store.get``
    calls and must not start swallowing these.
    """


class StoreUnavailableError(StoreError):
    """The database cannot be opened, read or written (locked, corrupt, symlinked, newer)."""

    def __init__(self, location: str, reason: str) -> None:
        super().__init__(f"storage unavailable at {location}: {reason}")
        self.location = location
        self.reason = reason
