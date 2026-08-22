"""Committed-baseline persistence over the sourced Household fields.

Pure module: stdlib + models/ + engine.data_sources.resolver only. No
streamlit imports.

The "committed baseline" is the JSON-serializable snapshot of every
currently-committed (Sourced*) attribute on a Household — what gets written
to disk between app runs so a fresh session can rebuild the exact same
frozen values (see resolver.py's freeze invariant) without re-deriving them
from session_state or a fresh snapshot every load.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.data_sources.resolver import HOUSEHOLD_SCALAR_FIELDS
from models.grants import StockGrant
from models.household import Household
from models.sourced import Provenance, Source, SourcedDict, SourcedList, SourcedValue

logger = logging.getLogger(__name__)

# Only the sourced fields that exist as real Household attributes today.
# (The *_ytd fields in SOURCED_SCALAR_FIELDS live in YTD snapshots, not on
# Household, so they are deliberately excluded here.)
COMMITTED_FIELD_ATTRS: list[str] = [*HOUSEHOLD_SCALAR_FIELDS, "prior_year_magi", "grants"]

_MAGI_ATTR = "prior_year_magi"
_GRANTS_ATTR = "grants"


def _grant_to_json(grant: StockGrant) -> dict:
    return dataclasses.asdict(grant)


def _grant_from_json(d: dict) -> StockGrant:
    return StockGrant(**d)


def extract_committed(hh: Household) -> dict:
    """Serialize every attr in COMMITTED_FIELD_ATTRS that is CURRENTLY a
    committed Sourced* instance on ``hh``. Plain (never-committed) attrs are
    skipped entirely.
    """
    out: dict[str, Any] = {}
    for attr in COMMITTED_FIELD_ATTRS:
        value = getattr(hh, attr, None)
        if attr == _MAGI_ATTR:
            if isinstance(value, SourcedDict):
                out[attr] = value.to_json()
        elif attr == _GRANTS_ATTR:
            if isinstance(value, SourcedList):
                out[attr] = {
                    "data": [_grant_to_json(g) for g in value],
                    "prov": [p.to_json() for p in value.prov],
                }
        elif isinstance(value, SourcedValue):
            out[attr] = value.to_json()
    return out


def apply_committed(hh: Household, committed_json: dict) -> None:
    """Mutate ``hh`` in place, wrapping each attr present in ``committed_json``
    as the corresponding Sourced* type. Keys absent from ``committed_json``
    (or not in COMMITTED_FIELD_ATTRS) are left untouched on ``hh``.
    """
    for attr, payload in committed_json.items():
        if attr not in COMMITTED_FIELD_ATTRS:
            continue
        if attr == _MAGI_ATTR:
            setattr(hh, attr, SourcedDict.from_json(payload, key_type=int))
        elif attr == _GRANTS_ATTR:
            grants = [_grant_from_json(d) for d in payload["data"]]
            prov = [Provenance.from_json(p) for p in payload["prov"]]
            setattr(hh, attr, SourcedList(grants, prov))
        else:
            setattr(hh, attr, SourcedValue.from_json(payload))


def migrate_committed(hh: Household, recorded_at: datetime, detail: str = "pre-migration") -> dict:
    """Wrap every COMMITTED_FIELD_ATTRS value currently on ``hh`` as a
    committed Sourced* value with ``Source.UNKNOWN`` provenance (mutating
    ``hh`` in place), and return the resulting committed_json.

    Numeric values are preserved exactly — wrapping a float in SourcedValue
    keeps ``==`` against the original plain float.
    """
    prov = Provenance(Source.UNKNOWN, recorded_at, detail)
    for attr in COMMITTED_FIELD_ATTRS:
        value = getattr(hh, attr)
        if attr == _MAGI_ATTR:
            data = dict(value)
            setattr(hh, attr, SourcedDict(data, dict.fromkeys(data, prov)))
        elif attr == _GRANTS_ATTR:
            grants = list(value)
            setattr(hh, attr, SourcedList(grants, [prov] * len(grants)))
        else:
            setattr(hh, attr, SourcedValue(float(value), prov))
    return extract_committed(hh)


class CorruptCommittedCacheError(Exception):
    """Raised by :func:`load_committed` when ``path`` exists but its content
    is not a valid committed_json payload (truncated/malformed JSON or wrong
    shape).

    Deliberately NOT the same outcome as a missing file: a missing file means
    "no baseline has ever been committed yet" — safe for the caller to
    silently re-migrate and overwrite. A file that exists but fails to parse
    means "a baseline WAS committed and its bytes are still on disk but
    unreadable" — the only copy of that data is the corrupt file itself, so
    silently treating it as "nothing committed" and re-migrating over it
    would permanently destroy it. Callers must catch this separately and
    leave the file untouched (see app.py's load_committed call site).
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"committed cache at {path!r} is corrupt: {cause!r}")


def load_committed(path: str | Path) -> dict | None:
    """Load a committed_json payload from ``path``.

    Returns None if the file is missing/unreadable (OSError) — that case
    means "no baseline has ever been committed yet" and is safe for callers
    to treat as a fresh first load. Raises :class:`CorruptCommittedCacheError`
    if the file EXISTS but its content fails to parse or has the wrong shape
    — that case means real committed data is sitting on disk in an unreadable
    state, and must not be silently conflated with "nothing committed" (doing
    so would let a caller re-migrate and overwrite the only copy of that
    data). See CorruptCommittedCacheError's docstring for the full rationale.
    """
    try:
        raw = Path(path).read_text()
    except OSError as exc:
        logger.warning("load_committed(%s) failed (%s); returning None", path, exc)
        return None
    try:
        return dict(json.loads(raw))
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise CorruptCommittedCacheError(path, exc) from exc


def save_committed(path: str | Path, committed_json: dict) -> None:
    """Write ``committed_json`` to ``path`` durably.

    Writes to a temp file in the same directory then ``os.replace()`` onto
    the target so a crash or full disk mid-write cannot truncate an existing
    good committed baseline in place (audit-0809 #11 — see
    CorruptCommittedCacheError). ``os.replace`` is atomic on POSIX (and on
    Windows since it maps to ``MoveFileEx`` with the replace flag), so
    readers never observe a partially-written file.

    audit-0809 #11 (design follow-up): this is the ONE place the
    never-overwrite-a-baseline-we-couldn't-read invariant is enforced, so
    every caller gets it "for free" regardless of how it reached this call
    (a Setup Confirm click, app.py's migration path, etc). Before writing,
    if ``path`` already exists we read+parse its current content once; if
    that parse fails, we refuse to write and raise
    CorruptCommittedCacheError instead — the on-disk bytes may be the
    user's only copy of that data, and blindly replacing them (even via the
    atomic temp+replace above) would still destroy it. A missing target (no
    prior baseline to protect) or an existing-and-parseable one both write
    normally.
    """
    target = Path(path)
    if target.exists():
        try:
            json.loads(target.read_text())
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise CorruptCommittedCacheError(path, exc) from exc
    tmp_path = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    tmp_path.write_text(json.dumps(committed_json))
    os.replace(tmp_path, target)
