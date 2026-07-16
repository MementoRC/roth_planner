"""Per-field trusted-source choices for the Setup / Command Center.

Pure module: stdlib + models/ only. No streamlit, no other engine imports.

Once a user tells the app "trust FinExtract for your_ira going forward", that
choice is recorded here so future resolutions don't need to re-ask — until
the field is committed (frozen), at which point the choice becomes moot for
that field (see engine/data_sources/resolver.py).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from models.sourced import Source

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrustChoice:
    """A locked-in decision: use ``source`` for this field as of ``locked_at``."""

    source: Source
    locked_at: datetime

    def to_json(self) -> dict:
        return {"source": str(self.source), "locked_at": self.locked_at.isoformat()}

    @classmethod
    def from_json(cls, d: dict) -> TrustChoice:
        return cls(source=Source(d["source"]), locked_at=datetime.fromisoformat(d["locked_at"]))


class ChoiceMap:
    """Per-field-key trusted-source choice."""

    def __init__(self) -> None:
        self._data: dict[str, TrustChoice] = {}

    def set_choice(self, field_key: str, source: Source, locked_at: datetime) -> None:
        self._data[field_key] = TrustChoice(source=source, locked_at=locked_at)

    def get(self, field_key: str) -> TrustChoice | None:
        return self._data.get(field_key)

    def clear(self, field_key: str) -> None:
        self._data.pop(field_key, None)

    def to_json(self) -> dict:
        return {k: v.to_json() for k, v in self._data.items()}

    @classmethod
    def from_json(cls, d: dict) -> ChoiceMap:
        cm = cls()
        for k, v in d.items():
            cm._data[k] = TrustChoice.from_json(v)
        return cm

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_json()))

    @classmethod
    def load(cls, path: str | Path) -> ChoiceMap:
        """Load from ``path``; a missing or corrupt file yields an EMPTY map.

        Never raises — logs a warning instead so a broken cache file can't
        crash the Setup / Command Center.
        """
        try:
            raw = Path(path).read_text()
            d = json.loads(raw)
            return cls.from_json(d)
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            logger.warning("ChoiceMap.load(%s) failed (%s); returning empty map", path, exc)
            return cls()
