"""Each data store reads and writes its namespace in the document store (PR 2).

All values are synthetic.
"""

from __future__ import annotations

import pytest

from engine.koinly_report_pdf import KoinlyReport, load_koinly_report, save_koinly_report
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
from engine.ssa_statement_pdf import (
    SsaStatement,
    load_ssa_pdf_estimates,
    save_ssa_pdf_estimates,
)
from engine.store import SINGLE_KEY, CorruptStoreError
from engine.store.namespaces import KOINLY, PORTFOLIO, SSA, SSA_PDF, TAX_PDF, YTD
from engine.tax import load_prior_year_agi, load_prior_year_federal_tax
from engine.tax_return_pdf import (
    Form1040Record,
    load_pdf_tax_cache_raw,
    load_pdf_tax_records,
    save_pdf_tax_records,
)
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


def _form_1040(agi: float) -> dict[int, Form1040Record]:
    return {
        2024: Form1040Record(
            tax_year=2024,
            agi=agi,
            tax_exempt_interest=0.0,
            taxable_ss=0.0,
            qualified_dividends=0.0,
            ordinary_dividends=0.0,
            feie=0.0,
            magi=agi,
            filing_status=None,
            captured_at="2026-01-01T00:00:00",
        )
    }


class TestTaxPdfStore:
    def test_round_trip_and_raw_reader(self, store):
        save_pdf_tax_records(_form_1040(50_000.0))
        assert set(store.get(TAX_PDF)) == {"2024"}
        assert load_pdf_tax_records()[2024].agi == 50_000.0
        assert load_pdf_tax_cache_raw()["2024"]["agi"] == 50_000.0

    def test_tax_reads_prior_year_figures_from_the_store(self, store):
        store.put(TAX_PDF, {"2024": {"agi": 70_000.0, "total_federal_tax": 6_000.0}})
        assert load_prior_year_agi(2024) == 70_000.0
        assert load_prior_year_federal_tax(2024) == 6_000.0
        assert load_prior_year_federal_tax() == 6_000.0

    def test_absent_and_corrupt_read_empty(self, corrupt_row):
        assert load_pdf_tax_records() == {}
        assert load_prior_year_federal_tax() == 0.0
        corrupt_row(TAX_PDF)
        assert load_pdf_tax_records() == {}
        assert load_pdf_tax_cache_raw() is None
        assert load_prior_year_agi(2024) is None
        assert load_prior_year_federal_tax(2024) == 0.0


def _koinly() -> KoinlyReport:
    return KoinlyReport(
        tax_year=2026,
        crypto_stcg=10.0,
        crypto_ltcg=20.0,
        crypto_income=5.0,
        captured_at="2026-07-01T00:00:00",
    )


def _ssa_statement() -> SsaStatement:
    return SsaStatement(
        owner_key=None,
        statement_date="2026-01-15",
        fra_age=67,
        estimates={62: 1_000.0, 67: 1_500.0, 70: 1_900.0},
    )


class TestKoinlyStore:
    def test_round_trip(self, store):
        save_koinly_report(_koinly())
        assert store.has(KOINLY)
        assert load_koinly_report() == _koinly()

    def test_absent_and_corrupt_read_none(self, corrupt_row):
        assert load_koinly_report() is None
        corrupt_row(KOINLY)
        assert load_koinly_report() is None


class TestSsaPdfStore:
    def test_owners_share_one_document(self, store):
        save_ssa_pdf_estimates("you", _ssa_statement())
        save_ssa_pdf_estimates("spouse", _ssa_statement())
        assert set(store.get(SSA_PDF)) == {"you", "spouse"}
        loaded = load_ssa_pdf_estimates("you")
        assert loaded is not None
        assert loaded.estimates[67] == 1_500.0

    def test_corrupt_row_reads_none_and_refuses_a_save(self, store, corrupt_row):
        payload = corrupt_row(SSA_PDF)
        assert load_ssa_pdf_estimates("you") is None
        with pytest.raises(CorruptStoreError):
            save_ssa_pdf_estimates("you", _ssa_statement())
        assert store.backend.get(SSA_PDF, SINGLE_KEY).payload == payload

    def test_owner_is_still_validated(self):
        with pytest.raises(ValueError, match="owner"):
            save_ssa_pdf_estimates("household", _ssa_statement())
