"""audit-0823 PS-2: a dividend rollup total must not be duplicated per account.

``/query/brokerage?data_type=dividends_rollup`` returns ONE household-wide total
per symbol: ``fetch_dividends_rollup`` (engine/portfolio_sync/dividends.py) issues a
single unparameterised request, so the response has no account dimension and cannot
be scoped to one account.  ``apply_dividends_rollup`` nevertheless stamped that whole
total onto EVERY holding of the symbol in EVERY account, while ``forecast_portfolio``
deliberately SUMS same-ticker positions across accounts (audit-0720 L1) -- so a symbol
held in N brokerage accounts had its projected dividend income multiplied by N.

The inflated figure reaches ``hh.brokerage_growth.yield_rate`` via
engine/data_sources/snapshot_ingest.py:134 and raises projected MAGI in every
projection year, eroding IRMAA/NIIT headroom that does not need defending.

These gates assert on the FORECAST OUTPUT rather than on the stamped
``dividends_by_year`` field, so a fix that merely relabels the intermediate without
correcting the money cannot pass them.
"""

from __future__ import annotations

import pytest

# A 365-day window makes _derive_ttm_dividends' window-actualisation
# (sum * 365 / window_days) a no-op, so the expected dollars stay exact.
WINDOW = {"from": "2025-01-01", "to": "2026-01-01"}

HOUSEHOLD_DIVIDEND = 1_560.0
"""True household-wide 2025 dividend for AAPL, as FinExtract reports it."""

TOTAL_SHARES = 600.0
TOTAL_VALUE = 150_000.0


def _holding(symbol: str, shares: float, value: float, account_name: str):
    from engine.portfolio_sync import Holding

    return Holding(
        symbol=symbol,
        description=symbol,
        quantity=shares,
        market_value=value,
        account_name=account_name,
        asset_class="equity",
    )


def _account(account_name: str, owner: str, holdings: list):
    from engine.portfolio_sync import AccountSummary

    return AccountSummary(
        account_type="brokerage",
        owner=owner,
        account_name=account_name,
        total_value=sum(h.market_value for h in holdings),
        holdings=holdings,
    )


def _snapshot(accounts: list):
    from engine.portfolio_sync import PortfolioSnapshot

    return PortfolioSnapshot(accounts=accounts, server_available=True)


def _rollup(by_symbol: dict):
    from engine.portfolio_sync import DividendsRollupSnapshot

    return DividendsRollupSnapshot(
        server_available=True,
        by_symbol=by_symbol,
        window=WINDOW,
        freshness={"is_stale": False},
    )


def _aapl_rollup(total: float = HOUSEHOLD_DIVIDEND):
    return _rollup({"AAPL": {"by_year": {"2025": {"total": total, "count": 4}}}})


def _split_across_two_accounts():
    """600 AAPL shares / $150,000, split 400/200 between two brokerages."""
    return _snapshot(
        [
            _account("Fidelity", "you", [_holding("AAPL", 400.0, 100_000.0, "Fidelity")]),
            _account("Schwab", "spouse", [_holding("AAPL", 200.0, 50_000.0, "Schwab")]),
        ]
    )


def _held_in_one_account():
    """The identical 600 shares / $150,000, in a single brokerage account."""
    return _snapshot(
        [_account("Fidelity", "you", [_holding("AAPL", TOTAL_SHARES, TOTAL_VALUE, "Fidelity")])]
    )


def _run_forecast(snap, tmp_path):
    """Drive the real production path: apply rollup -> positions -> forecast.

    ``overrides_path`` is pinned to a non-existent file inside tmp_path so a
    ``.dividend_rates.json`` in the developer's working tree cannot silently
    supply an annual_rate override and mask the defect.
    """
    from engine.dividend_forecast import forecast_portfolio
    from engine.portfolio_sync import apply_dividends_rollup, positions_for_forecast_multi

    apply_dividends_rollup(snap, _aapl_rollup())
    return forecast_portfolio(
        positions_for_forecast_multi(snap.brokerage_accounts),
        total_balance=snap.brokerage_total,
        overrides_path=tmp_path / "no-such-overrides.json",
    )


class TestDividendNotMultipliedPerAccount:
    def test_split_position_does_not_multiply_projected_dividend(self, tmp_path):
        """The household received $1,560. Splitting it 400/200 must not report $3,120."""
        snap = _split_across_two_accounts()
        assert len(snap.brokerage_accounts) == 2, "fixture precondition: two brokerage accounts"

        fcst = _run_forecast(snap, tmp_path)

        assert fcst.per_position["AAPL"]["annual_div"] == pytest.approx(HOUSEHOLD_DIVIDEND)

    def test_split_position_does_not_inflate_yield_rate(self, tmp_path):
        """yield_rate feeds hh.brokerage_growth and thus projected MAGI every year."""
        fcst = _run_forecast(_split_across_two_accounts(), tmp_path)

        assert fcst.yield_rate == pytest.approx(HOUSEHOLD_DIVIDEND / TOTAL_VALUE)

    def test_forecast_is_invariant_to_how_shares_are_split(self, tmp_path):
        """The load-bearing invariant: WHERE the shares sit cannot change the money.

        Same 600 shares, same $150,000, same $1,560 household dividend -- only the
        account boundary differs. Any answer that varies with the split is wrong by
        construction, whatever the absolute figure.
        """
        one = _run_forecast(_held_in_one_account(), tmp_path)
        two = _run_forecast(_split_across_two_accounts(), tmp_path)

        assert two.yield_rate == pytest.approx(one.yield_rate)
        assert two.per_position["AAPL"]["annual_div"] == pytest.approx(
            one.per_position["AAPL"]["annual_div"]
        )

    def test_single_account_case_is_unchanged(self, tmp_path):
        """Guard the no-op case: one holding must still receive the whole total."""
        fcst = _run_forecast(_held_in_one_account(), tmp_path)

        assert fcst.per_position["AAPL"]["annual_div"] == pytest.approx(HOUSEHOLD_DIVIDEND)


class TestPerHoldingAllocation:
    def test_allocation_across_holdings_sums_to_household_total(self):
        """Whatever the per-holding split, the parts must reconstitute the whole."""
        from engine.portfolio_sync import apply_dividends_rollup

        snap = _split_across_two_accounts()
        apply_dividends_rollup(snap, _aapl_rollup())

        holdings = [h for acct in snap.accounts for h in acct.holdings]
        allocated = sum((h.dividends_by_year or {}).get("2025", 0.0) for h in holdings)

        assert allocated == pytest.approx(HOUSEHOLD_DIVIDEND)

    def test_allocation_is_proportional_to_shares(self):
        """Dividends are paid per share, so a 400/200 split must land 2:1."""
        from engine.portfolio_sync import apply_dividends_rollup

        snap = _split_across_two_accounts()
        apply_dividends_rollup(snap, _aapl_rollup())

        by_account = {
            h.account_name: (h.dividends_by_year or {}).get("2025", 0.0)
            for acct in snap.accounts
            for h in acct.holdings
        }

        assert by_account["Fidelity"] == pytest.approx(HOUSEHOLD_DIVIDEND * 400.0 / TOTAL_SHARES)
        assert by_account["Schwab"] == pytest.approx(HOUSEHOLD_DIVIDEND * 200.0 / TOTAL_SHARES)

    def test_zero_share_holdings_do_not_divide_by_zero(self):
        """A symbol whose every holding has zero shares must allocate nothing, not crash."""
        from engine.portfolio_sync import apply_dividends_rollup

        snap = _snapshot(
            [_account("Fidelity", "you", [_holding("AAPL", 0.0, 0.0, "Fidelity")])]
        )
        apply_dividends_rollup(snap, _aapl_rollup())

        holding = snap.accounts[0].holdings[0]
        assert (holding.dividends_by_year or {}).get("2025", 0.0) == pytest.approx(0.0)
