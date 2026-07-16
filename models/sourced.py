"""Provenance-carrying value wrappers for the Setup / Command Center feature.

Pure model module: stdlib only. No streamlit, no engine imports.

Every user-facing input value can originate from several places (manual entry,
a parsed PDF, a live FinExtract sync, a heuristic estimate, an imported data
bundle, or an as-shipped default). These wrappers let call sites carry that
provenance alongside the value itself without changing how the value behaves
in ordinary arithmetic.

``SourcedValue`` is a transparent ``float`` subclass: arithmetic operations
return plain ``float`` results because provenance is only meaningful for a
*stored* value, never for a value derived from it (e.g. ``balance * 1.07``
is a projection, not a re-sourced fact).

``SourcedDict`` and ``SourcedList`` carry a parallel provenance structure
(keyed/indexed the same as the data) for collections where each entry may
have been sourced independently (e.g. one MAGI year from a PDF, another
entered manually).

Limitation (this wave only): ``SourcedList.to_json``/``from_json`` support
numeric, string, and JSON-dict-able elements only. Non-trivially-serializable
elements (e.g. dataclass instances such as ``StockGrant``) are not supported
here; a future wave should extend this if grant lists need provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class Source(StrEnum):
    """Where a value came from."""

    MANUAL = "MANUAL"
    PDF = "PDF"
    FINEXTRACT_LIVE = "FINEXTRACT_LIVE"
    ESTIMATE = "ESTIMATE"
    BUNDLE = "BUNDLE"
    DEFAULT = "DEFAULT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Provenance:
    """Immutable record of where a value came from and when."""

    source: Source
    recorded_at: datetime
    detail: str = ""

    def to_json(self) -> dict:
        return {
            "source": str(self.source),
            "recorded_at": self.recorded_at.isoformat(),
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, d: dict) -> Provenance:
        return cls(
            source=Source(d["source"]),
            recorded_at=datetime.fromisoformat(d["recorded_at"]),
            detail=d.get("detail", ""),
        )


class SourcedValue(float):
    """Transparent float subclass carrying provenance.

    Arithmetic returns a plain ``float`` (provenance is meaningful only on a
    stored value, never on a value derived from one).
    """

    prov: Provenance

    def __new__(cls, value: float, prov: Provenance) -> SourcedValue:
        obj = super().__new__(cls, value)
        obj.prov = prov
        return obj

    def to_json(self) -> dict:
        payload = self.prov.to_json()
        payload["value"] = float(self)
        return payload

    @classmethod
    def from_json(cls, d: dict) -> SourcedValue:
        prov = Provenance.from_json(d)
        return cls(d["value"], prov)


class SourcedDict(dict):
    """Dict subclass with a parallel per-key ``Provenance`` map."""

    prov: dict[Any, Provenance]

    def __init__(self, data: dict, prov: dict[Any, Provenance]) -> None:
        super().__init__(data)
        self.prov = dict(prov)

    def to_json(self) -> dict:
        return {
            "data": {str(k): v for k, v in self.items()},
            "prov": {str(k): p.to_json() for k, p in self.prov.items()},
        }

    @classmethod
    def from_json(cls, d: dict, key_type: type = int) -> SourcedDict:
        data = {key_type(k): v for k, v in d["data"].items()}
        prov = {key_type(k): Provenance.from_json(p) for k, p in d["prov"].items()}
        return cls(data, prov)


class SourcedList(list):
    """List subclass with a parallel ``Provenance`` list, aligned by index.

    Limitation (this wave only): ``to_json``/``from_json`` only support
    elements that are numbers, strings, or otherwise trivially JSON-able.
    """

    prov: list[Provenance]

    def __init__(self, data: list, prov: list[Provenance]) -> None:
        super().__init__(data)
        self.prov = list(prov)

    def to_json(self) -> dict:
        return {
            "data": list(self),
            "prov": [p.to_json() for p in self.prov],
        }

    @classmethod
    def from_json(cls, d: dict) -> SourcedList:
        prov = [Provenance.from_json(p) for p in d["prov"]]
        return cls(list(d["data"]), prov)
