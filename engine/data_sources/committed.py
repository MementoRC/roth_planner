"""Committed-baseline persistence over the sourced Household fields.

No streamlit imports. Saved in the document store, namespace ``committed``.

The "committed baseline" is the JSON-serializable snapshot of every
currently-committed (Sourced*) attribute on a Household — what is saved
between app runs so a fresh session can rebuild the exact same
frozen values (see resolver.py's freeze invariant) without re-deriving them
from session_state or a fresh snapshot every load.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from engine.data_sources.resolver import HOUSEHOLD_SCALAR_FIELDS
from engine.store import CorruptDocumentError, CorruptStoreError, Store, get_store
from engine.store.namespaces import COMMITTED
from models.grants import StockGrant
from models.household import Household
from models.legacy_keys import migrate_legacy_keys
from models.sourced import Provenance, Source, SourcedDict, SourcedList, SourcedValue

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
    (or not in COMMITTED_FIELD_ATTRS) are left untouched on ``hh``. A baseline
    saved before the stock_* rename is read under the current attr names.
    """
    for attr, payload in migrate_legacy_keys(committed_json).items():
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


def migrate_committed(
    hh: Household,
    recorded_at: datetime,
    detail: str = "pre-migration",
    default_fields: Collection[str] = (),
) -> dict:
    """Wrap every COMMITTED_FIELD_ATTRS value currently on ``hh`` as a
    committed Sourced* value with ``Source.UNKNOWN`` provenance (mutating
    ``hh`` in place), and return the resulting committed_json.

    Attrs named in ``default_fields`` (every item, for dict/list attrs) are
    instead stamped ``Source.DEFAULT`` with detail "seed default".

    Numeric values are preserved exactly — wrapping a float in SourcedValue
    keeps ``==`` against the original plain float.
    """
    unknown_prov = Provenance(Source.UNKNOWN, recorded_at, detail)
    default_prov = Provenance(Source.DEFAULT, recorded_at, "seed default")
    for attr in COMMITTED_FIELD_ATTRS:
        prov = default_prov if attr in default_fields else unknown_prov
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


class CorruptCommittedCacheError(CorruptDocumentError):
    """Raised by :func:`load_committed` and :func:`save_committed` when a saved
    baseline exists but cannot be read or has the wrong shape.

    Deliberately NOT the same outcome as no saved baseline: that means "no
    baseline has ever been committed yet" — safe for the caller to silently
    re-migrate and overwrite. A saved baseline that fails to parse means "a
    baseline WAS committed and its row is still saved but unreadable" — the
    only copy of that data is the corrupt row itself, so silently treating it
    as "nothing committed" and re-migrating over it would permanently destroy
    it. Callers must catch this separately and leave it untouched (see
    app.py's load_committed call site).
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"committed cache at {path!r} is corrupt: {cause!r}")


def _validated(data: Any) -> dict:
    """The saved baseline exactly as the app consumes it.

    Valid JSON is not enough: the payload must be an object whose entries
    ``apply_committed`` can rebuild, or every later render would crash on it.
    Raises ValueError/TypeError/KeyError/AttributeError on a bad shape.
    """
    if not isinstance(data, dict):
        raise ValueError(f"committed baseline must be a JSON object, got {type(data).__name__}")
    apply_committed(SimpleNamespace(), data)  # type: ignore[arg-type]
    return migrate_legacy_keys(data)


_SHAPE_ERRORS = (KeyError, ValueError, TypeError, AttributeError)


def _read_saved(store: Store) -> dict | None:
    """The saved baseline, ``None`` if none; CorruptCommittedCacheError if unreadable."""
    try:
        data = store.get(COMMITTED)
        return None if data is None else _validated(data)
    except (CorruptStoreError, *_SHAPE_ERRORS) as exc:
        raise CorruptCommittedCacheError(COMMITTED, exc) from exc


def load_committed() -> dict | None:
    """The saved committed baseline, or ``None`` when none has been saved yet.

    Raises :class:`CorruptCommittedCacheError` when a baseline exists but cannot
    be read or has the wrong shape. Real committed data must not be mistaken
    for "nothing committed", or a caller would re-migrate over the only copy.
    """
    return _read_saved(get_store())


def save_committed(committed_json: dict) -> None:
    """Save *committed_json* as the committed baseline.

    audit-0809 #11: this is the ONE place the never-overwrite-a-baseline-we-
    could-not-read rule is enforced, so every caller gets it. If the saved
    baseline fails the parse :func:`load_committed` runs (JSON *and* shape,
    audit-1008 W11), this raises CorruptCommittedCacheError and writes nothing.
    The check and the write are one transaction.
    """
    store = get_store()
    with store.transaction():
        _read_saved(store)
        store.put(COMMITTED, committed_json)
