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
from engine.data_sources.resolver import ResolveResult, resolve
from engine.data_sources.snapshot_ingest import apply_snapshot_overwrite, record_snapshot_candidates
from models.household import Household


@dataclass
class AppResolveResult:
    result: ResolveResult
    committed_json: dict
    migrated: bool
    dropped_missing_strike: list[tuple[int, int]]


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

    apply_committed(session_hh, committed_json)

    dropped: list[tuple[int, int]] = []
    if snap is not None and getattr(snap, "server_available", False):
        dropped = record_snapshot_candidates(store, snap, strikes, recorded_at)

    result = resolve(session_hh, store, choices)
    return AppResolveResult(
        result=result,
        committed_json=committed_json,
        migrated=migrated,
        dropped_missing_strike=dropped,
    )
