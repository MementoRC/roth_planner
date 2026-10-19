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
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
    """Raised by :meth:`ChoiceMap.save` when ``path`` exists but does not
    parse as a choice map (truncated/malformed write, or wrong shape).

    Mirrors ``CorruptCandidateStoreError`` (engine/data_sources/candidate_store.py)
    and ``CorruptCommittedCacheError`` (engine/data_sources/committed.py): a
    missing file means no choice was ever saved, so writing fresh is safe. A
    file that exists but fails to parse may hold the user's only copy of their
    trust choices, and ``ChoiceMap.load`` has already turned it into an empty
    map, so saving that map back would make the loss permanent and silent
    (audit-1008 F-45). Callers catch this and leave the file untouched (see
    app.py's ``choices.save`` call site).
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"trust choices at {path!r} are corrupt: {cause!r}")


class ChoiceMap:
    """Per-field-key trusted-source choice."""

    def __init__(self) -> None:
        self._data: dict[str, TrustChoice] = {}
        # Set True by ``load`` when the file EXISTED but failed to parse, so an
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

    def save(self, path: str | Path) -> None:
        """Write this map to ``path`` durably.

        Mirrors ``CandidateStore.save`` (audit-1008 F-45): the JSON goes to a
        temp file in the same directory and is ``os.replace()``d onto the
        target, so a crash or full disk mid-write cannot truncate an existing
        good file. If ``path`` exists but does not parse as a choice map, the
        write is refused with :class:`CorruptChoiceMapError` so the unreadable
        bytes (possibly the only copy of the user's choices) stay on disk.
        """
        target = Path(path)
        if target.exists():
            try:
                self.from_json(json.loads(target.read_text()))
            except (OSError, KeyError, ValueError, TypeError) as exc:
                raise CorruptChoiceMapError(path, exc) from exc
        tmp_path = target.with_name(f"{target.name}.tmp-{os.getpid()}")
        try:
            tmp_path.write_text(json.dumps(self.to_json()))
            os.replace(tmp_path, target)
        finally:
            tmp_path.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: str | Path) -> ChoiceMap:
        """Load from ``path``; a missing or corrupt file yields an EMPTY map.

        Never raises — logs a warning instead so a broken cache file can't
        crash the Setup / Command Center. A file that exists but fails to
        parse sets ``load_corrupt`` on the returned map; ``save`` refuses to
        overwrite such a file.
        """
        try:
            raw = Path(path).read_text()
            return cls.from_json(json.loads(raw))
        except OSError as exc:
            logger.warning("ChoiceMap.load(%s) failed (%s); returning empty map", path, exc)
            return cls()
        except (KeyError, ValueError, TypeError) as exc:
            # UnicodeDecodeError (non-UTF-8 bytes) is a ValueError: corrupt, not fatal.
            logger.warning(
                "ChoiceMap.load(%s) failed (%s); returning empty (corrupt) map", path, exc
            )
            cm = cls()
            cm.load_corrupt = True
            return cm
