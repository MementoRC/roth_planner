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

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, SupportsIndex


class Source(StrEnum):
    """Where a value came from."""

    MANUAL = "MANUAL"
    PDF = "PDF"
    FINEXTRACT_LIVE = "FINEXTRACT_LIVE"
    MARKET_QUOTE = "MARKET_QUOTE"
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

    def __reduce__(self) -> tuple[type[SourcedValue], tuple[float, Provenance]]:
        return (SourcedValue, (float(self), self.prov))


class SourcedDict(dict):
    """Dict subclass with a parallel per-key ``Provenance`` map.

    Immutable after construction (audit-0721 C24): the inherited dict
    mutators (``__setitem__``, ``update``, ``pop``, ``popitem``,
    ``setdefault``, ``clear``, ``__delitem__``) are overridden to raise,
    because none of them know how to keep ``self.prov`` in sync -- a silent
    mutation would desync provenance from data and corrupt ``to_json``/
    ``from_json`` round-trips. Build a new ``SourcedDict`` with the desired
    data + provenance instead.
    """

    prov: dict[Any, Provenance]
    _MUTATION_ERROR = (
        "SourcedDict is immutable after construction — build a new SourcedDict "
        "with the desired data + provenance instead of mutating in place "
        "(inherited dict mutators would desync self.prov from the data)."
    )

    def __init__(self, data: dict, prov: dict[Any, Provenance]) -> None:
        super().__init__(data)
        self.prov = dict(prov)

    def __setitem__(self, key: Any, value: Any) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def __delitem__(self, key: Any) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def pop(self, *args: Any) -> Any:
        raise TypeError(self._MUTATION_ERROR)

    def popitem(self) -> Any:
        raise TypeError(self._MUTATION_ERROR)

    def setdefault(self, key: Any, default: Any = None) -> Any:
        raise TypeError(self._MUTATION_ERROR)

    def clear(self) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def to_json(self) -> dict:
        if self.keys() != self.prov.keys():
            raise ValueError(
                f"SourcedDict data/prov key mismatch: {sorted(self.keys(), key=str)} "
                f"vs {sorted(self.prov.keys(), key=str)}"
            )
        return {
            "data": {str(k): v for k, v in self.items()},
            "prov": {str(k): p.to_json() for k, p in self.prov.items()},
        }

    @classmethod
    def from_json(cls, d: dict, key_type: type = int) -> SourcedDict:
        data = {key_type(k): v for k, v in d["data"].items()}
        prov = {key_type(k): Provenance.from_json(p) for k, p in d["prov"].items()}
        return cls(data, prov)

    def __reduce__(self) -> tuple[type[SourcedDict], tuple[dict, dict[Any, Provenance]]]:
        return (SourcedDict, (dict(self), self.prov))


class SourcedList(list):
    """List subclass with a parallel ``Provenance`` list, aligned by index.

    Limitation (this wave only): ``to_json``/``from_json`` only support
    elements that are numbers, strings, or otherwise trivially JSON-able.

    Immutable after construction (audit-0721 C24): the inherited list
    mutators (``append``, ``insert``, ``extend``, ``pop``, ``remove``,
    ``clear``, ``__setitem__``, ``__delitem__``, ``__iadd__``) are overridden
    to raise, because none of them know how to keep ``self.prov`` aligned by
    index -- a silent mutation would desync provenance from data and
    corrupt ``to_json``/``from_json`` round-trips. Build a new ``SourcedList``
    with the desired data + provenance instead.
    """

    prov: list[Provenance]
    _MUTATION_ERROR = (
        "SourcedList is immutable after construction — build a new SourcedList "
        "with the desired data + provenance instead of mutating in place "
        "(inherited list mutators would desync self.prov from the data)."
    )

    def __init__(self, data: list, prov: list[Provenance]) -> None:
        super().__init__(data)
        self.prov = list(prov)

    def append(self, value: Any) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def insert(self, index: SupportsIndex, value: Any) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def extend(self, values: Any) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def pop(self, index: SupportsIndex = -1) -> Any:
        raise TypeError(self._MUTATION_ERROR)

    def remove(self, value: Any) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def clear(self) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def __setitem__(self, index: Any, value: Any) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def __delitem__(self, index: Any) -> None:
        raise TypeError(self._MUTATION_ERROR)

    def __iadd__(self, other: Iterable[Any]) -> SourcedList:  # type: ignore[misc]
        # Always raises -- mypy's __iadd__/__add__ consistency check doesn't
        # model that, so this override is exempted rather than widened to an
        # unsound signature.
        raise TypeError(self._MUTATION_ERROR)

    def to_json(self) -> dict:
        if len(self) != len(self.prov):
            raise ValueError(
                f"SourcedList data/prov length mismatch: {len(self)} data vs "
                f"{len(self.prov)} prov"
            )
        return {
            "data": list(self),
            "prov": [p.to_json() for p in self.prov],
        }

    @classmethod
    def from_json(cls, d: dict) -> SourcedList:
        prov = [Provenance.from_json(p) for p in d["prov"]]
        return cls(list(d["data"]), prov)

    def __reduce__(self) -> tuple[type[SourcedList], tuple[list, list[Provenance]]]:
        return (SourcedList, (list(self), self.prov))
