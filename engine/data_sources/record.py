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

from collections.abc import Iterable
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
    if field_key not in ("your_ss_fra", "spouse_ss_fra"):
        raise ValueError(f"field_key must be 'your_ss_fra' or 'spouse_ss_fra', got {field_key!r}")
    store = CandidateStore.load(store_path)
    recorded = ingest.record_candidate(
        store, field_key, float(round(monthly_amount)), source, detail, recorded_at
    )
    if recorded:
        store.save(store_path)
    return recorded


def record_ira_balance_candidate(
    field_key: str,
    balance: float,
    detail: str,
    recorded_at: datetime,
    store_path: str | Path = CANDIDATE_STORE_PATH,
) -> bool:
    """Record a statement-PDF pre-tax IRA balance as a ``Source.PDF`` candidate.

    ``field_key`` must be ``"your_ira"`` or ``"spouse_ira"``. Mirrors
    ``record_ss_fra_candidate``: rounds to the nearest whole dollar (the
    int-typed Setup number_input), saves the store back, and never writes to
    ``Household`` or session state directly -- the Command Center's
    pending-review gate decides whether it replaces the committed value.
    """
    if field_key not in ("your_ira", "spouse_ira"):
        raise ValueError(f"field_key must be 'your_ira' or 'spouse_ira', got {field_key!r}")
    return _record_pdf_balance(field_key, balance, detail, recorded_at, store_path)


def record_roth_balance_candidate(
    field_key: str,
    balance: float,
    detail: str,
    recorded_at: datetime,
    store_path: str | Path = CANDIDATE_STORE_PATH,
) -> bool:
    """Record a statement-PDF Roth IRA balance as a ``Source.PDF`` candidate.

    ``field_key`` must be ``"your_roth"`` or ``"spouse_roth"``. Same contract
    as :func:`record_ira_balance_candidate`.
    """
    if field_key not in ("your_roth", "spouse_roth"):
        raise ValueError(f"field_key must be 'your_roth' or 'spouse_roth', got {field_key!r}")
    return _record_pdf_balance(field_key, balance, detail, recorded_at, store_path)


def _record_pdf_balance(
    field_key: str,
    balance: float,
    detail: str,
    recorded_at: datetime,
    store_path: str | Path,
) -> bool:
    store = CandidateStore.load(store_path)
    recorded = ingest.record_candidate(
        store, field_key, float(round(balance)), Source.PDF, detail, recorded_at
    )
    if recorded:
        store.save(store_path)
    return recorded


_BALANCE_FIELDS: dict[str, tuple[str, str]] = {
    "you": ("your_ira", "your_roth"),
    "spouse": ("spouse_ira", "spouse_roth"),
}


def discard_pdf_balance_candidates(
    slots: Iterable[str], store_path: str | Path | None = None
) -> int:
    """Drop the statement-PDF IRA/Roth balance candidates of each person in *slots*.

    Called after a bundle import replaced those people's accounts: a PDF
    candidate was computed from statement accounts that are no longer in the
    portfolio, so offering it in Review Changes would bring back a figure the
    import just superseded. Other sources' candidates are kept. Returns how
    many candidates were dropped. *store_path* defaults to the module's
    ``CANDIDATE_STORE_PATH`` read at call time, not a baked-in default.
    """
    if store_path is None:
        store_path = CANDIDATE_STORE_PATH
    store = CandidateStore.load(store_path)
    dropped = 0
    for slot in slots:
        for field_key in _BALANCE_FIELDS.get(slot, ()):
            if store.discard_source(field_key, Source.PDF):
                dropped += 1
    if dropped:
        store.save(store_path)
    return dropped


def record_stock_quote_candidate(
    price: float,
    *,
    detail: str = "Yahoo Finance quote",
    recorded_at: datetime | None = None,
    store_path: str | Path = CANDIDATE_STORE_PATH,
) -> bool:
    """Record a fetched employer-stock market quote as a ``stock_price_now`` candidate.

    Mirrors ``record_ss_fra_candidate``: loads the ``CandidateStore`` from
    ``store_path``, records the candidate from ``Source.MARKET_QUOTE`` rounded
    to the nearest cent (it's a share price, not a whole-dollar amount), saves
    the store back, and never writes to ``Household`` or session state
    directly — callers rely on the resolver / Command Center to surface and
    confirm this candidate.
    """
    store = CandidateStore.load(store_path)
    when = recorded_at or datetime.now()
    recorded = ingest.record_candidate(
        store, "stock_price_now", round(float(price), 2), Source.MARKET_QUOTE, detail, when
    )
    if recorded:
        store.save(store_path)
    return recorded
