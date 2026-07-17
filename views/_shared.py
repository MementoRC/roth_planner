"""Shared view primitives for the Command Center input-consolidation migration.

Reusable pieces every later wave depends on:

1. ``command_center_button`` — a button that navigates to the Setup / Command
   Center page on the next rerun by setting the sidebar radio's key. Uses
   ``on_click`` so the nav key is mutated BEFORE the radio widget re-instantiates
   (setting a keyed widget's ``session_state`` slot *after* that widget already
   ran in the same script pass raises Streamlit's "cannot be modified" error;
   callbacks fire before widgets instantiate on the rerun, so they may set it).

2. ``render_canonical_field`` — a read-only display of a canonical ``Household``
   value with a provenance chip (when the value carries one) plus a jump button
   to edit it in the Command Center. Replaces the old "editable local copy that
   silently writes nowhere" widgets.

3. ``run_folder_scan`` — the SINGLE view-layer writer of
   ``st.session_state["_pdf_1040_scanned"]``. Wraps
   ``engine.data_sources.scan_ingest.scan_and_record`` (which does the actual
   scan + candidate-record + cache-persist) and writes one canonical session
   shape (W2 Part A — kills the ``_pdf_1040_scanned`` dual-writer between
   ``views/ytd_income.py`` and ``views/setup/parameters.py``).

4. ``sync_everything`` — the Command Center's "Sync everything" action (W2
   Part B). Fans out to the three already-candidate-based ingestion paths
   (FinExtract portfolio, FinExtract SS, unified PDF folder scan),
   independently error-isolated so one unreachable source never blocks the
   others. Every produced value lands PENDING via the existing candidate
   paths (``record_snapshot_candidates``, ``record_ss_fra_candidate`` via
   ``_sync_ssa_for``, ``run_folder_scan``) — the freeze-until-confirm gate is
   untouched, nothing here ever commits.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from config.loader import load_defaults
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.paths import CANDIDATE_STORE_PATH
from engine.data_sources.resolver import GRANTS_KEY
from engine.data_sources.scan_ingest import ScanIngestResult, scan_and_record
from engine.data_sources.snapshot_ingest import record_snapshot_candidates
from models.household import Household
from models.sourced import Source

# The sidebar radio's key (added in app.py). Kept here so views never hardcode it.
NAV_KEY = "nav_page"
# Exact label of the Setup page option in app.py's sidebar radio.
SETUP_PAGE = "⚙️ Setup"


def _go_to(page_label: str) -> Callable[[], None]:
    def _cb() -> None:
        st.session_state[NAV_KEY] = page_label

    return _cb


def command_center_button(*, key: str, label: str = "Edit in Command Center →") -> None:
    """A button that navigates to the Setup / Command Center page on next rerun.

    Uses ``on_click`` so the nav key is set BEFORE the radio widget re-instantiates
    (the Streamlit-safe pattern for mutating a keyed widget's state).
    """
    st.button(label, key=key, on_click=_go_to(SETUP_PAGE))


def render_canonical_field(
    caption: str, value: Any, *, key: str, fmt: Callable | None = None
) -> None:
    """Read-only display of a canonical ``Household`` value.

    Shows a provenance chip when *value* carries one (``SourcedValue`` has
    ``.prov``; a plain float/int/str does not), plus a jump button to edit it in
    the Command Center. ``getattr(value, "prov", None)`` is mandatory: ages and
    filing status are plain values (never wrapped), only balances are
    ``SourcedValue`` after ``resolve_for_app``.
    """
    prov = getattr(value, "prov", None)
    shown = fmt(value) if fmt else str(value)
    left, right = st.columns([3, 1])
    with left:
        st.caption(caption)
        st.markdown(f"### {shown}")
        if prov is not None:
            st.caption(f"source: **{prov.source}** · {prov.detail or 'no detail'}")
        else:
            st.caption("entered in Setup")
    with right:
        command_center_button(key=f"nav_{key}")


def run_folder_scan(
    folder_path: Path, *, recorded_at: datetime | None = None
) -> ScanIngestResult:
    """Scan *folder_path* and write the single canonical ``_pdf_1040_scanned``.

    Delegates the scan + 1040-candidate-record + cache-persist work entirely
    to ``scan_and_record`` (no streamlit there) and only writes
    ``st.session_state["_pdf_1040_scanned"]`` here — the one place that key is
    ever written. Only writes it when the scan found at least one Form 1040
    this pass, matching the prior per-view handlers' behavior (a scan with no
    new 1040s leaves whatever was already in session state untouched).
    """
    result = scan_and_record(folder_path, recorded_at=recorded_at)
    if result.form_1040_count:
        st.session_state["_pdf_1040_scanned"] = result.pdf_cache
    return result


# Portfolio-sourced scalar/list fields that ``record_snapshot_candidates`` may
# record (SS fields are a separate source in "Sync everything" — see
# ``_sync_ss_source`` — so they're excluded here).
_PORTFOLIO_CANDIDATE_FIELDS = ["your_ira", "spouse_ira", "your_roth", "spouse_roth", "txn_price_now", GRANTS_KEY]


def _count_candidates_from(store: CandidateStore, field_keys: list[str], source: Source) -> int:
    """Count how many of *field_keys* now carry a candidate from *source*."""
    return sum(
        1 for key in field_keys if any(c.prov.source == source for c in store.candidates_for(key))
    )


@dataclass(frozen=True)
class PortfolioSyncSummary:
    """One leg of ``sync_everything`` — the FinExtract portfolio + MAGI sync."""

    candidates_recorded: int
    server_available: bool
    error: str | None


def _sync_portfolio_source(hh: Household) -> PortfolioSyncSummary:
    """Run the FinExtract portfolio sync core and record its candidates immediately.

    Reuses ``views.setup.portfolio.sync_portfolio_from_finextract`` (the same
    core the "Sync from FinExtract" button calls) then, unlike the button
    (which relies on the NEXT ``app.get_household()`` render to record
    candidates), calls ``record_snapshot_candidates`` here so the values are
    pending right away — required for "Sync everything" to report an
    accurate count in the same pass.
    """
    from views.setup.portfolio import sync_portfolio_from_finextract

    try:
        outcome = sync_portfolio_from_finextract(hh)
    except Exception as exc:  # noqa: BLE001 -- one source failing must not abort the others
        return PortfolioSyncSummary(candidates_recorded=0, server_available=False, error=str(exc))

    if not outcome.snap.server_available:
        return PortfolioSyncSummary(
            candidates_recorded=0, server_available=False, error=outcome.snap.error
        )

    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    strikes = st.session_state.get("_user_grant_strikes") or load_defaults().get("grant_strikes", {})
    record_snapshot_candidates(store, outcome.snap, strikes, datetime.now())
    store.save(CANDIDATE_STORE_PATH)

    scalar_recorded = _count_candidates_from(store, _PORTFOLIO_CANDIDATE_FIELDS, Source.FINEXTRACT_LIVE)
    return PortfolioSyncSummary(
        candidates_recorded=scalar_recorded + outcome.magi_candidates_recorded,
        server_available=True,
        error=None,
    )


@dataclass(frozen=True)
class SsSyncSummary:
    """One leg of ``sync_everything`` — the FinExtract SS-at-FRA sync (per person)."""

    candidates_recorded: int
    warnings: list[str]


def _sync_ss_source() -> SsSyncSummary:
    """Sync SS-at-FRA for both people (skipping spouse when filing Single).

    Reuses ``views.setup.parameters._sync_ssa_for`` — the same per-person
    callable the individual "Sync SS from FinExtract" buttons call — which
    already records through ``record_ss_fra_candidate`` (W2 Part C).
    """
    from views.setup.parameters import _sync_ssa_for

    warnings: list[str] = []
    recorded = 0
    is_single = st.session_state.get("filing_status", "MFJ") == "Single"
    people = [("you", "your_fra_age")] + ([] if is_single else [("spouse", "spouse_fra_age")])
    for owner, fra_key in people:
        fra_age = st.session_state.get(fra_key, 67)
        try:
            warning = _sync_ssa_for(owner, fra_age)
        except Exception as exc:  # noqa: BLE001 -- one source failing must not abort the others
            warning = f"SS sync ({owner}) failed: {exc}"
        if warning:
            warnings.append(warning)
        else:
            recorded += 1
    return SsSyncSummary(candidates_recorded=recorded, warnings=warnings)


@dataclass(frozen=True)
class ScanSyncSummary:
    """One leg of ``sync_everything`` — the unified PDF-folder scan."""

    result: ScanIngestResult | None
    error: str | None


def _sync_scan_source() -> ScanSyncSummary:
    """Scan the canonical statement folder via ``run_folder_scan``, if configured."""
    from engine.brokerage_statement_pdf import load_statement_folder_path

    folder = load_statement_folder_path()
    if not folder:
        return ScanSyncSummary(result=None, error="no folder configured")
    try:
        result = run_folder_scan(Path(folder))
    except Exception as exc:  # noqa: BLE001 -- one source failing must not abort the others
        return ScanSyncSummary(result=None, error=str(exc))
    return ScanSyncSummary(result=result, error=None)


@dataclass(frozen=True)
class SyncEverythingResult:
    """Combined summary of the Command Center's "Sync everything" action.

    Every produced value lands PENDING via the existing candidate paths —
    the freeze-until-confirm gate is unchanged and nothing here ever commits.
    """

    portfolio: PortfolioSyncSummary
    ss: SsSyncSummary
    scan: ScanSyncSummary


def sync_everything(hh: Household) -> SyncEverythingResult:
    """Fan out to the portfolio/SS/scan sources, independently error-isolated.

    Each source is wrapped so a failure in one (e.g. FinExtract unreachable)
    never prevents the others from running. Records everything through the
    existing candidate paths only — never commits, never writes ``Household``
    directly.
    """
    return SyncEverythingResult(
        portfolio=_sync_portfolio_source(hh),
        ss=_sync_ss_source(),
        scan=_sync_scan_source(),
    )
