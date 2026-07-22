"""Regression tests for audit-0721 Wave 2 — data-sources reconcile seam.

Covers the provenance/reconcile candidate -> committed pipeline:
- engine/portfolio_sync/client.py::_flatten_query_rows (C17, NOT reproduced)
- engine/data_sources/orchestrator.py::resolve_for_app (C18)
- engine/data_sources/resolver.py::_resolve_grants (C19)
- engine/data_sources/snapshot_ingest.py::record_snapshot_candidates (C20)
"""

from __future__ import annotations

from datetime import datetime

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.orchestrator import resolve_for_app
from engine.data_sources.resolver import GRANTS_KEY, resolve
from engine.data_sources.snapshot_ingest import record_snapshot_candidates
from engine.portfolio_sync import AccountSummary, EquityGrant, PortfolioSnapshot
from engine.portfolio_sync.client import _flatten_query_rows
from models.grants import StockGrant
from models.household import Household
from models.sourced import Provenance, Source, SourcedList

FIXED_DT = datetime(2026, 7, 21, 12, 0, 0)


class TestFlattenQueryRowsMultiInstitution:
    """C17 — NOT reproduced: _flatten_query_rows already iterates
    data["institutions"].values() (all institutions) and collects every
    batch's rows via a correct nested comprehension. Locked in here as a
    regression guard."""

    def test_two_institutions_both_survive(self) -> None:
        data = {
            "institutions": {
                "fidelity": {"rows": [{"symbol": "AAPL", "quantity": 10}]},
                "schwab": {"rows": [{"symbol": "TXN", "quantity": 5}]},
            }
        }

        rows = _flatten_query_rows(data)

        assert {"symbol": "AAPL", "quantity": 10} in rows
        assert {"symbol": "TXN", "quantity": 5} in rows
        assert len(rows) == 2


class TestResolveForAppFirstLoadSnapshotBaseline:
    """C18 — first load (no committed.json yet) with a snapshot present.

    ``reconcile_manual_edits`` must not run against a committed baseline
    that was itself derived from a snapshot-overwritten household copy: the
    raw/pristine ``session_hh`` it compares against was never overwritten,
    so the two diverge and the freshly-migrated FinExtract value gets
    spuriously relabeled Source.MANUAL with the pristine (stale/default)
    session value, corrupting the very migration it just performed.
    """

    def test_first_load_with_snapshot_migrates_without_manual_corruption(self) -> None:
        session_hh = Household()  # your_ira defaults to 0.0, pristine
        snap = PortfolioSnapshot(
            accounts=[
                AccountSummary(account_type="trad_ira", owner="you", total_value=500_000.0)
            ],
            server_available=True,
        )
        store = CandidateStore()
        choices = ChoiceMap()

        outcome = resolve_for_app(session_hh, snap, {}, store, choices, None, FIXED_DT)

        assert outcome.migrated is True
        committed_your_ira = outcome.committed_json["your_ira"]
        assert committed_your_ira["value"] == 500_000.0
        assert committed_your_ira["source"] != "MANUAL"
        assert outcome.result.household.your_ira == 500_000.0


class TestResolveGrantsValueDrift:
    """C19 — a grant matched by identity (grant_id) but with a changed
    strike/shares must still be flagged pending_review, not silently
    treated as unchanged just because its key() matches."""

    def test_same_grant_id_changed_strike_flags_pending(self) -> None:
        committed = Household()
        baseline_grant = StockGrant(
            year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="G1"
        )
        committed.grants = SourcedList(
            [baseline_grant], [Provenance(Source.FINEXTRACT_LIVE, FIXED_DT)]
        )

        store = CandidateStore()
        drifted_grant = StockGrant(
            year=2019, strike=150.0, shares=1000, expiry_year=2029, grant_id="G1"
        )
        store.record_candidate(
            GRANTS_KEY, [drifted_grant], Provenance(Source.FINEXTRACT_LIVE, FIXED_DT)
        )
        choices = ChoiceMap()

        result = resolve(committed, store, choices)

        assert GRANTS_KEY in result.pending_review
        # Committed value itself must stay frozen (freeze-until-confirm).
        assert list(result.household.grants) == [baseline_grant]

    def test_same_grant_id_same_values_not_pending(self) -> None:
        committed = Household()
        baseline_grant = StockGrant(
            year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="G1"
        )
        committed.grants = SourcedList(
            [baseline_grant], [Provenance(Source.FINEXTRACT_LIVE, FIXED_DT)]
        )

        store = CandidateStore()
        same_grant = StockGrant(
            year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="G1"
        )
        store.record_candidate(
            GRANTS_KEY, [same_grant], Provenance(Source.FINEXTRACT_LIVE, FIXED_DT)
        )
        choices = ChoiceMap()

        result = resolve(committed, store, choices)

        assert GRANTS_KEY not in result.pending_review


class TestRecordSnapshotCandidatesEmptyGrants:
    """C20 — a snapshot that legitimately clears/empties grants (all
    outstanding<=0, so the merged list is []) must still record a GRANTS_KEY
    candidate so the reconcile pipeline can surface/commit the empty state.
    A snapshot that never reported grant data at all (equity_grants == [])
    must continue to record nothing."""

    def test_snapshot_with_all_grants_exercised_still_records_empty_candidate(self) -> None:
        snap = PortfolioSnapshot(
            equity_grants=[
                EquityGrant(
                    grant_id="G1",
                    grant_type="NQO",
                    grant_date="2019-01-01",
                    shares_granted=1000,
                    outstanding=0,  # fully exercised -> merges to []
                    current_value=0.0,
                )
            ],
            server_available=True,
        )
        store = CandidateStore()

        record_snapshot_candidates(store, snap, {}, FIXED_DT)

        grants_candidates = store.candidates_for(GRANTS_KEY)
        assert len(grants_candidates) == 1
        assert grants_candidates[0].value == []

    def test_snapshot_with_no_grant_data_records_nothing(self) -> None:
        snap = PortfolioSnapshot(equity_grants=[], server_available=True)
        store = CandidateStore()

        record_snapshot_candidates(store, snap, {}, FIXED_DT)

        assert store.candidates_for(GRANTS_KEY) == []
