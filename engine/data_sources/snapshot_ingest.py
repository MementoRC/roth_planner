"""Pure replication of app.py's snapshot-derived sourced-field overwrites.

Pure module: stdlib + models/ + engine.upload_merge + engine.dividend_forecast
+ engine.portfolio_sync (types only) + engine.data_sources.* only. No
streamlit imports.

This module exists to let engine/data_sources/orchestrator.py build a
numerically-identical migration baseline (``apply_snapshot_overwrite``) and,
going forward, to route snapshot-derived values through the candidate
arbiter instead of clobbering Household attributes directly
(``record_snapshot_candidates``) — fixing the exact clobber bug the
Setup / Command Center exists to prevent (a fresh FinExtract sync silently
overwriting a value the user already confirmed).
"""

from __future__ import annotations

from datetime import datetime

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.ingest import record_candidate
from engine.data_sources.resolver import GRANTS_KEY
from engine.dividend_forecast import forecast_portfolio
from engine.portfolio_sync import PortfolioSnapshot, positions_for_forecast_multi
from engine.upload_merge import derive_ira_balances, derive_roth_balances
from models.grants import StockGrant
from models.household import GrowthProfile, Household
from models.sourced import Source

_DETAIL = "FinExtract live"


def _merge_snapshot_grants(
    snap: PortfolioSnapshot, strikes: dict
) -> tuple[list[StockGrant], list[tuple[int, int]]]:
    """Merge FinExtract equity grants with user-supplied strikes.

    Mirrors app.py's original grant-merge block exactly: grants with
    outstanding<=0 (fully exercised) are dropped silently; grants with
    outstanding shares but no configured strike (>0) are dropped but
    reported via the returned dropped_missing_strike list so callers can
    still warn instead of silently hiding a real position.
    """
    merged: list[StockGrant] = []
    dropped_missing_strike: list[tuple[int, int]] = []
    for g in snap.equity_grants:
        year = int(g.grant_date.split("-")[0]) if g.grant_date else 0
        if g.outstanding <= 0:
            continue
        strike = float(strikes.get(str(year), 0.0))
        if strike <= 0:
            dropped_missing_strike.append((year, g.outstanding))
            continue
        merged.append(
            StockGrant(
                year=year,
                strike=strike,
                shares=g.outstanding,
                expiry_year=year + 10,
                grant_id=g.grant_id,
            )
        )
    return merged, dropped_missing_strike


def apply_snapshot_overwrite(hh: Household, snap: PortfolioSnapshot, strikes: dict) -> None:
    """Replicate app.py's OLD sourced-field overwrites from a portfolio snapshot.

    Used ONLY to build the migration baseline so first-load numbers are
    identical to pre-Setup/Command-Center behavior. Does NOT set growth
    profiles — see ``derive_snapshot_growth``.
    """
    your_pretax, spouse_pretax = derive_ira_balances(snap)
    if your_pretax > 0:
        hh.your_ira = your_pretax
    if spouse_pretax > 0:
        hh.spouse_ira = spouse_pretax

    your_roth_bal, spouse_roth_bal = derive_roth_balances(snap)
    if your_roth_bal > 0:
        hh.your_roth = your_roth_bal
    if spouse_roth_bal > 0:
        hh.spouse_roth = spouse_roth_bal

    if snap.txn_shares_held > 0 and snap.txn_shares_value > 0:
        hh.txn_price_now = snap.txn_shares_value / snap.txn_shares_held

    if snap.equity_grants:
        merged_grants, _dropped = _merge_snapshot_grants(snap, strikes)
        if merged_grants:
            hh.grants = merged_grants


def derive_snapshot_growth(hh: Household, snap: PortfolioSnapshot) -> list[str]:
    """Replicate ONLY the growth-profile derivations from app.py's snapshot
    block. Growth is NOT a sourced field, so app.py keeps calling this every
    load (not gated by committed-baseline / candidate resolution).

    Returns a list of human-readable notes (may be empty).
    """
    notes: list[str] = []

    your_pretax, spouse_pretax = derive_ira_balances(snap)
    if your_pretax > 0:
        hh.your_ira_growth = GrowthProfile(default_rate=snap.pretax_weighted_return_for("you"))
        notes.append("your IRA growth derived from portfolio sync")
    if spouse_pretax > 0:
        hh.spouse_ira_growth = GrowthProfile(default_rate=snap.pretax_weighted_return_for("spouse"))
        notes.append("spouse IRA growth derived from portfolio sync")

    your_roth_bal, spouse_roth_bal = derive_roth_balances(snap)
    if your_roth_bal > 0:
        your_roth_accounts = [a for a in snap.accounts if a.owner == "you" and a.is_roth]
        your_roth_return = (
            sum(a.total_value * a.weighted_return for a in your_roth_accounts) / your_roth_bal
            if your_roth_accounts
            else hh.growth_rate
        )
        hh.your_roth_growth = GrowthProfile(default_rate=your_roth_return)
        notes.append("your Roth growth derived from portfolio sync")
    if spouse_roth_bal > 0:
        spouse_roth_accounts = [a for a in snap.accounts if a.owner == "spouse" and a.is_roth]
        spouse_roth_return = (
            sum(a.total_value * a.weighted_return for a in spouse_roth_accounts) / spouse_roth_bal
            if spouse_roth_accounts
            else hh.growth_rate
        )
        hh.spouse_roth_growth = GrowthProfile(default_rate=spouse_roth_return)
        notes.append("spouse Roth growth derived from portfolio sync")

    brokerage_accounts = snap.brokerage_accounts
    brokerage_total = snap.brokerage_total
    if brokerage_accounts and brokerage_total > 0:
        fcst = forecast_portfolio(
            positions_for_forecast_multi(brokerage_accounts),
            total_balance=brokerage_total,
        )
        hh.brokerage_growth = GrowthProfile(
            default_rate=snap.brokerage_weighted_return,
            yield_rate=fcst.yield_rate,
            qualified_fraction=fcst.qualified_fraction,
        )
        notes.append("brokerage growth/yield derived from portfolio sync")

    return notes


def record_snapshot_candidates(
    store: CandidateStore,
    snap: PortfolioSnapshot,
    strikes: dict,
    recorded_at: datetime,
) -> list[tuple[int, int]]:
    """Record the snapshot-derived sourced values as FINEXTRACT_LIVE
    candidates instead of overwriting Household directly.

    Returns dropped_missing_strike so callers can still surface the same
    "grant with no configured strike" warning app.py used to emit inline.
    """
    your_pretax, spouse_pretax = derive_ira_balances(snap)
    if your_pretax > 0:
        record_candidate(store, "your_ira", your_pretax, Source.FINEXTRACT_LIVE, _DETAIL, recorded_at)
    if spouse_pretax > 0:
        record_candidate(
            store, "spouse_ira", spouse_pretax, Source.FINEXTRACT_LIVE, _DETAIL, recorded_at
        )

    your_roth_bal, spouse_roth_bal = derive_roth_balances(snap)
    if your_roth_bal > 0:
        record_candidate(
            store, "your_roth", your_roth_bal, Source.FINEXTRACT_LIVE, _DETAIL, recorded_at
        )
    if spouse_roth_bal > 0:
        record_candidate(
            store, "spouse_roth", spouse_roth_bal, Source.FINEXTRACT_LIVE, _DETAIL, recorded_at
        )

    if snap.txn_shares_held > 0 and snap.txn_shares_value > 0:
        price = snap.txn_shares_value / snap.txn_shares_held
        record_candidate(store, "txn_price_now", price, Source.FINEXTRACT_LIVE, _DETAIL, recorded_at)

    dropped_missing_strike: list[tuple[int, int]] = []
    if snap.equity_grants:
        # ``snap.equity_grants`` non-empty means the snapshot DID report grant
        # data — always record the merge result (even []) as a candidate so a
        # snapshot that legitimately clears/empties grants (all exercised) is
        # still recordable/committable, distinct from "no snapshot data at
        # all" (the outer guard above) (audit-0721 C20).
        merged_grants, dropped_missing_strike = _merge_snapshot_grants(snap, strikes)
        record_candidate(
            store, GRANTS_KEY, merged_grants, Source.FINEXTRACT_LIVE, _DETAIL, recorded_at
        )

    return dropped_missing_strike
