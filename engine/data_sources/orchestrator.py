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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import apply_committed, migrate_committed
from engine.data_sources.resolver import (
    DEFAULT_LADDER,
    HOUSEHOLD_SCALAR_FIELDS,
    ResolveResult,
    _pick_candidate,
    magi_field_key,
    resolve,
)
from engine.data_sources.seed_defaults import seed_default_fields
from engine.data_sources.snapshot_ingest import (
    apply_snapshot_overwrite,
    record_bundle_grant_candidates,
    record_snapshot_candidates,
)
from models.household import Household
from models.sourced import Provenance, Source

_MAGI_ATTR = "prior_year_magi"

# Session key the Setup MAGI-anchor form uses to flag years the user set back
# to 0; app.py passes it to ``resolve_for_app(cleared_magi_years=...)`` and
# drops it once applied (audit-1008 F-64).
MAGI_CLEARED_KEY = "_prior_year_magi_cleared"

# Session keys (issue #43). SEED_VALUES_KEY: attr -> value a fresh session was
# seeded with, consumed (popped) by the first resolve. BUNDLE_SCALARS_KEY: the
# scalars a bundle import drained before any baseline existed, consumed by the
# migrating resolve so they are labelled Backup import.
SEED_VALUES_KEY = "_seed_sourced_values"
BUNDLE_SCALARS_KEY = "_first_load_bundle_scalars"

# Single source of truth for Household sourced-field attr -> session_state key.
# Almost all are 1:1; stock_price_now is aliased to "stock_price" because the
# Setup number_input widget (views/setup/parameters.py) predates this field
# being a Household attribute name. Shared by app.py's post-resolve session
# writeback and views/setup/command_center.py's confirm handler so both stay
# in sync on the same key (a prior mismatch here reverted Command Center
# confirms of stock_price_now on the very next render).
SOURCED_SESSION_KEYS: dict[str, str] = {
    "your_ira": "your_ira",
    "spouse_ira": "spouse_ira",
    "your_roth": "your_roth",
    "spouse_roth": "spouse_roth",
    "stock_price_now": "stock_price",
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


def _matches_pending_candidate(
    store: CandidateStore | None,
    year: int,
    value: float,
    choices: ChoiceMap | None = None,
) -> bool:
    """True when ``value`` is the pending winner for ``year`` written back by app.py.

    app.py mirrors the resolved ``prior_year_magi`` (which includes the pending,
    unconfirmed winning candidate for years with no committed value) back into
    session_state. On the next rerun that value must not read as a manual
    edit, or the candidate is committed as Source.MANUAL without ever being
    confirmed (audit-1008 F-46). Only the resolved winner is written back, so a
    value equal to a losing candidate is a genuine edit (audit-1026 F-10).
    """
    if store is None:
        return False
    field_key = magi_field_key(year)
    winner = _pick_candidate(
        field_key, store.candidates_for(field_key), choices or ChoiceMap(), DEFAULT_LADDER
    )
    return winner is not None and not _rounded_differs(value, float(winner.value))


def stamp_bundle_scalars(
    committed_json: dict,
    scalar_updates: dict[str, Any],
    recorded_at: datetime,
    detail: str = "Data bridge import",
) -> bool:
    """Commit bundle-imported sourced scalars as ``Source.BUNDLE`` (audit-1018 F-21).

    ``scalar_updates`` is keyed by session key (``stock_price``, not
    ``stock_price_now``). Without this, the drained values differ from the
    baseline and ``reconcile_manual_edits`` relabels them ``Source.MANUAL``.
    Only fields already committed whose whole-dollar value actually changes are
    re-stamped, in place; returns True when anything changed.
    """
    changed = False
    prov_json = Provenance(Source.BUNDLE, recorded_at, detail).to_json()
    for attr in HOUSEHOLD_SCALAR_FIELDS:
        payload = committed_json.get(attr)
        value = scalar_updates.get(SOURCED_SESSION_KEYS.get(attr, attr))
        if payload is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if _rounded_differs(value, payload["value"]):
            committed_json[attr] = {**prov_json, "value": float(value)}
            changed = True
    return changed


def stamp_first_load_bundle_scalars(
    committed_json: dict,
    bundle_scalars: Mapping[str, Any],
    recorded_at: datetime,
    detail: str = "Data bridge import",
) -> None:
    """Label freshly migrated fields that hold a bundle-imported value ``Source.BUNDLE``.

    First-load counterpart of :func:`stamp_bundle_scalars` (audit-1018 F-21
    follow-up, issue #43): with no baseline yet the import could not stamp
    anything, so the migrated value would be UNKNOWN or DEFAULT. A field is
    stamped only when its migrated whole-dollar value equals the imported one
    (a snapshot overlay that won the migration keeps its own provenance).
    """
    prov_json = Provenance(Source.BUNDLE, recorded_at, detail).to_json()
    for attr in HOUSEHOLD_SCALAR_FIELDS:
        payload = committed_json.get(attr)
        value = bundle_scalars.get(SOURCED_SESSION_KEYS.get(attr, attr))
        if payload is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not _rounded_differs(value, payload["value"]):
            committed_json[attr] = {**prov_json, "value": payload["value"]}


def reconcile_manual_edits(
    session_hh: Household,
    committed_json: dict,
    recorded_at: datetime,
    store: CandidateStore | None = None,
    cleared_magi_years: Iterable[int] = (),
    unedited_seed: Mapping[str, float] | None = None,
    choices: ChoiceMap | None = None,
) -> tuple[dict, bool]:
    """Promote Setup-form edits (raw ``session_hh`` values) to committed
    MANUAL entries, mutating and returning ``committed_json`` in place.

    Must run BEFORE ``apply_committed`` mutates ``session_hh`` — it compares
    the raw session value (whatever the Setup number_input currently holds)
    against the frozen committed numeric value. A genuine difference means
    the user edited the field since it was last committed, so it is promoted
    to a fresh ``Source.MANUAL`` entry; an unchanged field is left untouched
    (provenance is not disturbed just because reconcile ran).

    For ``prior_year_magi``, a year with no committed value whose session
    value equals the resolved winning candidate (per ``choices``/the default
    ladder) is the written-back pending candidate, not an edit, and stays
    pending (audit-1008 F-46); a value equal to a losing candidate is an edit
    (audit-1026 F-10).
    A committed year absent from the session dict is NOT deleted (a fresh
    session seeds ``{}``); only years in ``cleared_magi_years`` — the explicit
    "Leave 0" signal from the Setup form — are removed (audit-1008 F-64).

    ``unedited_seed`` (attr -> value) are the values a fresh session was seeded
    with. A session value still equal to its seed is not a user action, so it
    must not overwrite a committed baseline value as ``Source.MANUAL`` (issue
    #43); only the first resolve of a session passes it.
    """
    changed = False
    seed = unedited_seed or {}
    prov_json = Provenance(Source.MANUAL, recorded_at, "manual entry").to_json()

    for attr in HOUSEHOLD_SCALAR_FIELDS:
        payload = committed_json.get(attr)
        if payload is None:
            continue
        session_value = getattr(session_hh, attr, None)
        if session_value is None:
            continue
        if attr in seed and not _rounded_differs(session_value, seed[attr]):
            continue
        if _rounded_differs(session_value, payload["value"]):
            new_payload = dict(prov_json)
            new_payload["value"] = float(session_value)
            committed_json[attr] = new_payload
            changed = True

    magi_payload = committed_json.get(_MAGI_ATTR)
    session_magi = getattr(session_hh, _MAGI_ATTR, None) or {}
    cleared = {int(y) for y in cleared_magi_years}
    if magi_payload is not None and (session_magi or cleared):
        data = dict(magi_payload.get("data", {}))
        prov = dict(magi_payload.get("prov", {}))
        magi_changed = False
        for year in cleared:
            if data.pop(str(year), None) is not None:
                magi_changed = True
            prov.pop(str(year), None)
        for year, value in session_magi.items():
            if int(year) in cleared:
                continue
            year_key = str(year)
            existing = data.get(year_key)
            if existing is None and _matches_pending_candidate(store, int(year), value, choices):
                continue
            if existing is None or _rounded_differs(value, existing):
                data[year_key] = float(value)
                prov[year_key] = prov_json
                magi_changed = True
                if store is not None:
                    store.clear_dismissals(magi_field_key(int(year)))
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
    cleared_magi_years: Iterable[int] = (),
    unedited_seed: Mapping[str, float] | None = None,
    bundle_scalars: Mapping[str, Any] | None = None,
) -> AppResolveResult:
    """Run the per-load pipeline (see module docstring).

    ``cleared_magi_years`` are prior-year MAGI years the user explicitly set
    back to 0 in Setup ("Leave 0 to use projected MAGI"): they are removed
    from the committed baseline and their candidates dropped from ``store``
    so the anchor does not reappear (audit-1008 F-64). Their candidates are
    recorded as dismissed in ``store`` so the boot-time PDF re-record does not
    bring them back on a later rerun or restart (audit-1026 F-05); a manual
    entry for the year lifts the dismissal.

    ``unedited_seed`` and ``bundle_scalars`` are issue #43 inputs: see
    :func:`reconcile_manual_edits` and :func:`stamp_first_load_bundle_scalars`.
    """
    migrated = False
    cleared = {int(y) for y in cleared_magi_years}
    for year in cleared:
        store.dismiss(magi_field_key(year))

    if committed_json is None:
        # First load with no saved committed baseline: replicate the OLD
        # behavior (session + snapshot overwrite of sourced fields) as the
        # baseline so migration is a numeric no-op.
        base = copy.deepcopy(session_hh)
        # Untouched seed values are tagged DEFAULT so the completeness badge
        # can flag them "unconfirmed". A snapshot overlay can't be told apart
        # from a coincidentally-equal seed value, so with one every field
        # keeps the UNKNOWN baseline.
        default_fields: frozenset[str] = frozenset()
        if snap is not None and getattr(snap, "server_available", False):
            apply_snapshot_overwrite(base, snap, strikes)
        else:
            default_fields = seed_default_fields(base)
        committed_json = migrate_committed(base, recorded_at, default_fields=default_fields)
        if bundle_scalars:
            stamp_first_load_bundle_scalars(committed_json, bundle_scalars, recorded_at)
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
        committed_json, reconciled = reconcile_manual_edits(
            session_hh, committed_json, recorded_at, store, cleared, unedited_seed, choices
        )

    apply_committed(session_hh, committed_json)

    dropped: list[tuple[int, int]] = []
    if snap is not None and getattr(snap, "server_available", False):
        dropped = record_snapshot_candidates(store, snap, strikes, recorded_at)
    elif snap is not None and getattr(snap, "imported_from_bundle", False):
        # audit-1008 F-06: an imported snapshot never has server_available,
        # so its grants were silently ignored here.
        dropped = record_bundle_grant_candidates(store, snap, strikes, recorded_at)

    result = resolve(session_hh, store, choices)
    return AppResolveResult(
        result=result,
        committed_json=committed_json,
        migrated=migrated,
        committed_changed=migrated or reconciled,
        dropped_missing_strike=dropped,
    )
