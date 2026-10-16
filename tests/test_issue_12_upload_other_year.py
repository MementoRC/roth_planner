"""Issue #12: a taxable statement from another tax year, imported from an upload.

The 2025 Fidelity statement is a committed synthetic fixture. Against a 2026
YTD snapshot its figures must not be counted, the review must say so (the
W9 other-year note) instead of listing the account as "Counted toward YTD
income", and applying must leave the snapshot's income fields alone. A
current-year taxable statement is the control. All figures are invented.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.pdf_import import scan_pdf_documents
from engine.pdf_ledger import (
    PdfLedger,
    off_year_contributions,
    statement_in_tax_year,
    write_brokerage_contribution,
)
from models.ytd_income import YTDSnapshot

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic" / "fidelity_taxable_2025.pdf"
SCAN_MOD = "views.ytd_income._partials._sync_scan"


def _prior_year_record() -> BrokerageStatementRecord:
    result = scan_pdf_documents([(FIXTURE.name, FIXTURE.read_bytes())])
    assert result.errors == []
    [rec] = result.brokerage_records
    return rec


def _current_year_record() -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number="XXXX0002",
        broker="vanguard",
        account_type="taxable",
        statement_period_end="2026-06-30",
        interest_taxable_ytd=10.0,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=20.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-07-10T00:00:00+00:00",
    )


def _ledger_with(rec: BrokerageStatementRecord) -> PdfLedger:
    return write_brokerage_contribution({"koinly": {}, "brokerage": {}}, "you", rec)


def _review_app() -> None:
    import streamlit as st

    from views.ytd_income._partials import _sync_scan

    _sync_scan._render_scan_review(
        st.session_state["statement_by_account"],
        st.session_state["test_ledger"],
        {},
        {},
        "you",
        True,
    )


def _run_review(
    rec: BrokerageStatementRecord, *, acknowledge: bool = False, click_apply: bool = False
) -> AppTest:
    at = AppTest.from_function(_review_app, default_timeout=30)
    at.session_state["statement_by_account"] = {rec.account_number: rec}
    at.session_state["test_ledger"] = _ledger_with(rec)
    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026)
    with (
        patch(f"{SCAN_MOD}.save_ledger"),
        patch(f"{SCAN_MOD}.save_ytd_snapshot"),
        patch(f"{SCAN_MOD}._load_ubs_orders_safe", return_value=[]),
    ):
        at.run()
        assert not at.exception
        if acknowledge:
            # Fidelity statements carry no realized gains, so the record is
            # held until the user opts in to using it.
            at.checkbox(key=f"partial_ack_{rec.account_number}").check().run()
            assert not at.exception
        if click_apply:
            at.button(key="apply_statements_btn").click().run()
            assert not at.exception
    return at


def _texts(elements: object) -> list[str]:
    return [str(e.value) for e in elements]  # type: ignore[attr-defined]


class TestFixture:
    def test_fixture_is_a_prior_year_taxable_account(self) -> None:
        rec = _prior_year_record()
        assert (rec.broker, rec.account_type, rec.account_number) == (
            "fidelity",
            "taxable",
            "Z12-345678",
        )
        assert rec.statement_period_end == "2025-12-31"
        assert rec.dividends_taxable_ytd == 3000.0
        assert rec.dividends_tax_exempt_ytd == 500.0


class TestEngineHelpers:
    def test_statement_in_tax_year(self) -> None:
        assert not statement_in_tax_year(_prior_year_record(), 2026)
        assert statement_in_tax_year(_prior_year_record(), 2025)
        assert statement_in_tax_year(_current_year_record(), 2026)
        assert statement_in_tax_year(_prior_year_record(), None)

    def test_off_year_note_can_be_limited_to_statements(self) -> None:
        ledger = _ledger_with(_prior_year_record())
        assert len(off_year_contributions(ledger, 2026)) == 1
        assert len(off_year_contributions(ledger, 2026, brokerage=False)) == 0
        [note] = off_year_contributions(ledger, 2026, koinly=False)
        assert "Z12-345678" in note
        assert "2025" in note


class TestUploadReviewPriorYear:
    def test_held_statement_says_up_front_it_will_not_be_counted(self) -> None:
        at = _run_review(_prior_year_record())
        notes = _texts(at.info)
        assert any("Z12-345678" in n and "2025" in n and "not counted" in n for n in notes), notes

    def test_other_year_note_is_shown_once_the_statement_is_used(self) -> None:
        at = _run_review(_prior_year_record(), acknowledge=True)
        notes = _texts(at.info)
        assert any("Z12-345678" in n and "2025" in n and "not counted" in n for n in notes), notes

    def test_account_is_not_listed_as_counted(self) -> None:
        at = _run_review(_prior_year_record(), acknowledge=True)
        captions = _texts(at.caption)
        assert not any("Counted toward YTD income" in c for c in captions), captions

    def test_apply_leaves_the_snapshot_income_fields_unchanged(self) -> None:
        # Regression lock: the year-gated totals already excluded the figures.
        at = _run_review(_prior_year_record(), acknowledge=True, click_apply=True)
        snap = at.session_state["ytd_snapshot"]
        assert snap.ordinary_dividends_ytd == 0.0
        assert snap.interest_ytd == 0.0
        assert snap.tax_exempt_interest_ytd == 0.0
        assert snap.magi_ytd == 0.0

    def test_apply_does_not_claim_the_account_was_applied(self) -> None:
        at = _run_review(_prior_year_record(), acknowledge=True, click_apply=True)
        successes = _texts(at.success)
        assert not any(s.startswith("Applied 1 taxable") for s in successes), successes


class TestUploadReviewCurrentYearControl:
    def test_current_year_statement_is_listed_as_counted(self) -> None:
        at = _run_review(_current_year_record())
        captions = _texts(at.caption)
        assert any("Counted toward YTD income: XXXX0002" in c for c in captions), captions
        assert not any("not counted" in n for n in _texts(at.info))

    def test_current_year_statement_is_applied(self) -> None:
        at = _run_review(_current_year_record(), click_apply=True)
        snap = at.session_state["ytd_snapshot"]
        assert snap.ordinary_dividends_ytd == pytest.approx(20.0)
        assert snap.interest_ytd == pytest.approx(10.0)
