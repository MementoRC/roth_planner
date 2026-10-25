"""Per-field trusted-source choices for the Setup / Command Center.

No streamlit. Saved in the document store, namespace ``trust_choices``.

Once a user tells the app "trust FinExtract for your_ira going forward", that
choice is recorded here so future resolutions don't need to re-ask — until
the field is committed (frozen), at which point the choice becomes moot for
that field (see engine/data_sources/resolver.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from engine.store import CorruptStoreError, get_store
from engine.store.namespaces import TRUST_CHOICES
from models.legacy_keys import migrate_legacy_keys
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


class CorruptChoiceMapError(Exception):
    """Raised by :meth:`ChoiceMap.save` when the saved choices do not parse
    as a choice map (truncated/malformed write, or wrong shape).

    Mirrors ``CorruptCandidateStoreError`` (engine/data_sources/candidate_store.py)
    and ``CorruptCommittedCacheError`` (engine/data_sources/committed.py): no
    saved choices means none was ever made, so writing fresh is safe. A saved
    row that fails to parse may hold the user's only copy of their
    trust choices, and ``ChoiceMap.load`` has already turned it into an empty
    map, so saving that map back would make the loss permanent and silent
    (audit-1008 F-45). Callers catch this and leave it untouched (see
    app.py's ``choices.save`` call site).
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"trust choices at {path!r} are corrupt: {cause!r}")


_SHAPE_ERRORS = (KeyError, ValueError, TypeError)


class ChoiceMap:
    """Per-field-key trusted-source choice."""

    def __init__(self) -> None:
        self._data: dict[str, TrustChoice] = {}
        # Set True by ``load`` when the saved row EXISTED but failed to parse, so an
        # empty-because-corrupt map is distinguishable from a never-saved one.
        self.load_corrupt: bool = False

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
        """Rebuild a map; raises ValueError/TypeError/KeyError on a bad shape."""
        if not isinstance(d, dict):
            raise ValueError(f"trust choices must be a JSON object, got {type(d).__name__}")
        cm = cls()
        # Choices saved before the stock_* rename key the price field by its old name.
        for k, v in migrate_legacy_keys(d).items():
            cm._data[k] = TrustChoice.from_json(v)
        return cm

    def save(self) -> None:
        """Save this map.

        Refuses, with :class:`CorruptChoiceMapError`, to replace saved choices
        that do not parse as a choice map. They may be the only copy of the
        user's choices, and :meth:`load` already turned them into an empty map
        (audit-1008 F-45). The check and the write are one transaction.
        """
        store = get_store()
        with store.transaction():
            try:
                saved = store.get(TRUST_CHOICES)
                if saved is not None:
                    self.from_json(saved)
            except (CorruptStoreError, *_SHAPE_ERRORS) as exc:
                raise CorruptChoiceMapError(TRUST_CHOICES, exc) from exc
            store.put(TRUST_CHOICES, self.to_json())

    @classmethod
    def load(cls) -> ChoiceMap:
        """The saved map; absent or unreadable yields an EMPTY map.

        An unreadable row sets ``load_corrupt`` on the returned map rather than
        raising, and :meth:`save` refuses to replace it.
        """
        try:
            saved = get_store().get(TRUST_CHOICES)
            return cls() if saved is None else cls.from_json(saved)
        except (CorruptStoreError, *_SHAPE_ERRORS) as exc:
            logger.warning("saved trust choices are unreadable (%s); using an empty map", exc)
            empty = cls()
            empty.load_corrupt = True
            return empty
