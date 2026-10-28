"""audit-1026 F-15: the brokerage ledger keeps one slot per (owner, account, tax year)."""

from engine.brokerage_statement_pdf import (
    BrokerageStatementRecord,
    merge_latest_per_account,
    pick_latest_per_account,
)
from engine.pdf_ledger import (
    derive_brokerage_totals,
    extract_owner,
    prune_duplicates_elsewhere,
    replace_owner,
    write_brokerage_contribution,
)


def _rec(
    period_end: str,
    interest: float,
    account_number: str = "X0000001",
    broker: str = "schwab",
) -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number=account_number,
        broker=broker,
        account_type="taxable",
        statement_period_end=period_end,
        interest_taxable_ytd=interest,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=0.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-07-13T00:00:00+00:00",
    )


def _empty() -> dict:
    return {"koinly": {}, "brokerage": {}}


def _interest(ledger: dict, year: int) -> float:
    return derive_brokerage_totals(ledger, tax_year=year)["interest_ytd"]


def test_next_year_statement_preserves_current_year() -> None:
    ledger = write_brokerage_contribution(_empty(), "you", _rec("2026-12-31", 100.0))
    ledger = write_brokerage_contribution(ledger, "you", _rec("2027-03-31", 7.0))
    assert _interest(ledger, 2026) == 100.0
    assert _interest(ledger, 2027) == 7.0


def test_next_year_first_then_current_year() -> None:
    ledger = write_brokerage_contribution(_empty(), "you", _rec("2027-03-31", 7.0))
    ledger = write_brokerage_contribution(ledger, "you", _rec("2026-12-31", 100.0))
    assert _interest(ledger, 2026) == 100.0
    assert _interest(ledger, 2027) == 7.0


def test_same_year_newer_replaces_older() -> None:
    ledger = write_brokerage_contribution(_empty(), "you", _rec("2026-06-30", 50.0))
    ledger = write_brokerage_contribution(ledger, "you", _rec("2026-12-31", 100.0))
    assert _interest(ledger, 2026) == 100.0
    assert len(ledger["brokerage"]["you"]) == 1


def test_same_year_older_rejected() -> None:
    ledger = write_brokerage_contribution(_empty(), "you", _rec("2026-12-31", 100.0))
    ledger = write_brokerage_contribution(ledger, "you", _rec("2026-06-30", 50.0))
    assert _interest(ledger, 2026) == 100.0


def test_legacy_yearless_slot_derives_and_is_replaced_in_its_year() -> None:
    legacy = _rec("2026-06-30", 50.0).to_dict()
    ledger = {"koinly": {}, "brokerage": {"you": {"X0000001": legacy}}}
    assert _interest(ledger, 2026) == 50.0
    ledger = write_brokerage_contribution(ledger, "you", _rec("2026-12-31", 100.0))
    assert _interest(ledger, 2026) == 100.0
    ledger = write_brokerage_contribution(ledger, "you", _rec("2027-03-31", 7.0))
    assert _interest(ledger, 2026) == 100.0
    assert _interest(ledger, 2027) == 7.0


def test_other_broker_same_number_still_separate_per_year() -> None:
    ledger = write_brokerage_contribution(_empty(), "you", _rec("2026-12-31", 1.0))
    ledger = write_brokerage_contribution(ledger, "you", _rec("2026-12-31", 2.0, broker="vanguard"))
    ledger = write_brokerage_contribution(ledger, "you", _rec("2027-03-31", 4.0))
    ledger = write_brokerage_contribution(ledger, "you", _rec("2027-03-31", 8.0, broker="vanguard"))
    assert _interest(ledger, 2026) == 3.0
    assert _interest(ledger, 2027) == 12.0
    assert len(ledger["brokerage"]["you"]) == 4


def test_owner_move_moves_only_that_year() -> None:
    ledger = write_brokerage_contribution(_empty(), "you", _rec("2026-12-31", 100.0))
    ledger = write_brokerage_contribution(ledger, "you", _rec("2027-03-31", 7.0))
    ledger = write_brokerage_contribution(ledger, "spouse", _rec("2026-12-31", 100.0))
    assert _interest(ledger, 2026) == 100.0
    assert _interest(ledger, 2027) == 7.0
    assert len(ledger["brokerage"]["spouse"]) == 1


def test_extract_and_replace_owner_roundtrip_all_years() -> None:
    ledger = write_brokerage_contribution(_empty(), "you", _rec("2026-12-31", 100.0))
    ledger = write_brokerage_contribution(ledger, "you", _rec("2027-03-31", 7.0))
    sliced = extract_owner(ledger, "you")
    out = replace_owner(_empty(), "you", sliced)
    assert _interest(out, 2026) == 100.0
    assert _interest(out, 2027) == 7.0


def test_prune_duplicates_elsewhere_is_per_year() -> None:
    ledger = write_brokerage_contribution(_empty(), "stale", _rec("2026-12-31", 100.0))
    ledger = write_brokerage_contribution(ledger, "stale", _rec("2027-03-31", 7.0))
    incoming = write_brokerage_contribution(_empty(), "you", _rec("2026-12-31", 100.0))
    merged = {
        "koinly": {},
        "brokerage": {**ledger["brokerage"], "you": incoming["brokerage"]["you"]},
    }
    pruned = prune_duplicates_elsewhere(merged, frozenset({"you"}))
    assert _interest(pruned, 2026) == 100.0  # not double counted
    assert _interest(pruned, 2027) == 7.0  # the other year is not the same document


def test_pick_latest_per_account_keeps_one_per_year() -> None:
    picked = pick_latest_per_account(
        [_rec("2026-06-30", 1.0), _rec("2026-12-31", 2.0), _rec("2027-03-31", 3.0)]
    )
    assert sorted((r.statement_period_end, r.interest_taxable_ytd) for r in picked.values()) == [
        ("2026-12-31", 2.0),
        ("2027-03-31", 3.0),
    ]
    assert len(picked) == 2


def test_merge_latest_per_account_dedupes_per_account_and_year() -> None:
    held = pick_latest_per_account([_rec("2026-12-31", 2.0)])
    merged = merge_latest_per_account(held, [_rec("2027-03-31", 3.0), _rec("2026-06-30", 1.0)])
    assert sorted(r.statement_period_end for r in merged.values()) == ["2026-12-31", "2027-03-31"]
