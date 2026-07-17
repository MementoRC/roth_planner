"""Reusable MAGI-candidate recording helper for the Setup / Command Center.

Pure module: stdlib + engine.data_sources / models.sourced only. No
streamlit imports.

Every external MAGI producer (FinExtract tax-return sync, PDF 1040 scan,
Data Bridge bundle import) should call ``record_magi_candidates`` instead of
writing directly to ``st.session_state["prior_year_magi"]`` — that gap-fill /
overwrite pattern was the contradictory MAGI policy (audit defect #2). This
records one ``prior_year_magi.<year>`` candidate per (year, value) pair via
``ingest.record_candidate`` and lets the resolver's default ladder (PDF >
FINEXTRACT_LIVE > BUNDLE) decide precedence, surfaced through the Command
Center's pending-review gate.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from engine.data_sources import ingest
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.paths import CANDIDATE_STORE_PATH
from engine.data_sources.resolver import magi_field_key
from models.sourced import Source


def record_magi_candidates(
    magi_by_year: dict,
    source: Source,
    detail: str,
    recorded_at: datetime,
    store_path: str | Path = CANDIDATE_STORE_PATH,
) -> int:
    """Record one ``prior_year_magi.<year>`` candidate per truthy value.

    Loads the ``CandidateStore`` from ``store_path``, records a candidate for
    each ``(year, value)`` pair in ``magi_by_year`` from ``source`` (skipping
    falsy/``None`` values; years cast to ``int``, values to ``float``), saves
    the store back to ``store_path``, and returns the count recorded.

    Never writes to ``Household`` or session state directly — callers rely on
    the resolver / Command Center to surface and confirm these candidates.
    """
    store = CandidateStore.load(store_path)
    count = 0
    for year, value in magi_by_year.items():
        if not value:
            continue
        recorded = ingest.record_candidate(
            store, magi_field_key(int(year)), float(value), source, detail, recorded_at
        )
        if recorded:
            count += 1
    if count:
        store.save(store_path)
    return count


def record_ss_fra_candidate(
    field_key: str,
    monthly_amount: float,
    source: Source,
    detail: str,
    recorded_at: datetime,
    store_path: str | Path = CANDIDATE_STORE_PATH,
) -> bool:
    """Record a Social Security full-retirement-age monthly-benefit candidate.

    ``field_key`` must be ``"your_ss_fra"`` or ``"spouse_ss_fra"``. Mirrors
    ``record_magi_candidates``: loads the ``CandidateStore`` from
    ``store_path``, records the candidate rounded to the nearest whole dollar
    (matching the int-typed Setup number_input), saves the store back, and
    never writes to ``Household`` or session state directly — callers rely on
    the resolver / Command Center to surface and confirm this candidate.
    """
    store = CandidateStore.load(store_path)
    recorded = ingest.record_candidate(
        store, field_key, float(round(monthly_amount)), source, detail, recorded_at
    )
    if recorded:
        store.save(store_path)
    return recorded
