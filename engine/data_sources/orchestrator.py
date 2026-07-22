"""Top-level orchestration for the Setup / Command Center per-load pipeline.

Pure module: stdlib + models/ + engine.data_sources.* only. No streamlit
imports — app.py wires this in during Wave 3.1b.

``resolve_for_app`` ties together: (1) one-time migration of a pre-existing
Household into a committed baseline (first load only, numerically identical
to the old clobber-based behavior), (2) applying that committed baseline
onto the current session's Household, (3) recording any fresh snapshot
values as candidates rather than overwriting, and (4) arbitrating pending
candidates via resolver.resolve().
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import apply_committed, migrate_committed
from engine.data_sources.resolver import HOUSEHOLD_SCALAR_FIELDS, ResolveResult, resolve
from engine.data_sources.snapshot_ingest import apply_snapshot_overwrite, record_snapshot_candidates
from models.household import Household
from models.sourced import Provenance, Source

_MAGI_ATTR = "prior_year_magi"

# Single source of truth for Household sourced-field attr -> session_state key.
# Almost all are 1:1; txn_price_now is aliased to "txn_price" because the
# Setup number_input widget (views/setup/parameters.py) predates this field
# being a Household attribute name. Shared by app.py's post-resolve session
# writeback and views/setup/command_center.py's confirm handler so both stay
# in sync on the same key (a prior mismatch here reverted Command Center
# confirms of txn_price_now on the very next render).
SOURCED_SESSION_KEYS: dict[str, str] = {
    "your_ira": "your_ira",
    "spouse_ira": "spouse_ira",
    "your_roth": "your_roth",
    "spouse_roth": "spouse_roth",
    "txn_price_now": "txn_price",
    "your_ss_fra": "your_ss_fra",
    "spouse_ss_fra": "spouse_ss_fra",
    _MAGI_ATTR: _MAGI_ATTR,
}


def session_keys_for_writeback() -> dict[str, str]:
    """Return the Household sourced-field attr -> session_state key map."""
    return dict(SOURCED_SESSION_KEYS)


@dataclass
class AppResolveResult:
    result: ResolveResult
    committed_json: dict
    migrated: bool
    committed_changed: bool
    dropped_missing_strike: list[tuple[int, int]]


def _rounded_differs(session_value: float, committed_value: float) -> bool:
    """True when a session value is a genuine edit vs. the committed value.

    Setup balance/price ``number_input`` widgets are integer (``format="%d"``),
    and the resolved-writeback mirror likewise stores ``int(round(value))``.
    Committed values, however, may be fractional (e.g. a FinExtract snapshot's
    summed account balances with cents, or a PDF-parsed MAGI). Comparing the
    raw values exactly would treat the whole-dollar rounding delta as a
    "manual edit" and relabel a FINEXTRACT_LIVE/PDF/confirmed provenance as
    Source.MANUAL on every render, silently dropping cents. Compare at the
    widget's whole-unit granularity instead: only a difference of a full
    dollar or more is a genuine edit.
    """
    return round(float(session_value)) != round(float(committed_value))


def reconcile_manual_edits(
    session_hh: Household, committed_json: dict, recorded_at: datetime
) -> tuple[dict, bool]:
    """Promote Setup-form edits (raw ``session_hh`` values) to committed
    MANUAL entries, mutating and returning ``committed_json`` in place.

    Must run BEFORE ``apply_committed`` mutates ``session_hh`` — it compares
    the raw session value (whatever the Setup number_input currently holds)
    against the frozen committed numeric value. A genuine difference means
    the user edited the field since it was last committed, so it is promoted
    to a fresh ``Source.MANUAL`` entry; an unchanged field is left untouched
    (provenance is not disturbed just because reconcile ran).

    Limitation: for ``prior_year_magi`` this only adds/updates years present
    in ``session_hh.prior_year_magi`` — it never deletes a committed year
    that is absent from the session dict.
    """
    changed = False
    prov_json = Provenance(Source.MANUAL, recorded_at, "manual entry").to_json()

    for attr in HOUSEHOLD_SCALAR_FIELDS:
        payload = committed_json.get(attr)
        if payload is None:
            continue
        session_value = getattr(session_hh, attr, None)
        if session_value is None:
            continue
        if _rounded_differs(session_value, payload["value"]):
            new_payload = dict(prov_json)
            new_payload["value"] = float(session_value)
            committed_json[attr] = new_payload
            changed = True

    magi_payload = committed_json.get(_MAGI_ATTR)
    session_magi = getattr(session_hh, _MAGI_ATTR, None) or {}
    if magi_payload is not None and session_magi:
        data = dict(magi_payload.get("data", {}))
        prov = dict(magi_payload.get("prov", {}))
        magi_changed = False
        for year, value in session_magi.items():
            year_key = str(year)
            existing = data.get(year_key)
            if existing is None or _rounded_differs(value, existing):
                data[year_key] = float(value)
                prov[year_key] = prov_json
                magi_changed = True
        if magi_changed:
            committed_json[_MAGI_ATTR] = {"data": data, "prov": prov}
            changed = True

    return committed_json, changed


def resolve_for_app(
    session_hh: Household,
    snap: Any,
    strikes: dict,
    store: CandidateStore,
    choices: ChoiceMap,
    committed_json: dict | None,
    recorded_at: datetime,
) -> AppResolveResult:
    migrated = False

    if committed_json is None:
        # First load with no committed baseline on disk: replicate the OLD
        # behavior (session + snapshot overwrite of sourced fields) as the
        # baseline so migration is a numeric no-op.
        base = copy.deepcopy(session_hh)
        if snap is not None and getattr(snap, "server_available", False):
            apply_snapshot_overwrite(base, snap, strikes)
        committed_json = migrate_committed(base, recorded_at)
        migrated = True

    # Skip reconcile on the migration render: the committed baseline just
    # built above may already include a snapshot overwrite (``base``), while
    # ``session_hh`` is still the pristine pre-snapshot value. Comparing them
    # here would spuriously read as a "manual edit" and relabel the freshly
    # migrated FinExtract-derived value MANUAL with the stale pristine value
    # (audit-0721 C18). There is nothing to reconcile yet on a first load.
    if migrated:
        reconciled = False
    else:
        committed_json, reconciled = reconcile_manual_edits(session_hh, committed_json, recorded_at)

    apply_committed(session_hh, committed_json)

    dropped: list[tuple[int, int]] = []
    if snap is not None and getattr(snap, "server_available", False):
        dropped = record_snapshot_candidates(store, snap, strikes, recorded_at)

    result = resolve(session_hh, store, choices)
    return AppResolveResult(
        result=result,
        committed_json=committed_json,
        migrated=migrated,
        committed_changed=migrated or reconciled,
        dropped_missing_strike=dropped,
    )
