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


def load_committed(path: str | Path) -> dict | None:
    """Load a committed_json payload from ``path``.

    Returns None if the file is missing or corrupt (logging a warning in the
    corrupt case) — never raises, matching CandidateStore.load/ChoiceMap.load.
    """
    try:
        raw = Path(path).read_text()
        return dict(json.loads(raw))
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning("load_committed(%s) failed (%s); returning None", path, exc)
        return None


def save_committed(path: str | Path, committed_json: dict) -> None:
    Path(path).write_text(json.dumps(committed_json))
