"""Per-source candidate accumulation for the Setup / Command Center.

Pure module: stdlib + models/ only. No streamlit, no other engine imports.

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
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models.grants import StockGrant
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
    """Raised by :meth:`CandidateStore.save` when ``path`` exists but its
    content is not valid JSON (truncated/malformed write, e.g. process
    killed mid-write).

    Mirrors ``CorruptCommittedCacheError`` (engine/data_sources/committed.py,
    audit-0809 #11 / PR #442) for exactly the same reason: a missing file
    means "no candidates have ever been persisted yet" — safe to write
    fresh. A file that exists but fails to parse means real candidate data
    (every source's latest reported value for every field — a manual entry,
    a parsed PDF, a live FinExtract sync, ...) is sitting on disk in an
    unreadable state, and the only copy of it is those corrupt bytes.
    ``CandidateStore.load`` already tolerates this by returning an empty
    store rather than raising (callers depend on that resilience) — but
    if ``save`` then wrote that empty store straight back over the corrupt
    file, the corruption would become permanent and silent (audit-0823
    PS-2-adjacent: observed for real as an "Expecting value: line 1 column
    1" corruption of .candidate_store.json). Callers must catch this
    separately and leave the file untouched (see app.py's store.save call
    site).
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"candidate store at {path!r} is corrupt: {cause!r}")


class CandidateStore:
    """Latest-per-source candidate values, keyed by field."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Candidate]] = {}
        # Set True by ``load`` when the on-disk file EXISTED but failed to
        # parse, so an empty-because-corrupt store is distinguishable from
        # an empty-because-nothing-was-ever-saved store (a missing file
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

    def field_keys(self) -> list[str]:
        return list(self._data.keys())

    def to_json(self) -> dict:
        return {
            field_key: {source: c.to_json() for source, c in bucket.items()}
            for field_key, bucket in self._data.items()
        }

    @classmethod
    def from_json(cls, d: dict) -> CandidateStore:
        store = cls()
        for field_key, bucket in d.items():
            store._data[field_key] = {source: Candidate.from_json(c) for source, c in bucket.items()}
        return store

    def save(self, path: str | Path) -> None:
        """Write this store to ``path`` durably.

        Mirrors ``save_committed`` (engine/data_sources/committed.py,
        audit-0809 #11 / PR #442): writes to a temp file in the same
        directory then ``os.replace()`` onto the target so a crash or full
        disk mid-write cannot truncate an existing good store in place —
        ``os.replace`` is atomic on POSIX (and on Windows via ``MoveFileEx``
        with the replace flag), so readers never observe a partial write.

        Before writing, if ``path`` already exists we read+parse its current
        content once; if that parse fails, we refuse to write and raise
        CorruptCandidateStoreError instead of silently overwriting the only
        copy of the user's candidates with a freshly-loaded empty store
        (audit-0823 — see CorruptCandidateStoreError's docstring). A missing
        target (first run, nothing to protect) or an existing-and-parseable
        one both write normally.
        """
        target = Path(path)
        if target.exists():
            try:
                json.loads(target.read_text())
            except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
                raise CorruptCandidateStoreError(path, exc) from exc
        tmp_path = target.with_name(f"{target.name}.tmp-{os.getpid()}")
        tmp_path.write_text(json.dumps(self.to_json()))
        os.replace(tmp_path, target)

    @classmethod
    def load(cls, path: str | Path) -> CandidateStore:
        """Load from ``path``; a missing or corrupt file yields an EMPTY store.

        Never raises — logs a warning instead so a broken cache file can't
        crash the Setup / Command Center (callers depend on this
        resilience). The missing-vs-corrupt distinction that ``save``
        relies on to refuse a clobber is instead exposed on the returned
        store's ``load_corrupt`` attribute: False for a genuinely missing
        file (nothing has ever been saved — normal first run), True when
        the file existed but failed to parse (real candidate data may still
        be sitting in those unreadable bytes).
        """
        try:
            raw = Path(path).read_text()
        except OSError as exc:
            logger.warning("CandidateStore.load(%s) failed (%s); returning empty store", path, exc)
            return cls()
        try:
            d = json.loads(raw)
            return cls.from_json(d)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "CandidateStore.load(%s) failed (%s); returning empty (corrupt) store", path, exc
            )
            store = cls()
            store.load_corrupt = True
            return store
