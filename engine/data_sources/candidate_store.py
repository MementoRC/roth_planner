"""Per-source candidate accumulation for the Setup / Command Center.

No streamlit. Saved in the document store, namespace ``candidate_store``.

Every input field can receive competing candidate values from several data
sources (a manual entry, a parsed PDF, a live FinExtract sync, ...). The
``CandidateStore`` keeps the *latest* candidate seen per (field, source) pair
so the resolver can later arbitrate between them without losing what each
source most recently reported.

Candidate values are almost always JSON-native scalars, but the ``grants``
field's candidates are ``list[StockGrant]`` — kept as real ``StockGrant``
instances (not dicts) because ``resolver._resolve_grants`` / ``resolve()``
compare and re-emit them as live objects (``.spread()``, ``.key()``, ...).
``Candidate.to_json``/``from_json`` therefore special-case ``StockGrant``
lists so ``CandidateStore.save`` never chokes on a non-JSON-serializable
dataclass (see the "Sync everything" crash this was fixed for).
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.store import CorruptStoreError, get_store
from engine.store.namespaces import CANDIDATE_STORE
from models.grants import StockGrant
from models.legacy_keys import migrate_legacy_keys
from models.sourced import Provenance

logger = logging.getLogger(__name__)

_GRANTS_TYPE_TAG = "__stock_grants__"


def _value_to_json(value: Any) -> Any:
    """Serialize a candidate value, special-casing ``list[StockGrant]``."""
    if isinstance(value, list) and value and all(isinstance(v, StockGrant) for v in value):
        return {_GRANTS_TYPE_TAG: True, "items": [dataclasses.asdict(v) for v in value]}
    return value


def _value_from_json(value: Any) -> Any:
    """Inverse of ``_value_to_json``: reconstitute ``StockGrant`` instances."""
    if isinstance(value, dict) and value.get(_GRANTS_TYPE_TAG):
        return [StockGrant(**item) for item in value["items"]]
    return value


@dataclass
class Candidate:
    """A single source's most recent reported value for a field."""

    value: Any
    prov: Provenance

    def to_json(self) -> dict:
        return {"value": _value_to_json(self.value), "prov": self.prov.to_json()}

    @classmethod
    def from_json(cls, d: dict) -> Candidate:
        return cls(value=_value_from_json(d["value"]), prov=Provenance.from_json(d["prov"]))


class CorruptCandidateStoreError(Exception):
    """Raised by :meth:`CandidateStore.save` when the saved store cannot be
    read or rebuilt.

    Mirrors ``CorruptCommittedCacheError`` (engine/data_sources/committed.py,
    audit-0809 #11 / PR #442) for exactly the same reason: no saved store
    means "no candidates have ever been persisted yet" — safe to write
    fresh. A saved store that fails to parse means real candidate data
    (every source's latest reported value for every field — a manual entry,
    a parsed PDF, a live FinExtract sync, ...) is saved in an
    unreadable state, and that row is the only copy.
    ``CandidateStore.load`` already tolerates this by returning an empty
    store rather than raising (callers depend on that resilience) — but
    if ``save`` then wrote that empty store back over it, the corruption
    would become permanent and silent (audit-0823). Callers must catch this
    separately and leave it untouched (see app.py's store.save call site).
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"candidate store at {path!r} is corrupt: {cause!r}")


# What from_json raises on a bad shape (UnicodeDecodeError never reaches here).
_SHAPE_ERRORS = (KeyError, ValueError, TypeError)


class CandidateStore:
    """Latest-per-source candidate values, keyed by field."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Candidate]] = {}
        # Set True by ``load`` when the saved row EXISTED but failed to
        # parse, so an empty-because-corrupt store is distinguishable from
        # an empty-because-nothing-was-ever-saved store (an absent row
        # leaves this False). ``load`` must not raise (callers depend on
        # that), so this attribute is the recoverable signal in its place —
        # see ``load``'s docstring.
        self.load_corrupt: bool = False

    def record_candidate(self, field_key: str, value: Any, prov: Provenance) -> None:
        """Upsert the latest candidate for ``field_key`` from ``prov.source``."""
        bucket = self._data.setdefault(field_key, {})
        bucket[str(prov.source)] = Candidate(value=value, prov=prov)

    def candidates_for(self, field_key: str) -> list[Candidate]:
        return list(self._data.get(field_key, {}).values())

    def has_candidates(self, field_key: str) -> bool:
        return bool(self._data.get(field_key))

    def discard(self, field_key: str) -> bool:
        """Drop every source's candidate for ``field_key``; True if any existed."""
        return self._data.pop(field_key, None) is not None

    def discard_source(self, field_key: str, source: object) -> bool:
        """Drop ``source``'s candidate for ``field_key``; True if one existed.

        Other sources' candidates for the field are kept.
        """
        bucket = self._data.get(field_key)
        if not bucket or bucket.pop(str(source), None) is None:
            return False
        if not bucket:
            del self._data[field_key]
        return True

    def field_keys(self) -> list[str]:
        return list(self._data.keys())

    def to_json(self) -> dict:
        return {
            field_key: {source: c.to_json() for source, c in bucket.items()}
            for field_key, bucket in self._data.items()
        }

    @classmethod
    def from_json(cls, d: dict) -> CandidateStore:
        """Rebuild a store; raises ValueError/TypeError/KeyError on a bad shape."""
        if not isinstance(d, dict):
            raise ValueError(f"candidate store must be a JSON object, got {type(d).__name__}")
        store = cls()
        # A store saved before the stock_* rename keys the price field by its old name.
        for field_key, bucket in migrate_legacy_keys(d).items():
            if not isinstance(bucket, dict):
                raise ValueError(
                    f"candidates for {field_key!r} must be a JSON object, "
                    f"got {type(bucket).__name__}"
                )
            store._data[field_key] = {
                source: Candidate.from_json(c) for source, c in bucket.items()
            }
        return store

    def save(self) -> None:
        """Save this store.

        Refuses, with :class:`CorruptCandidateStoreError`, to replace a saved
        store that fails the parse :meth:`load` runs (JSON *and* shape). The
        empty store ``load`` returned for it must never clobber the user's
        candidates (audit-0823). An unknown Source or stale StockGrant fields
        count as unreadable too (audit-1008 F-44). The check and the write are
        one transaction.
        """
        store = get_store()
        with store.transaction():
            try:
                saved = store.get(CANDIDATE_STORE)
                if saved is not None:
                    self.from_json(saved)
            except (CorruptStoreError, *_SHAPE_ERRORS) as exc:
                raise CorruptCandidateStoreError(CANDIDATE_STORE, exc) from exc
            store.put(CANDIDATE_STORE, self.to_json())

    @classmethod
    def load(cls) -> CandidateStore:
        """The saved store; absent or unreadable yields an EMPTY store.

        An unreadable row never raises, because the Setup / Command Center
        depend on that. It sets ``load_corrupt`` on the returned store instead,
        and :meth:`save` refuses to replace it. An absent row is a normal first
        run (``load_corrupt`` stays False). Storage that cannot be opened at
        all still raises ``StoreUnavailableError``.
        """
        try:
            saved = get_store().get(CANDIDATE_STORE)
            return cls() if saved is None else cls.from_json(saved)
        except (CorruptStoreError, *_SHAPE_ERRORS) as exc:
            logger.warning("saved candidate store is unreadable (%s); using an empty one", exc)
            empty = cls()
            empty.load_corrupt = True
            return empty
