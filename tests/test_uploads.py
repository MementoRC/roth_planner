"""Tests for upload-time cross-mapping helpers (PR D — two-primary-planners model)."""

from __future__ import annotations

import pytest

from engine.portfolio_sync import (
    AccountSummary,
    EquityGrant,
    Holding,
    PortfolioSnapshot,
    merge_snapshots,
    positions_for_forecast_multi,
)
from engine.upload_merge import build_user_defaults_session_updates, derive_ira_balances


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


# ---------------------------------------------------------------------------
# derive_ira_balances (engine.upload_merge) — owner-filtered IRA extraction
#
# Bug context (PR #39): after a spouse upload, PortfolioSnapshot.pretax_total
# sums ALL is_pretax accounts regardless of owner. app.py feeds pretax_total
# into Household.your_ira, so the spouse IRA is double-counted — once in
# your_ira (via pretax_total) and once in spouse_ira (via user_defaults
# cross-map). The fix is a new derive_ira_balances() helper that filters by
# owner before summing, giving app.py the per-owner values it needs.
# ---------------------------------------------------------------------------


class TestDeriveIraBalances:
    """derive_ira_balances(snap) must return (your_ira, spouse_ira) filtered by owner.

    These tests FAIL on development because derive_ira_balances does not yet
    exist. The "current behavior" assertion (pretax_total) passes today and
    guards that we do not accidentally change the owner-blind combined property.
    """

    def _make_mixed_snapshot(self) -> PortfolioSnapshot:
        """Snapshot with one your pretax, one spouse pretax, one brokerage."""
        return PortfolioSnapshot(
            accounts=[
                _make_account("you", "Your IRA", 1_000_000.0, acct_type="trad_ira"),
                _make_account("spouse", "Spouse IRA", 500_000.0, acct_type="trad_ira"),
                _make_account("you", "Your Brokerage", 200_000.0, acct_type="brokerage"),
            ],
        )

    def test_current_pretax_total_is_owner_blind(self):
        """Documents existing behavior — pretax_total sums both owners.

        This assertion PASSES today. It acts as a guard: if it ever fails,
        something changed the combined-view property that views/portfolio.py
        depends on.
        """
        snap = self._make_mixed_snapshot()
        assert snap.pretax_total == 1_500_000.0

    def test_derive_ira_balances_returns_your_ira(self):
        """derive_ira_balances must return only your-owned pretax total as first element."""
        snap = self._make_mixed_snapshot()
        your_ira, _spouse_ira = derive_ira_balances(snap)
        assert your_ira == 1_000_000.0

    def test_derive_ira_balances_returns_spouse_ira(self):
        """derive_ira_balances must return only spouse-owned pretax total as second element."""
        snap = self._make_mixed_snapshot()
        _your_ira, spouse_ira = derive_ira_balances(snap)
        assert spouse_ira == 500_000.0

    def test_derive_ira_balances_ignores_brokerage(self):
        """Non-pretax accounts must not appear in either balance."""
        snap = self._make_mixed_snapshot()
        your_ira, spouse_ira = derive_ira_balances(snap)
        assert your_ira + spouse_ira == snap.pretax_total

    def test_derive_ira_balances_no_spouse_accounts(self):
        """When no spouse pretax accounts exist, spouse_ira must be zero."""
        snap = PortfolioSnapshot(
            accounts=[
                _make_account("you", "Your IRA", 1_700_000.0, acct_type="trad_ira"),
            ],
        )
        your_ira, spouse_ira = derive_ira_balances(snap)
        assert your_ira == 1_700_000.0
        assert spouse_ira == 0.0


# ---------------------------------------------------------------------------
# brokerage aggregation helpers (engine.portfolio_sync)
#
# Bug context (PR #39 + app.py:570): account_by_type("brokerage") returns the
# FIRST matching account regardless of owner. After a spouse upload, the spouse's
# brokerage is silently dropped from the dividend forecast — the engine only sees
# the receiver's own account. Because Household has a single brokerage_growth slot
# (not a per-owner pair like IRAs), the fix is to AGGREGATE across all owners:
# joint MAGI drives Roth conversion headroom, so spouse's brokerage dividends
# close part of that window too.
#
# Pre-existing latent bug: multiple brokerage accounts under the same owner are
# also dropped by account_by_type — these new helpers fix that case too.
#
# Desired contract (to be implemented as a follow-up — these tests FAIL now):
#   snap.brokerage_accounts          — all type=="brokerage" accounts, all owners
#   snap.brokerage_total             — sum of total_value across all
#   snap.brokerage_weighted_return   — balance-weighted average weighted_return
#   positions_for_forecast_multi(accounts) — flat Position list from multiple accounts
# ---------------------------------------------------------------------------


def _make_holding(symbol: str, market_value: float, account_name: str = "acct") -> Holding:
    return Holding(
        symbol=symbol,
        description=symbol,
        quantity=1.0,
        market_value=market_value,
        account_name=account_name,
        asset_class="equity",
    )


def _make_mixed_brokerage_snapshot() -> PortfolioSnapshot:
    """Snapshot with one you-brokerage ($1M equity), one spouse-brokerage ($500K bond),
    and one you-pretax IRA ($800K) to confirm it's filtered OUT of brokerage helpers.

    Allocations are chosen so weighted_return values differ:
      - you brokerage: 100% equity → weighted_return == EXPECTED_RETURNS["equity"] (0.09)
      - spouse brokerage: 100% bond → weighted_return == EXPECTED_RETURNS["bond"] (0.04)
    """
    return PortfolioSnapshot(
        accounts=[
            AccountSummary(
                account_type="brokerage",
                owner="you",
                account_name="Your Brokerage",
                total_value=1_000_000.0,
                equity_value=1_000_000.0,
            ),
            AccountSummary(
                account_type="brokerage",
                owner="spouse",
                account_name="Spouse Brokerage",
                total_value=500_000.0,
                bond_value=500_000.0,
            ),
            AccountSummary(
                account_type="trad_ira",
                owner="you",
                account_name="Your IRA",
                total_value=800_000.0,
                equity_value=800_000.0,
            ),
        ],
    )


class TestBrokerageAggregation:
    def test_brokerage_accounts_returns_both_owners(self):
        """brokerage_accounts must include all type=='brokerage' accounts from all owners,
        and must exclude pretax IRA accounts.
        """
        snap = _make_mixed_brokerage_snapshot()
        accounts = snap.brokerage_accounts
        assert len(accounts) == 2
        types = {a.account_type for a in accounts}
        assert types == {"brokerage"}

    def test_brokerage_total_sums_both_owners(self):
        """brokerage_total must sum total_value across all owners' brokerage accounts."""
        snap = _make_mixed_brokerage_snapshot()
        assert snap.brokerage_total == 1_500_000.0

    def test_brokerage_weighted_return_is_balance_weighted(self):
        """brokerage_weighted_return must be a balance-weighted average of each
        account's weighted_return (mirrors pretax_weighted_return formula).

        Fixture: you=$1M all-equity (0.09), spouse=$500K all-bond (0.04).
        Expected: (1_000_000 * 0.09 + 500_000 * 0.04) / 1_500_000 ≈ 0.07333...
        """
        snap = _make_mixed_brokerage_snapshot()
        brok_accts = snap.brokerage_accounts
        total = sum(a.total_value for a in brok_accts)
        expected = sum(a.total_value * a.weighted_return for a in brok_accts) / total
        assert snap.brokerage_weighted_return == pytest.approx(expected)

    def test_positions_for_forecast_multi_concatenates(self):
        """positions_for_forecast_multi must return a flat Position list combining
        holdings from ALL supplied accounts, preserving per-account symbol identity.
        """
        brok1 = AccountSummary(
            account_type="brokerage",
            owner="you",
            account_name="Yours",
            total_value=100_000.0,
            equity_value=100_000.0,
            holdings=[
                _make_holding("VTI", 60_000.0, "Yours"),
                _make_holding("VXUS", 40_000.0, "Yours"),
            ],
        )
        brok2 = AccountSummary(
            account_type="brokerage",
            owner="spouse",
            account_name="Spouses",
            total_value=80_000.0,
            equity_value=80_000.0,
            holdings=[
                _make_holding("VOO", 80_000.0, "Spouses"),
            ],
        )
        positions = positions_for_forecast_multi([brok1, brok2])
        assert len(positions) == 3
        tickers = {p.ticker for p in positions}
        assert tickers == {"VTI", "VXUS", "VOO"}

    def test_multi_brokerage_single_owner(self):
        """Two brokerage accounts owned by 'you' must both appear in brokerage_accounts.

        This also validates the pre-existing latent bug: account_by_type() only
        returns the FIRST match, silently dropping any additional accounts of the
        same type. brokerage_accounts must return ALL of them regardless of owner.
        """
        snap = PortfolioSnapshot(
            accounts=[
                _make_account("you", "Brokerage-A", 600_000.0, acct_type="brokerage"),
                _make_account("you", "Brokerage-B", 400_000.0, acct_type="brokerage"),
            ],
        )
        assert len(snap.brokerage_accounts) == 2
        assert snap.brokerage_total == 1_000_000.0

    def test_brokerage_helpers_empty(self):
        """With no brokerage accounts, brokerage_total must be 0.0, brokerage_accounts
        must be [], and brokerage_weighted_return must be 0.0.

        Empty-case mirrors pretax_weighted_return: when total <= 0 return 0.0
        (see PortfolioSnapshot.pretax_weighted_return L179-184).
        """
        snap = PortfolioSnapshot(
            accounts=[
                _make_account("you", "Your IRA", 1_000_000.0, acct_type="trad_ira"),
            ],
        )
        assert snap.brokerage_accounts == []
        assert snap.brokerage_total == 0.0
        # Mirror pretax_weighted_return: 0.0 when no accounts (total <= 0)
        assert snap.brokerage_weighted_return == 0.0
