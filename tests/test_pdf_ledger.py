"""Tests for engine.pdf_ledger -- per-owner PDF contribution ledger.

Proves the override-fix design goal: derive-on-apply reproduces today's exact
single-owner behavior and SUMS across owners for a two-owner scan, replacing
the old single-valued direct-assignment bug (crypto_stcg_ytd = report.stcg).
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.koinly_report_pdf import KoinlyReport
from engine.pdf_ledger import (
    derive_brokerage_totals,
    derive_koinly_totals,
    extract_owner,
    load_ledger,
    replace_owner,
    save_ledger,
    write_brokerage_contribution,
    write_koinly_contribution,
)


def _koinly(stcg: float, ltcg: float, income: float) -> KoinlyReport:
    return KoinlyReport(
        tax_year=2026,
        crypto_stcg=stcg,
        crypto_ltcg=ltcg,
        crypto_income=income,
        captured_at="2026-07-13T00:00:00+00:00",
    )


def _brokerage(account_number: str, interest: float = 0.0) -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number=account_number,
        broker="schwab",
        account_type="taxable",
        statement_period_end="2026-06-30",
        interest_taxable_ytd=interest,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=0.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-07-13T00:00:00+00:00",
    )


class TestKoinlySingleOwnerMatchesToday:
    """One owner scanned once -> identical to the pre-ledger direct-assignment
    behavior (the non-regression half of the fix)."""

    def test_single_owner_totals_equal_report(self) -> None:
        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", _koinly(100.0, 200.0, 50.0))
        totals = derive_koinly_totals(ledger)
        assert totals == {"stcg": 100.0, "ltcg": 200.0, "income": 50.0}


class TestKoinlyOverrideFixed:
    """The specific bug: scanning spouse's report after yours must ADD, not
    overwrite."""

    def test_two_owner_additive(self) -> None:
        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", _koinly(100.0, 200.0, 50.0))
        ledger = write_koinly_contribution(ledger, "spouse", _koinly(10.0, 20.0, 5.0))
        totals = derive_koinly_totals(ledger)
        assert totals == {"stcg": 110.0, "ltcg": 220.0, "income": 55.0}

    def test_idempotent_rescan_same_owner(self) -> None:
        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", _koinly(100.0, 200.0, 50.0))
        ledger = write_koinly_contribution(ledger, "spouse", _koinly(10.0, 20.0, 5.0))
        # Re-scan "you" with an updated report -- must REPLACE "you"'s slot only.
        ledger = write_koinly_contribution(ledger, "you", _koinly(150.0, 200.0, 50.0))
        totals = derive_koinly_totals(ledger)
        assert totals == {"stcg": 160.0, "ltcg": 220.0, "income": 55.0}

    def test_household_role_is_a_distinct_slot(self) -> None:
        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", _koinly(100.0, 0.0, 0.0))
        ledger = write_koinly_contribution(ledger, "household", _koinly(5.0, 0.0, 0.0))
        totals = derive_koinly_totals(ledger)
        assert totals["stcg"] == pytest.approx(105.0)


class TestKoinlyProvenancePreservedOnMerge:
    """Provenance/owner_key must survive a subsequent write from the OTHER
    owner (last-write-wins across owners must not discard the earlier
    owner's data-quality warnings)."""

    def test_provenance_and_owner_key_survive_second_owner_write(self) -> None:
        ledger: dict = {}
        you_report = KoinlyReport(
            tax_year=2026,
            crypto_stcg=100.0,
            crypto_ltcg=200.0,
            crypto_income=50.0,
            captured_at="2026-07-13T00:00:00+00:00",
            provenance={"income_total_mismatch": "reported $500 != summed $450"},
            owner_key="you@example.com",
        )
        ledger = write_koinly_contribution(ledger, "you", you_report)
        ledger = write_koinly_contribution(ledger, "spouse", _koinly(10.0, 20.0, 5.0))
        assert ledger["koinly"]["you"]["provenance"] == {
            "income_total_mismatch": "reported $500 != summed $450"
        }
        assert ledger["koinly"]["you"]["owner_key"] == "you@example.com"


class TestKoinlyEmptyLedger:
    def test_empty_ledger_returns_zeros(self) -> None:
        assert derive_koinly_totals({}) == {"stcg": 0.0, "ltcg": 0.0, "income": 0.0}


class TestBrokerageAdditive:
    def test_single_owner_single_account(self) -> None:
        ledger: dict = {}
        ledger = write_brokerage_contribution(ledger, "you", _brokerage("111", interest=10.0))
        totals = derive_brokerage_totals(ledger)
        assert totals["interest_ytd"] == pytest.approx(10.0)

    def test_two_owners_different_accounts_additive(self) -> None:
        ledger: dict = {}
        ledger = write_brokerage_contribution(ledger, "you", _brokerage("111", interest=10.0))
        ledger = write_brokerage_contribution(ledger, "spouse", _brokerage("222", interest=20.0))
        totals = derive_brokerage_totals(ledger)
        assert totals["interest_ytd"] == pytest.approx(30.0)

    def test_rescan_same_owner_same_account_replaces(self) -> None:
        ledger: dict = {}
        ledger = write_brokerage_contribution(ledger, "you", _brokerage("111", interest=10.0))
        ledger = write_brokerage_contribution(ledger, "you", _brokerage("111", interest=15.0))
        totals = derive_brokerage_totals(ledger)
        assert totals["interest_ytd"] == pytest.approx(15.0)


class TestLedgerCache:
    def test_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_ledger as mod

        monkeypatch.setattr(mod, "_LEDGER_PATH", tmp_path / ".pdf_import_ledger.json")
        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", _koinly(100.0, 0.0, 0.0))
        save_ledger(ledger)
        loaded = load_ledger()
        assert derive_koinly_totals(loaded) == {"stcg": 100.0, "ltcg": 0.0, "income": 0.0}

    def test_load_missing_returns_empty_ledger(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_ledger as mod

        monkeypatch.setattr(mod, "_LEDGER_PATH", tmp_path / "nope.json")
        assert load_ledger() == {"koinly": {}, "brokerage": {}}

    def test_load_corrupt_returns_empty_ledger(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_ledger as mod

        bad = tmp_path / ".pdf_import_ledger.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_LEDGER_PATH", bad)
        assert load_ledger() == {"koinly": {}, "brokerage": {}}

    def test_load_predates_ledger_defaults_missing_doc_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hand-crafted / partially-migrated ledger missing the 'brokerage'
        key must default it to {} rather than KeyError."""
        import engine.pdf_ledger as mod

        partial = tmp_path / ".pdf_import_ledger.json"
        partial.write_text('{"koinly": {}}')
        monkeypatch.setattr(mod, "_LEDGER_PATH", partial)
        loaded = load_ledger()
        assert loaded == {"koinly": {}, "brokerage": {}}


class TestOwnerSlice:
    def _ledger(self):
        return {
            "koinly": {
                "you": {"stcg": 10.0, "ltcg": 5.0, "income": 1.0, "captured_at": "t", "source": "k"},
                "spouse": {"stcg": 99.0, "ltcg": 0.0, "income": 0.0, "captured_at": "t", "source": "k"},
            },
            "brokerage": {
                "you": {"A1": {"interest": 3.0}},
                "spouse": {"B2": {"interest": 7.0}},
            },
        }

    def test_extract_owner_returns_only_that_owner_inner_values(self):
        slice_ = extract_owner(self._ledger(), "you")
        assert slice_ == {
            "koinly": {"stcg": 10.0, "ltcg": 5.0, "income": 1.0, "captured_at": "t", "source": "k"},
            "brokerage": {"A1": {"interest": 3.0}},
        }

    def test_extract_owner_missing_owner_yields_empty_sections(self):
        assert extract_owner({"koinly": {}, "brokerage": {}}, "you") == {"koinly": {}, "brokerage": {}}

    def test_extract_owner_does_not_mutate_input(self):
        led = self._ledger()
        snapshot = copy.deepcopy(led)
        extract_owner(led, "you")
        assert led == snapshot

    def test_replace_owner_drops_old_and_inserts_new_under_target(self):
        led = self._ledger()
        new_slice = {"koinly": {"stcg": 1.0, "ltcg": 2.0, "income": 0.0, "captured_at": "t2", "source": "k"},
                     "brokerage": {"Z9": {"interest": 4.0}}}
        out = replace_owner(led, "spouse", new_slice)
        assert out["koinly"]["spouse"] == new_slice["koinly"]
        assert out["brokerage"]["spouse"] == {"Z9": {"interest": 4.0}}
        assert out["koinly"]["you"] == led["koinly"]["you"]
        assert out["brokerage"]["you"] == led["brokerage"]["you"]

    def test_replace_owner_with_empty_slice_clears_that_owner(self):
        led = self._ledger()
        out = replace_owner(led, "spouse", {"koinly": {}, "brokerage": {}})
        assert "spouse" not in out["koinly"]
        assert "spouse" not in out["brokerage"]
        assert "you" in out["koinly"]

    def test_replace_owner_does_not_mutate_input(self):
        led = self._ledger()
        snapshot = copy.deepcopy(led)
        replace_owner(led, "spouse", {"koinly": {}, "brokerage": {}})
        assert led == snapshot
