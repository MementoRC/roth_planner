"""IRA statement balances (Vanguard / Fidelity / IBKR) -> portfolio snapshot.

Synthetic names and round amounts only.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.record import record_ira_balance_candidate, record_roth_balance_candidate
from engine.ira_statement_balance import (
    apply_ira_statement,
    default_ira_owner,
    describe_ira_record,
    is_ira_record,
    pick_latest_ira_records,
    to_ira_account_summary,
)
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot


def _rec(**kw: object) -> BrokerageStatementRecord:
    base: dict[str, object] = {
        "account_number": "X1234",
        "broker": "vanguard",
        "account_type": "traditional_ira",
        "statement_period_end": "2026-08-31",
        "interest_taxable_ytd": 0.0,
        "interest_tax_exempt_ytd": 0.0,
        "dividends_taxable_ytd": 0.0,
        "dividends_tax_exempt_ytd": 0.0,
        "stcg_net_ytd": 0.0,
        "ltcg_net_ytd": 0.0,
        "captured_at": "2026-09-01T00:00:00",
        "owner_key": "JOHN Q SAMPLE",
        "ending_value": 100_000.0,
    }
    base.update(kw)
    return BrokerageStatementRecord(**base)  # type: ignore[arg-type]


class TestIsIraRecord:
    @pytest.mark.parametrize("account_type", ["traditional_ira", "roth_ira"])
    def test_ira_with_value(self, account_type: str) -> None:
        assert is_ira_record(_rec(account_type=account_type))

    @pytest.mark.parametrize("account_type", ["taxable", "hsa", "unknown"])
    def test_other_types_are_not_ira(self, account_type: str) -> None:
        assert not is_ira_record(_rec(account_type=account_type))

    def test_ira_without_value_is_not_usable(self) -> None:
        assert not is_ira_record(_rec(ending_value=None))


class TestToSummary:
    def test_traditional(self) -> None:
        acct = to_ira_account_summary(_rec(), "you")
        assert (acct.account_type, acct.owner, acct.total_value) == ("trad_ira", "you", 100_000.0)
        assert acct.account_name == "Vanguard Traditional IRA X1234"
        assert (acct.as_of, acct.as_of_source) == ("2026-08-31", "statement")

    def test_roth(self) -> None:
        acct = to_ira_account_summary(_rec(account_type="roth_ira", broker="ibkr"), "spouse")
        assert (acct.account_type, acct.owner) == ("roth_ira", "spouse")
        assert acct.account_name == "IBKR Roth IRA X1234"

    def test_rejects_unknown_owner(self) -> None:
        with pytest.raises(ValueError, match="owner"):
            to_ira_account_summary(_rec(), "household")


class TestDescribe:
    def test_uses_last_four_of_the_account_number(self) -> None:
        assert describe_ira_record(_rec(broker="fidelity", account_number="123-456789")) == (
            "Fidelity Traditional IRA …6789"
        )

    def test_roth(self) -> None:
        assert describe_ira_record(_rec(account_type="roth_ira")) == "Vanguard Roth IRA …1234"


def _snap(*accts: AccountSummary) -> PortfolioSnapshot:
    return PortfolioSnapshot(accounts=list(accts))


class TestApply:
    def test_adds_when_no_account_on_file(self) -> None:
        other = AccountSummary(account_type="403b", owner="you", total_value=5_000.0)
        out = apply_ira_statement(_snap(other), _rec(), "you")
        assert out.replaced == []
        assert out.kept_newer is None
        assert [a.total_value for a in out.snapshot.accounts] == [5_000.0, 100_000.0]
        assert out.field_key == "your_ira"
        assert out.total == pytest.approx(105_000.0)  # pre-tax = IRA + 403(b)
        assert "Vanguard" in out.detail
        assert "2026-08-31" in out.detail

    def test_replaces_same_account_and_leaves_others(self) -> None:
        old = to_ira_account_summary(
            _rec(ending_value=90_000.0, statement_period_end="2026-07-31"), "you"
        )
        other_owner = AccountSummary(account_type="trad_ira", owner="spouse", total_value=7.0)
        snap = _snap(old, other_owner)
        out = apply_ira_statement(snap, _rec(), "you")
        assert out.replaced == [old]
        assert [(a.owner, a.total_value) for a in out.snapshot.accounts] == [
            ("spouse", 7.0),
            ("you", 100_000.0),
        ]
        assert [a.total_value for a in snap.accounts] == [90_000.0, 7.0]  # input untouched

    def test_tie_goes_to_the_statement(self) -> None:
        old = to_ira_account_summary(_rec(ending_value=90_000.0), "you")
        out = apply_ira_statement(_snap(old), _rec(), "you")
        assert out.kept_newer is None
        assert out.snapshot.accounts[0].total_value == 100_000.0

    def test_newer_cache_is_kept_unless_forced(self) -> None:
        cached = to_ira_account_summary(
            _rec(ending_value=111_000.0, statement_period_end="2026-09-30"), "you"
        )
        snap = _snap(cached)
        out = apply_ira_statement(snap, _rec(), "you")
        assert out.kept_newer is cached
        assert out.snapshot is snap
        assert out.new_account is not None
        assert out.new_account.total_value == 100_000.0
        assert out.replaced == []

        forced = apply_ira_statement(snap, _rec(), "you", force=True)
        assert forced.kept_newer is None
        assert [a.total_value for a in forced.snapshot.accounts] == [100_000.0]
        assert forced.replaced == [cached]

    def test_spouse_traditional_field_key(self) -> None:
        assert apply_ira_statement(_snap(), _rec(), "spouse").field_key == "spouse_ira"

    @pytest.mark.parametrize(("owner", "key"), [("you", "your_roth"), ("spouse", "spouse_roth")])
    def test_roth_field_key_and_roth_total(self, owner: str, key: str) -> None:
        trad = AccountSummary(account_type="trad_ira", owner=owner, total_value=1_000_000.0)
        roth_other = AccountSummary(
            account_type="roth_ira", owner=owner, account_name="Other", total_value=3_000.0
        )
        out = apply_ira_statement(_snap(trad, roth_other), _rec(account_type="roth_ira"), owner)
        assert out.field_key == key
        assert out.total == pytest.approx(103_000.0)  # Roth accounts only

    def test_a_different_account_of_the_same_owner_is_added_not_replaced(self) -> None:
        old = to_ira_account_summary(_rec(account_number="X9999"), "you")
        out = apply_ira_statement(_snap(old), _rec(), "you")
        assert out.replaced == []
        assert len(out.snapshot.accounts) == 2


class TestDefaultOwner:
    def test_learned_name_wins(self) -> None:
        assert default_ira_owner(_rec(), {"john q sample": "spouse"}, "you") == "spouse"

    def test_falls_back_to_instance_owner_then_you(self) -> None:
        assert default_ira_owner(_rec(owner_key=None), {}, "spouse") == "spouse"
        assert default_ira_owner(_rec(owner_key=None), {}, "household") == "you"


class TestPickLatest:
    def test_keeps_newest_ira_per_broker_and_account(self) -> None:
        older = _rec(statement_period_end="2026-07-31", ending_value=1.0)
        newer = _rec()
        other_broker = _rec(broker="fidelity")
        taxable = _rec(account_type="taxable", account_number="T1")
        no_value = _rec(account_number="X7", ending_value=None)
        picked = pick_latest_ira_records([older, newer, other_broker, taxable, no_value])
        assert picked == [newer, other_broker]


class TestRecordRothCandidate:
    def test_records_a_pdf_candidate(self) -> None:
        assert record_roth_balance_candidate(
            "spouse_roth",
            22_000.4,
            "Vanguard Roth IRA PDF",
            datetime(2026, 10, 1),
        )
        [cand] = CandidateStore.load().candidates_for("spouse_roth")
        assert cand.value == 22_000.0

    def test_rejects_a_non_roth_field(self) -> None:
        with pytest.raises(ValueError, match="your_roth"):
            record_roth_balance_candidate("your_ira", 1.0, "x", datetime(2026, 1, 1))

    def test_ira_recorder_still_rejects_roth_keys(self) -> None:
        with pytest.raises(ValueError, match="your_ira"):
            record_ira_balance_candidate("your_roth", 1.0, "x", datetime(2026, 1, 1))
