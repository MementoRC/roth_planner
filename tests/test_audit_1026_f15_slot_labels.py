"""audit-1026 F-15 follow-up: ledger account labels show account + year, not the slot key."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from models.ytd_income import YTDSnapshot

SCAN_MOD = "views.ytd_income._partials._sync_scan"


def _rec(period_end: str, account_type: str) -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number="XXXX0042",
        broker="schwab",
        account_type=account_type,
        statement_period_end=period_end,
        interest_taxable_ytd=10.0,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=20.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-07-10T00:00:00+00:00",
    )


def _app() -> None:
    import streamlit as st

    from views.ytd_income._partials import _sync_scan

    _sync_scan._render_scan_review(
        st.session_state["statement_by_account"],
        {"koinly": {}, "brokerage": {}},
        {},
        {},
        "you",
        True,
    )


def _run(account_type: str) -> AppTest:
    at = AppTest.from_function(_app, default_timeout=30)
    at.session_state["statement_by_account"] = {
        "XXXX0042": _rec("2025-12-31", account_type),
        "XXXX0042@2026": _rec("2026-06-30", account_type),
    }
    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026)
    with (
        patch(f"{SCAN_MOD}.save_ledger"),
        patch(f"{SCAN_MOD}.save_ytd_snapshot"),
        patch(f"{SCAN_MOD}._load_ubs_orders_safe", return_value=[]),
    ):
        at.run()
    assert not at.exception
    return at


def test_two_years_of_an_unknown_account_get_one_confirm_control():
    at = _run("unknown")
    assert len(at.selectbox) == 1
    assert at.selectbox[0].label == "Account XXXX0042 (schwab)"
    assert "1 account(s) have no stated tax status" in at.warning[0].value


def test_taxable_slots_are_labelled_with_account_and_year_never_the_slot_key():
    at = _run("taxable")
    caption = " ".join(str(c.value) for c in at.caption)
    assert "XXXX0042 · 2026" in caption
    assert "@2026" not in caption
    assert "@2026" not in " ".join(str(i.value) for i in at.info)


def test_incomplete_slots_are_labelled_with_account_and_year():
    partial = replace(_rec("2026-06-30", "taxable"), missing_fields=("ltcg_net_ytd",))
    at = AppTest.from_function(_app, default_timeout=30)
    at.session_state["statement_by_account"] = {"XXXX0042@2026": partial}
    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026)
    with (
        patch(f"{SCAN_MOD}.save_ledger"),
        patch(f"{SCAN_MOD}.save_ytd_snapshot"),
        patch(f"{SCAN_MOD}._load_ubs_orders_safe", return_value=[]),
    ):
        at.run()
    assert not at.exception
    assert at.checkbox[0].label.startswith("Use XXXX0042 · 2026 anyway")
    assert at.checkbox[0].key == "partial_ack_XXXX0042@2026"
