"""IRA statement -> account-on-file linking (engine/account_link.py and
apply_ira_statement(replaces=...)). Synthetic names and round amounts only."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import account_link as mod
from engine.account_link import (
    NEW,
    link_candidates,
    load_link_map,
    remember_link,
    save_link_map,
    statement_identity,
    suggest_link,
)
from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.ira_statement_balance import apply_ira_statement, to_ira_account_summary
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
from engine.portfolio_sync.as_of import account_key, merge_sync
from engine.portfolio_sync.shapes import Holding
from engine.upload_merge import derive_ira_balances, synced_account_sums


def _rec(**kw: object) -> BrokerageStatementRecord:
    base: dict[str, object] = {
        "account_number": "Z00000001",
        "broker": "fidelity",
        "account_type": "traditional_ira",
        "statement_period_end": "2026-08-31",
        "interest_taxable_ytd": 0.0,
        "interest_tax_exempt_ytd": 0.0,
        "dividends_taxable_ytd": 0.0,
        "dividends_tax_exempt_ytd": 0.0,
        "stcg_net_ytd": 0.0,
        "ltcg_net_ytd": 0.0,
        "captured_at": "2026-09-01T00:00:00",
        "owner_key": "JANE Q SAMPLE",
        "ending_value": 100_000.0,
    }
    base.update(kw)
    return BrokerageStatementRecord(**base)  # type: ignore[arg-type]


def _acct(name: str, value: float = 90_000.0, **kw: object) -> AccountSummary:
    base: dict[str, object] = {
        "account_type": "trad_ira",
        "owner": "you",
        "account_name": name,
        "total_value": value,
        "as_of": "2026-07-31",
        "as_of_source": "finextract",
    }
    base.update(kw)
    return AccountSummary(**base)  # type: ignore[arg-type]


class TestSuggestLink:
    def test_full_number_match_fidelity_style(self) -> None:
        a, b = _acct("Rollover IRAZ00000001"), _acct("Rollover IRAZ00000002")
        assert suggest_link(_rec(), [a, b], {}) == account_key(a)

    def test_full_number_match_ibkr_raw_id(self) -> None:
        a, b = _acct("U1234567"), _acct("U7654321")
        rec = _rec(broker="ibkr", account_number="U1234567")
        assert suggest_link(rec, [a, b], {}) == account_key(a)

    def test_vanguard_last4_plus_broker_token(self) -> None:
        a = _acct("Vanguard Traditional IRA registration 5678")
        b = _acct("Fidelity Rollover 5678")
        rec = _rec(broker="vanguard", account_number="XXXX5678")
        assert suggest_link(rec, [a, b], {}) == account_key(a)

    def test_last4_without_broker_token_is_not_enough(self) -> None:
        rec = _rec(broker="vanguard", account_number="XXXX5678")
        assert suggest_link(rec, [_acct("Rollover 5678")], {}) is None

    def test_no_match(self) -> None:
        assert suggest_link(_rec(), [_acct("Something Else")], {}) is None

    def test_ambiguous_hits_return_none(self) -> None:
        a, b = _acct("Rollover Z00000001 A"), _acct("Rollover Z00000001 B")
        assert suggest_link(_rec(), [a, b], {}) is None

    def test_remembered_link_wins(self) -> None:
        a, b = _acct("Rollover IRAZ00000001"), _acct("Other")
        link_map = {statement_identity(_rec()): account_key(b)}
        assert suggest_link(_rec(), [a, b], link_map) == account_key(b)

    def test_remembered_link_with_vanished_target_is_ignored(self) -> None:
        a = _acct("Rollover IRAZ00000001")
        link_map = {statement_identity(_rec()): ("trad_ira", "you", "Gone")}
        assert suggest_link(_rec(), [a], link_map) == account_key(a)

    def test_remembered_new(self) -> None:
        a = _acct("Rollover IRAZ00000001")
        link_map = {statement_identity(_rec()): NEW}
        assert suggest_link(_rec(), [a], link_map) == NEW


class TestLinkCandidates:
    def test_same_type_and_owner_excluding_pdf_named(self) -> None:
        pdf_named = to_ira_account_summary(_rec(), "you")
        snap = PortfolioSnapshot(
            accounts=[
                _acct("Synced"),
                _acct("Spouse one", owner="spouse"),
                _acct("Roth one", account_type="roth_ira"),
                pdf_named,
            ]
        )
        assert [a.account_name for a in link_candidates(_rec(), snap, "you", {})] == ["Synced"]

    def test_other_statements_accounts_are_not_candidates(self) -> None:
        fidelity_written = _acct("Rollover", as_of_source="statement")
        synced = _acct("U1234567")
        snap = PortfolioSnapshot(accounts=[fidelity_written, synced])
        ibkr = _rec(broker="ibkr", account_number="U1234567")
        got = link_candidates(
            ibkr, snap, "you", {"fidelity|Z00000001": account_key(fidelity_written)}
        )
        assert [a.account_name for a in got] == ["U1234567"]

    def test_another_statements_linked_target_is_excluded_even_when_synced(self) -> None:
        taken, free = _acct("Taken"), _acct("Free")
        snap = PortfolioSnapshot(accounts=[taken, free])
        got = link_candidates(_rec(), snap, "you", {"ibkr|U1": account_key(taken)})
        assert [a.account_name for a in got] == ["Free"]

    def test_unlinked_statement_sourced_account_is_excluded(self) -> None:
        snap = PortfolioSnapshot(accounts=[_acct("Written", as_of_source="statement")])
        assert link_candidates(_rec(), snap, "you", {}) == []

    def test_remembered_target_overwritten_by_this_statement_stays_and_is_suggested(self) -> None:
        mine = _acct("Rollover IRAZ00000001", as_of_source="statement")
        snap = PortfolioSnapshot(accounts=[mine, _acct("Other")])
        link_map = {statement_identity(_rec()): account_key(mine)}
        cands = link_candidates(_rec(), snap, "you", link_map)
        assert account_key(mine) in [account_key(a) for a in cands]
        assert suggest_link(_rec(), cands, link_map) == account_key(mine)


class TestApplyWithReplaces:
    def _snap(self, *extra: AccountSummary) -> tuple[PortfolioSnapshot, AccountSummary]:
        synced = _acct(
            "Rollover IRAZ00000001",
            equity_value=90_000.0,
            holdings=[Holding("AAA", "Fund", 10, 90_000.0, "x", "equity")],
        )
        return PortfolioSnapshot(accounts=[synced, *extra]), synced

    def test_value_lands_in_synced_account_keeping_name(self) -> None:
        snap, synced = self._snap()
        out = apply_ira_statement(snap, _rec(), "you", replaces=account_key(synced))
        [acct] = out.snapshot.accounts
        assert acct.account_name == "Rollover IRAZ00000001"
        assert acct.total_value == 100_000.0
        assert (acct.as_of, acct.as_of_source) == ("2026-08-31", "statement")
        assert acct.holdings == []
        # audit-1008 F-08: the prior class mix (all equity) is kept, scaled to
        # the statement total. A zeroed split would make weighted_return 0.0
        # and drive the owner's derived IRA growth to 0%/yr.
        assert acct.equity_value == 100_000.0
        assert acct.weighted_return == synced.weighted_return

    def test_owner_total_counts_it_once(self) -> None:
        snap, synced = self._snap()
        out = apply_ira_statement(snap, _rec(), "you", replaces=account_key(synced))
        assert derive_ira_balances(out.snapshot)[0] == 100_000.0
        assert synced_account_sums(out.snapshot, "you", "pretax") == {"trad_ira": 100_000.0}
        assert out.total == 100_000.0

    def test_older_statement_does_not_overwrite_newer_unless_forced(self) -> None:
        snap, synced = self._snap()
        snap.accounts[0].as_of = "2026-09-30"
        kept = apply_ira_statement(snap, _rec(), "you", replaces=account_key(synced))
        assert kept.kept_newer is not None
        assert kept.snapshot is snap
        forced = apply_ira_statement(snap, _rec(), "you", force=True, replaces=account_key(synced))
        assert forced.snapshot.accounts[0].total_value == 100_000.0

    def test_leftover_pdf_named_duplicate_is_removed(self) -> None:
        leftover = to_ira_account_summary(_rec(), "you")
        snap, synced = self._snap(leftover)
        out = apply_ira_statement(snap, _rec(), "you", replaces=account_key(synced))
        assert [a.account_name for a in out.snapshot.accounts] == ["Rollover IRAZ00000001"]
        assert derive_ira_balances(out.snapshot)[0] == 100_000.0

    def test_refused_apply_still_drops_leftover(self) -> None:
        leftover = to_ira_account_summary(_rec(), "you")
        snap, synced = self._snap(leftover)
        snap.accounts[0].as_of = "2026-09-30"
        out = apply_ira_statement(snap, _rec(), "you", replaces=account_key(synced))
        assert out.kept_newer is not None
        assert [a.account_name for a in out.snapshot.accounts] == ["Rollover IRAZ00000001"]
        assert out.snapshot.accounts[0].total_value == 90_000.0
        assert out.total == 90_000.0

    def test_vanished_target_falls_back_to_pdf_named_account(self) -> None:
        out = apply_ira_statement(
            PortfolioSnapshot(), _rec(), "you", replaces=("trad_ira", "you", "Gone")
        )
        assert [a.account_name for a in out.snapshot.accounts] == [
            "Fidelity Traditional IRA Z00000001"
        ]

    def test_later_finextract_merge_still_yields_one_account(self) -> None:
        snap, synced = self._snap()
        out = apply_ira_statement(snap, _rec(), "you", replaces=account_key(synced))
        resync = _acct("Rollover IRAZ00000001", 101_000.0, as_of="2026-09-30")
        merged = merge_sync(out.snapshot.accounts, [resync]).accounts
        assert [(a.account_name, a.total_value) for a in merged] == [
            ("Rollover IRAZ00000001", 101_000.0)
        ]


class TestLinkMapStore:
    def test_round_trip(self) -> None:
        rec = _rec()
        key = ("trad_ira", "you", "Rollover IRAZ00000001")
        link_map = remember_link(rec, key, {})
        link_map = remember_link(_rec(account_number="Z2"), NEW, link_map)
        save_link_map(link_map)
        assert load_link_map() == link_map

    def test_missing_and_corrupt_files_load_empty(self) -> None:
        assert load_link_map() == {}
        mod._ACCOUNT_LINK_PATH.write_text("not json")
        assert load_link_map() == {}
        mod._ACCOUNT_LINK_PATH.write_text("[1]")
        assert load_link_map() == {}

    def test_malformed_entries_are_dropped(self) -> None:
        mod._ACCOUNT_LINK_PATH.write_text('{"a|1": ["x"], "b|2": "NEW"}')
        assert load_link_map() == {"b|2": NEW}

    def test_path_is_redirected_away_from_the_repo(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        assert repo_root not in mod._ACCOUNT_LINK_PATH.parents


@pytest.mark.parametrize("n", ["", "ab"])
def test_short_numbers_never_suggest(n: str) -> None:
    assert suggest_link(_rec(account_number=n), [_acct("ab Fidelity")], {}) is None
