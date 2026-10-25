"""Each data store reads and writes its namespace in the document store (PR 2).

All values are synthetic.
"""

from __future__ import annotations

import pytest

from engine.portfolio_sync.portfolio import (
    CorruptPortfolioCacheError,
    load_snapshot,
    save_snapshot,
)
from engine.portfolio_sync.shapes import (
    AccountSummary,
    PortfolioSnapshot,
    SSABenefitEstimate,
    SSASnapshot,
)
from engine.portfolio_sync.social_security import (
    CorruptSSACacheError,
    load_ssa_snapshot,
    save_ssa_snapshot,
)
from engine.portfolio_sync.ytd import load_ytd_snapshot, save_ytd_snapshot
from engine.store import SINGLE_KEY, CorruptStoreError
from engine.store.namespaces import PORTFOLIO, SSA, YTD
from models.ytd_income import YTDSnapshot


def _portfolio(total: float) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[AccountSummary(account_type="trad_ira", owner="you", total_value=total)],
        server_available=True,
    )


class TestPortfolioStore:
    def test_snapshot_round_trips_through_the_store(self, store):
        save_snapshot(_portfolio(1_000.0))
        assert store.get(PORTFOLIO)["accounts"][0]["total_value"] == 1_000.0
        loaded = load_snapshot()
        assert loaded is not None
        assert loaded.accounts[0].total_value == 1_000.0

    def test_absent_reads_none(self):
        assert load_snapshot() is None

    def test_corrupt_row_reads_none_and_refuses_a_save(self, store, corrupt_row):
        payload = corrupt_row(PORTFOLIO)
        assert load_snapshot() is None
        with pytest.raises(CorruptPortfolioCacheError, match="portfolio"):
            save_snapshot(_portfolio(1.0))
        assert store.backend.get(PORTFOLIO, SINGLE_KEY).payload == payload

    def test_non_object_document_refuses_a_save(self, store):
        store.put(PORTFOLIO, [1, 2])
        with pytest.raises(CorruptPortfolioCacheError, match="portfolio"):
            save_snapshot(_portfolio(1.0))
        assert store.get(PORTFOLIO) == [1, 2]

    def test_finextract_sections_survive_a_save(self, store):
        store.put(
            PORTFOLIO,
            {
                "accounts": [],
                "equity_sales": {"lots": [{"id": "L1"}], "executions": []},
                "sources": {"order_detail_summary": {"captured_at": "2026-01-01"}},
            },
        )
        save_snapshot(_portfolio(5.0))
        saved = store.get(PORTFOLIO)
        assert saved["equity_sales"]["lots"] == [{"id": "L1"}]
        assert saved["sources"]["order_detail_summary"]["captured_at"] == "2026-01-01"


class TestYtdStore:
    def test_round_trip(self, store):
        save_ytd_snapshot(YTDSnapshot(wages_ytd=12_000.0))
        assert store.has(YTD)
        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.wages_ytd == 12_000.0

    def test_absent_and_corrupt_read_none(self, corrupt_row):
        assert load_ytd_snapshot() is None
        corrupt_row(YTD)
        assert load_ytd_snapshot() is None

    def test_save_refuses_to_replace_a_corrupt_row(self, store, corrupt_row):
        payload = corrupt_row(YTD)
        with pytest.raises(CorruptStoreError, match="ytd"):
            save_ytd_snapshot(YTDSnapshot())
        assert store.backend.get(YTD, SINGLE_KEY).payload == payload


def _ssa(amount: float) -> SSASnapshot:
    return SSASnapshot(
        estimates=[SSABenefitEstimate(67, "2034-01-01", "retirement", amount)],
        server_available=True,
    )


class TestSsaStore:
    def test_owners_share_one_document(self, store):
        save_ssa_snapshot(_ssa(1_000.0), owner="you")
        save_ssa_snapshot(_ssa(800.0), owner="spouse")
        assert set(store.get(SSA)) == {"you", "spouse"}
        you = load_ssa_snapshot(owner="you")
        assert you is not None
        assert you.estimates[0].monthly_amount == 1_000.0

    def test_corrupt_row_reads_none_and_refuses_a_save(self, store, corrupt_row):
        payload = corrupt_row(SSA)
        assert load_ssa_snapshot(owner="you") is None
        with pytest.raises(CorruptSSACacheError):
            save_ssa_snapshot(_ssa(1.0), owner="you")
        assert store.backend.get(SSA, SINGLE_KEY).payload == payload

    def test_non_object_document_is_kept(self, store):
        store.put(SSA, [1, 2])
        assert load_ssa_snapshot(owner="you") is None
        with pytest.raises(CorruptSSACacheError):
            save_ssa_snapshot(_ssa(1.0), owner="you")
        assert store.get(SSA) == [1, 2]
