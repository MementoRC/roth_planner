"""Portfolio tab — the FinExtract sync core, and the thin tab-body composition.

The equity-grants table (and its ``GRANTS_KEY`` governance card) moved into
``views/setup/_partials/_options.py:render_options_partial`` as of Task 5 of the
ui-shell-theme-toggle plan. The Sync-from-FinExtract button, the read-only
accounts/holdings tables, and the Account Type Overrides expander moved into
``views/setup/_partials/_portfolio.py:render_portfolio_partial`` as of Task 6 — this
module now holds only ``sync_portfolio_from_finextract`` (the sync core,
also reused directly by ``views._shared.sync_everything``) and
``render_portfolio_tab``, which composes the two partials.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import streamlit as st

from engine.data_sources.record import record_magi_candidates
from engine.portfolio_sync import (
    EmptySnapshotWriteRefusedError,
    MagiSnapshot,
    PortfolioSnapshot,
    apply_dividends_rollup,
    apply_magi,
    apply_option_exercises,
    fetch_dividends_rollup,
    fetch_magi,
    fetch_option_exercises_with_cache,
    fetch_portfolio,
    fetch_ytd_snapshot,
    save_snapshot,
    save_ytd_snapshot,
)
from models.household import Household
from models.sourced import Source
from models.ytd_income import YTDSnapshot
from views.setup._partials import render_options_partial, render_portfolio_partial


@dataclass(frozen=True)
class PortfolioSyncOutcome:
    """Outcome of one FinExtract portfolio+MAGI+YTD sync pass.

    Extracted from the "Sync from FinExtract" button handler (W2 Part B) so
    ``views._shared.sync_everything`` can drive the identical fetch/save/
    candidate-record sequence without duplicating it. Balances
    (your_ira/spouse_ira/your_roth/spouse_roth/txn_price_now/grants) are
    deliberately NOT written to session_state or recorded as candidates
    here — ``app.get_household()`` records the saved snapshot as
    FINEXTRACT_LIVE candidates via ``record_snapshot_candidates`` on the
    next render (unchanged from prior behavior); ``sync_everything`` records
    them immediately via the same helper so they land pending right away.
    """

    snap: PortfolioSnapshot
    magi_candidates_recorded: int
    ytd_synced: bool
    dividend_history_synced: bool
    option_exercises_synced: bool


def sync_portfolio_from_finextract(hh: Household) -> PortfolioSyncOutcome:
    """Fetch + save a FinExtract portfolio snapshot; record MAGI candidates; sync YTD.

    Reproduces exactly what the "Sync from FinExtract" button used to do
    inline. When the server is unavailable, returns immediately with an
    all-false/zero outcome (the caller inspects ``outcome.snap.server_available``
    / ``outcome.snap.error``).
    """
    snap = fetch_portfolio(
        account_type_overrides=st.session_state.get("account_type_overrides") or None,
    )
    if not snap.server_available:
        return PortfolioSyncOutcome(
            snap=snap,
            magi_candidates_recorded=0,
            ytd_synced=False,
            dividend_history_synced=False,
            option_exercises_synced=False,
        )

    # Merge dividend history into holdings before saving snapshot
    div_rollup = fetch_dividends_rollup()
    if div_rollup.server_available:
        snap = apply_dividends_rollup(snap, div_rollup)
    try:
        save_snapshot(snap)
    except EmptySnapshotWriteRefusedError as exc:
        # PS-2b: the write was refused to protect a populated cache. Route into
        # the same failure branch the caller already handles for an unreachable
        # server, rather than reporting a sync that did not persist.
        snap.server_available = False
        snap.error = str(exc)
        return PortfolioSyncOutcome(
            snap=snap,
            magi_candidates_recorded=0,
            ytd_synced=False,
            dividend_history_synced=False,
            option_exercises_synced=False,
        )
    st.session_state.portfolio_snapshot = snap

    # MAGI 2-year history from FinExtract (IRMAA lookback anchor). Records
    # candidates for Command Center review instead of gap-filling
    # session_state directly (audit defect #2).
    magi_recorded = 0
    try:
        plan_year = datetime.now(UTC).year
        magi_snap = MagiSnapshot(fetched_at=datetime.now(UTC))
        for offset in (1, 2):  # batchTaxYear-1 and batchTaxYear-2 (2-year coverage shipped)
            apply_magi(magi_snap, fetch_magi(plan_year - offset))
        if magi_snap.prior_year_magi:
            magi_recorded = record_magi_candidates(
                magi_snap.prior_year_magi,
                Source.FINEXTRACT_LIVE,
                "FinExtract tax return",
                datetime.now(),
            )
    except Exception:  # noqa: BLE001 — sync is best-effort, never block on MAGI failure
        pass

    # Also sync YTD income data. fetch_ytd_snapshot only pings /status for
    # freshness/manually_entered metadata (investment income is
    # brokerage-PDF-sourced, not FinExtract-sourced -- see ytd.py docstring).
    # Overlay that metadata onto the PREVIOUSLY-PERSISTED snapshot instead of
    # starting from a fresh all-zero YTDSnapshot() (audit-0805 C32) -- a
    # fresh snapshot silently wiped every manually-entered/statement-sourced
    # field on every sync.
    ytd_status = fetch_ytd_snapshot()
    prev_ytd = st.session_state.get("ytd_snapshot") or YTDSnapshot()
    ytd_snap = prev_ytd.overlay(
        manually_entered=ytd_status.manually_entered,
        snapshot_date=ytd_status.snapshot_date,
    )
    # Phase: option exercises — prefer cache equity_sales, fall back to /query
    exercises = fetch_option_exercises_with_cache(snap)
    if exercises.server_available:
        ytd_snap = apply_option_exercises(ytd_snap, exercises, hh)
        if exercises.captured_at:
            st.session_state["exercises_captured_at"] = exercises.captured_at
    # ytd_status.snapshot_date (not ytd_snap's, which now also reflects the
    # exercises merge) is the one true signal of whether /status actually
    # responded -- ytd.py only stamps it on a successful ping.
    ytd_synced = bool(ytd_status.snapshot_date)
    if ytd_synced:
        st.session_state.ytd_snapshot = ytd_snap
        save_ytd_snapshot(ytd_snap)

    return PortfolioSyncOutcome(
        snap=snap,
        magi_candidates_recorded=magi_recorded,
        ytd_synced=ytd_synced,
        dividend_history_synced=div_rollup.server_available,
        option_exercises_synced=exercises.server_available,
    )


def render_portfolio_tab(hh: Household) -> None:
    """Extracted from setup.py render() — portfolio tab body.

    Composes the two Portfolio-tab partials (Task 6): the sync
    button/accounts-holdings-tables/overrides partial, then the
    equity-grants/stock-price partial (Task 5), in the same relative order
    as the pre-Task-6 inline body.
    """
    render_portfolio_partial(hh, st)
    render_options_partial(hh, st)
