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


class CandidateStore:
    """Latest-per-source candidate values, keyed by field."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Candidate]] = {}

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
        Path(path).write_text(json.dumps(self.to_json()))

    @classmethod
    def load(cls, path: str | Path) -> CandidateStore:
        """Load from ``path``; a missing or corrupt file yields an EMPTY store.

        Never raises — logs a warning instead so a broken cache file can't
        crash the Setup / Command Center.
        """
        try:
            raw = Path(path).read_text()
            d = json.loads(raw)
            return cls.from_json(d)
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            logger.warning("CandidateStore.load(%s) failed (%s); returning empty store", path, exc)
            return cls()
