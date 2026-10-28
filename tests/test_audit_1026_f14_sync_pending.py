"""audit-1026 F-14: Review Changes reflects candidates recorded by Sync everything.

The handler recorded candidates but the same pass read the stale pending set and
showed "All data sources reconciled" while the sidebar badge showed N pending.
Synthetic figures.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.command_center as command_center_mod
import views.setup.data_bridge as data_bridge_mod
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.scan_ingest import ScanIngestResult
from engine.pdf_import import PdfImportResult
from engine.store import get_store
from engine.store.namespaces import COMMITTED
from models.sourced import Provenance, Source, SourcedValue
from views._shared import (
    MarketQuoteSyncSummary,
    PortfolioSyncSummary,
    ScanSyncSummary,
    SsSyncSummary,
    SyncEverythingResult,
)

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
T0 = datetime(2026, 10, 1, 12, 0, 0)


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _fake_sync(_hh) -> SyncEverythingResult:
    store = CandidateStore.load()
    store.record_candidate("your_ira", 1_900_000.0, Provenance(Source.PDF, T0, "synthetic"))
    store.save()
    scan = ScanIngestResult(
        brokerage_count=0,
        form_1040_count=0,
        koinly_count=0,
        skipped_count=0,
        unrecognized_count=0,
        magi_candidates_recorded=0,
        errors=[],
        raw=PdfImportResult(),
        pdf_cache={},
    )
    return SyncEverythingResult(
        portfolio=PortfolioSyncSummary(candidates_recorded=1, server_available=True, error=None),
        ss=SsSyncSummary(candidates_recorded=0, warnings=[]),
        scan=ScanSyncSummary(result=scan, error=None),
        market_quote=MarketQuoteSyncSummary(candidates_recorded=0, error=None),
    )


def test_f14_sync_pending_shows_recorded_candidates_in_the_same_pass():
    get_store().put(
        COMMITTED,
        {"your_ira": SourcedValue(1_600_000.0, Provenance(Source.UNKNOWN, T0)).to_json()},
    )
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["your_ira"] = 1_600_000
    at.session_state["nav_page"] = "review"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.metric[0].value == "0"

    with patch.object(command_center_mod, "sync_everything", _fake_sync):
        at.button(key="sync_everything_btn").click().run()
    assert not at.exception, [e.value for e in at.exception]

    assert at.metric[0].value == "1"
    assert not any("reconciled" in s.value.lower() for s in at.success)
    assert any(b.key == "confirm_your_ira" for b in at.button)
    assert any("portfolio: 1 candidates" in i.value for i in at.info)
