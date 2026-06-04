"""Tests for upload-time cross-mapping helpers (PR D — two-primary-planners model)."""

from __future__ import annotations

from engine.portfolio_sync import (
    AccountSummary,
    EquityGrant,
    PortfolioSnapshot,
    merge_snapshots,
)
from engine.upload_merge import build_user_defaults_session_updates


def _make_account(owner: str, name: str, value: float, acct_type: str = "brokerage") -> AccountSummary:
    return AccountSummary(
        account_type=acct_type,
        owner=owner,
        account_name=name,
        total_value=value,
        equity_value=value,
    )


def _make_grant(grant_id: str = "g1") -> EquityGrant:
    return EquityGrant(
        grant_id=grant_id,
        grant_type="NQO",
        grant_date="2021-01-01",
        shares_granted=1000,
        outstanding=500,
        current_value=82500.0,
    )


def _build_updates(data: dict, *, as_spouse: bool) -> dict:
    return build_user_defaults_session_updates(data, as_spouse=as_spouse)


class TestBuildUserDefaultsUpdates:
    def test_me_mode_passes_through_all_scalars(self):
        data = {
            "your_age": 61,
            "spouse_age": 55,
            "your_ira": 1_700_000,
            "spouse_ira": 1_700_000,
            "your_ss_fra": "70",
            "spouse_ss_fra": "67",
            "living_expenses": 90_000,
            "stock_price_now": 165,
        }
        upd = _build_updates(data, as_spouse=False)
        assert upd["your_age"] == 61
        assert upd["spouse_age"] == 55
        assert upd["your_ira"] == 1_700_000
        assert upd["spouse_ira"] == 1_700_000
        assert upd["living_expenses"] == 90_000
        assert upd["txn_price"] == 165  # stock_price_now → txn_price alias

    def test_me_mode_passes_grant_strikes(self):
        data = {"grant_strikes": {"2019": 104, "2020": 130, "2021": 169}}
        upd = _build_updates(data, as_spouse=False)
        assert upd["_user_grant_strikes"] == {"2019": 104, "2020": 130, "2021": 169}

    def test_spouse_mode_cross_maps_your_to_spouse_only(self):
        data = {
            "your_age": 55,
            "your_ira": 1_700_000,
            "your_ss_fra": "67",
            "spouse_age": 99,  # spouse's view of receiver — must be ignored
            "spouse_ira": 0,
            "living_expenses": 0,  # joint field — ignored in spouse mode
            "stock_price_now": 999,  # ignored
            "grant_strikes": {"2099": 1.0},  # ignored
        }
        upd = _build_updates(data, as_spouse=True)
        assert upd == {
            "spouse_age": 55,
            "spouse_ira": 1_700_000,
            "spouse_ss_fra": "67",
        }

    def test_spouse_mode_partial_data_only_emits_present_keys(self):
        data = {"your_age": 55}
        upd = _build_updates(data, as_spouse=True)
        assert upd == {"spouse_age": 55}

    def test_spouse_mode_empty_data_returns_empty(self):
        assert _build_updates({}, as_spouse=True) == {}


# ---------------------------------------------------------------------------
# merge_snapshots (engine.portfolio_sync)
# ---------------------------------------------------------------------------


class TestMergeSnapshots:
    def test_me_into_empty_returns_incoming_as_is(self):
        incoming = PortfolioSnapshot(
            accounts=[_make_account("you", "A", 100.0)],
            equity_grants=[_make_grant()],
            txn_shares_held=10,
            txn_shares_value=1_650.0,
        )
        merged = merge_snapshots(None, incoming, as_spouse=False)
        assert [a.owner for a in merged.accounts] == ["you"]
        assert merged.equity_grants[0].grant_id == "g1"
        assert merged.txn_shares_held == 10

    def test_spouse_into_empty_rewrites_owner_drops_grants_txn(self):
        incoming = PortfolioSnapshot(
            accounts=[_make_account("you", "B", 200.0)],
            equity_grants=[_make_grant()],  # must be dropped
            txn_shares_held=5,
            txn_shares_value=825.0,
        )
        merged = merge_snapshots(None, incoming, as_spouse=True)
        assert [a.owner for a in merged.accounts] == ["spouse"]
        assert merged.equity_grants == []
        assert merged.txn_shares_held == 0
        assert merged.txn_shares_value == 0.0

    def test_me_into_existing_preserves_spouse_accounts(self):
        existing = PortfolioSnapshot(
            accounts=[
                _make_account("you", "A_old", 50.0),
                _make_account("spouse", "S1", 300.0),
            ],
        )
        incoming = PortfolioSnapshot(
            accounts=[_make_account("you", "A_new", 100.0)],
            equity_grants=[_make_grant()],
        )
        merged = merge_snapshots(existing, incoming, as_spouse=False)
        owners = sorted(a.owner for a in merged.accounts)
        names = {a.account_name for a in merged.accounts}
        assert owners == ["spouse", "you"]
        assert names == {"A_new", "S1"}  # A_old replaced
        assert len(merged.equity_grants) == 1

    def test_spouse_into_existing_preserves_your_grants_txn(self):
        existing = PortfolioSnapshot(
            accounts=[_make_account("you", "Y1", 1000.0)],
            equity_grants=[_make_grant()],
            txn_shares_held=7,
            txn_shares_value=1_155.0,
        )
        incoming = PortfolioSnapshot(
            accounts=[_make_account("you", "S1_was_yours_in_their_file", 400.0)],
            equity_grants=[_make_grant("ignored")],
            txn_shares_held=99,
        )
        merged = merge_snapshots(existing, incoming, as_spouse=True)
        owners_by_name = {a.account_name: a.owner for a in merged.accounts}
        assert owners_by_name["Y1"] == "you"
        assert owners_by_name["S1_was_yours_in_their_file"] == "spouse"
        assert merged.equity_grants[0].grant_id == "g1"
        assert merged.txn_shares_held == 7
        assert merged.txn_shares_value == 1_155.0

    def test_spouse_upload_twice_replaces_prior_spouse_accounts(self):
        first_incoming = PortfolioSnapshot(
            accounts=[_make_account("you", "S_old", 300.0)],
        )
        after_first = merge_snapshots(None, first_incoming, as_spouse=True)
        second_incoming = PortfolioSnapshot(
            accounts=[_make_account("you", "S_new", 350.0)],
        )
        after_second = merge_snapshots(after_first, second_incoming, as_spouse=True)
        names = {a.account_name for a in after_second.accounts}
        assert names == {"S_new"}  # S_old replaced, not appended

    def test_returns_new_object_not_mutating_existing(self):
        existing = PortfolioSnapshot(
            accounts=[_make_account("you", "Y1", 1000.0)],
            equity_grants=[_make_grant()],
        )
        incoming = PortfolioSnapshot(
            accounts=[_make_account("you", "S1", 400.0)],
        )
        merged = merge_snapshots(existing, incoming, as_spouse=True)
        # Existing's accounts list must still have just Y1
        assert len(existing.accounts) == 1
        assert existing.accounts[0].account_name == "Y1"
        # Merged is a new object
        assert merged is not existing
