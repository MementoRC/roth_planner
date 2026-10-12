"""Redesign PR 1b: every import that writes a hand-enterable YTD field settles marks."""

import pytest

import views.ytd_income._partials._sync_scan as sync_scan_mod
from engine.pdf_ledger import (
    apply_koinly_totals,
    latest_brokerage_period_end,
    latest_koinly_capture,
)
from models.ytd_income import ManualMark, YTDSnapshot

TOTALS = {
    "interest_ytd": 5_000.0,
    "tax_exempt_interest_ytd": 0.0,
    "ordinary_dividends_ytd": 0.0,
    "stcg_ytd": 0.0,
    "ltcg_ytd": 0.0,
}


def _ledger(period_end: str = "2026-09-30", koinly_captured: str = "2026-09-20T08:00:00") -> dict:
    return {
        "koinly": {
            "you": {"stcg": 11.0, "ltcg": 22.0, "income": 33.0, "captured_at": koinly_captured}
        },
        "brokerage": {
            "you": {"A1": {"broker": "schwab", "statement_period_end": "2026-06-30"}},
            "spouse": {"B2": {"broker": "vanguard", "statement_period_end": period_end}},
        },
    }


def _snap(field: str, value: float, as_of: str) -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={"you": {}},
        **{field: value},
        manual_marks={f"household.{field}": ManualMark(value, as_of, "typed")},
    )


def test_latest_brokerage_period_end_is_the_newest_statement():
    assert latest_brokerage_period_end(_ledger()) == "2026-09-30"
    assert latest_brokerage_period_end({"koinly": {}, "brokerage": {}}) is None


def test_latest_koinly_capture_is_a_date():
    assert latest_koinly_capture(_ledger()) == "2026-09-20"
    assert latest_koinly_capture({"koinly": {}, "brokerage": {}}) is None


def test_koinly_import_keeps_a_newer_hand_entry():
    snap = _snap("crypto_income_ytd", 999.0, "2026-10-01")
    assert apply_koinly_totals(snap, _ledger()) == ("household.crypto_income_ytd",)
    assert snap.crypto_income_ytd == 999.0
    assert snap.crypto_stcg_ytd == 11.0


def test_koinly_import_replaces_an_older_hand_entry():
    snap = _snap("crypto_income_ytd", 999.0, "2026-09-01")
    assert apply_koinly_totals(snap, _ledger()) == ()
    assert snap.crypto_income_ytd == 33.0
    assert snap.manual_marks == {}


@pytest.fixture
def no_ubs(monkeypatch):
    monkeypatch.setattr(sync_scan_mod, "_load_ubs_orders_safe", lambda: [])
    monkeypatch.setattr(sync_scan_mod, "operator_slot", lambda: "you")


def test_statement_scan_keeps_a_newer_hand_entry_and_says_so(no_ubs):
    snap = _snap("interest_ytd", 9_000.0, "2026-10-01")
    warnings = sync_scan_mod._apply_ubs_correction(dict(TOTALS), _ledger(), snap)
    assert snap.interest_ytd == 9_000.0
    assert any("interest" in w and "2026-10-01" in w for w in warnings)


def test_statement_scan_replaces_an_older_hand_entry(no_ubs):
    snap = _snap("interest_ytd", 9_000.0, "2026-09-01")
    sync_scan_mod._apply_ubs_correction(dict(TOTALS), _ledger(), snap)
    assert snap.interest_ytd == 5_000.0
    assert snap.manual_marks == {}


# --- Date normalisation and the unattributed edge case (Task 6 additions) ---


def test_an_unparsable_import_date_counts_as_undated_so_the_import_wins():
    snap = _snap("interest_ytd", 9_000.0, "2026-10-01")
    snap.interest_ytd = 5_000.0  # the import's write
    assert snap.settle_manual_marks(as_of="09/30/2026") == ()
    assert snap.interest_ytd == 5_000.0
    assert snap.manual_marks == {}


def test_a_garbled_statement_date_does_not_pin_hand_entries(no_ubs):
    ledger = _ledger(period_end="Sep 30 2026")
    ledger["brokerage"]["you"]["A1"]["statement_period_end"] = "junk"
    assert latest_brokerage_period_end(ledger) is None
    snap = _snap("interest_ytd", 9_000.0, "2026-10-01")
    sync_scan_mod._apply_ubs_correction(dict(TOTALS), ledger, snap)
    assert snap.interest_ytd == 5_000.0


def test_settling_an_unattributed_snapshot_does_not_raise():
    # Edge case checked for Task 6: on a legacy (household-only) snapshot a
    # per-owner restore goes through set_owned, which writes the household
    # total instead of raising (the Task 4 legacy semantics).
    snap = YTDSnapshot(
        wages_ytd=10.0,
        manual_marks={"you.wages_ytd": ManualMark(10.0, "2026-10-01", "typed")},
    )
    snap.wages_ytd = 3.0  # an import's write
    assert snap.settle_manual_marks(as_of="2026-09-30T12:00:00") == ("you.wages_ytd",)
    assert snap.wages_ytd == 10.0
    assert snap.by_owner == {}
