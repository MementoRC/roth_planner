"""Tests for engine.portfolio_sync — dividend forecast, rollup fetch/apply, TTM derivation."""

import pytest


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestDividendForecast:
    """Tests for engine.dividend_forecast."""

    def test_empty_portfolio_returns_zero(self):
        from engine.dividend_forecast import forecast_portfolio

        fcst = forecast_portfolio([], total_balance=0.0)
        assert fcst.yield_rate == 0.0
        assert fcst.qualified_fraction == 1.0

    def test_ttm_strategy(self):
        """TTM derivation: per-position dividends history → yield."""
        from engine.dividend_forecast import Position, forecast_portfolio

        positions = [
            Position(ticker="TXN", shares=1000, balance=200_000, ttm_dividends=5400),
        ]
        fcst = forecast_portfolio(positions, total_balance=200_000)
        # ttm_per_share = 5400/1000 = 5.4; annual_income = 1000 * 5.4 = 5400
        # yield_rate = 5400 / 200_000 = 0.027; TXN is equity → qualified_fraction = 1.0
        assert fcst.yield_rate == pytest.approx(0.027)
        assert fcst.qualified_fraction == pytest.approx(1.0)
        assert fcst.source_counts["ttm"] == 1

    def test_mixed_qualified_classifications(self):
        """REIT contributes ordinary; equity contributes qualified."""
        from engine.dividend_forecast import Position, forecast_portfolio

        positions = [
            Position(ticker="TXN", shares=500, balance=100_000, ttm_dividends=2700),  # 2.7% qual
            Position(ticker="VNQ", shares=100, balance=10_000, ttm_dividends=400),  # 4% ord (REIT)
        ]
        fcst = forecast_portfolio(positions, total_balance=110_000)
        # TXN: annual_income=2700, qual_frac=1.0 → qualified=2700, ordinary=0
        # VNQ: annual_income=400, qual_frac=0.0 → qualified=0, ordinary=400
        # total=3100, yield=3100/110_000, qual_frac=2700/3100
        assert fcst.yield_rate == pytest.approx(3100 / 110_000)
        assert fcst.qualified_fraction == pytest.approx(2700 / 3100)

    def test_no_history_uses_none_strategy(self):
        """No TTM data, no override, no yfinance → none."""
        from engine.dividend_forecast import Position, forecast_portfolio

        positions = [
            Position(ticker="NEWSTOCK", shares=100, balance=10_000, ttm_dividends=0.0),
        ]
        fcst = forecast_portfolio(positions, total_balance=10_000)
        assert fcst.yield_rate == 0.0
        assert fcst.source_counts["none"] == 1


class TestDeriveTtmDividendsMostRecentYear:
    """Regression for audit E-5: _derive_ttm_dividends must use the most-recent
    prior year's value, not the highest value across all years."""

    def test_derive_ttm_dividends_returns_most_recent_year(self):
        """Declining dividend stream: returns 2024 value (1_400), not max (2_400)."""
        from engine.portfolio_sync import Holding, _derive_ttm_dividends

        h = Holding(
            symbol="T",
            description="AT&T",
            quantity=100.0,
            market_value=10_000.0,
            account_name="Brokerage",
            asset_class="equity",
            dividends_by_year={"2022": 2_400.0, "2023": 1_800.0, "2024": 1_400.0},
            dividends_window=None,
        )
        # Buggy code returned max(values) = 2_400; correct is prior[max(keys)] = 1_400
        result = _derive_ttm_dividends(h)
        assert result == 1_400.0, f"Expected 1400.0 (most recent year), got {result}"

    def test_derive_ttm_dividends_empty_prior_returns_zero(self):
        """No prior-year data at all → 0.0 (unchanged behaviour)."""
        from engine.portfolio_sync import Holding, _derive_ttm_dividends

        h = Holding(
            symbol="T",
            description="AT&T",
            quantity=100.0,
            market_value=10_000.0,
            account_name="Brokerage",
            asset_class="equity",
            dividends_by_year={},
            dividends_window=None,
        )
        assert _derive_ttm_dividends(h) == 0.0


class TestDividendsRollupFetchAndMap:
    """Verify fetch_dividends_rollup + apply_dividends_rollup end-to-end."""

    # ------------------------------------------------------------------
    # fetch_dividends_rollup tests
    # ------------------------------------------------------------------

    def test_fetch_handles_server_unavailable(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_dividends_rollup

        def _raise(*args, **kwargs):
            raise req.exceptions.ConnectionError("refused")

        monkeypatch.setattr(req, "get", _raise)
        result = fetch_dividends_rollup()
        assert result.server_available is False
        assert result.error is not None

    def test_fetch_parses_rollup_payload(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_dividends_rollup

        payload = {
            "rollup": {
                "by_symbol": {
                    "AAPL": {"by_year": {"2024": {"total": 423.50, "count": 4}}},
                    "FBTC": {"by_year": {"2024": {"total": 0.0, "count": 0}}},
                },
                "window": {"from": "2024-06-01", "to": "2025-12-15", "months_covered_approx": 18.5},
                "freshness": {"is_stale": False, "as_of": "2025-12-20T00:00:00Z"},
                "classification": {},
                "per_institution_counts": {},
            }
        }

        class _FakeResp:
            status_code = 200

            def json(self):
                return payload

        monkeypatch.setattr(req, "get", lambda *a, **kw: _FakeResp())
        result = fetch_dividends_rollup()
        assert result.server_available is True
        assert "AAPL" in result.by_symbol
        assert result.window == {
            "from": "2024-06-01",
            "to": "2025-12-15",
            "months_covered_approx": 18.5,
        }
        assert result.freshness["is_stale"] is False

    # ------------------------------------------------------------------
    # apply_dividends_rollup tests
    # ------------------------------------------------------------------

    def _make_snap(self, symbols: list[str]) -> object:
        """Build a minimal PortfolioSnapshot with one holding per symbol."""
        from engine.portfolio_sync import (
            AccountSummary,
            Holding,
            PortfolioSnapshot,
        )

        holdings = [
            Holding(
                symbol=sym,
                description=sym,
                quantity=10.0,
                market_value=1000.0,
                account_name="Test",
                asset_class="equity",
            )
            for sym in symbols
        ]
        acct = AccountSummary(
            account_type="brokerage",
            owner="you",
            account_name="Test",
            total_value=1000.0 * len(symbols),
            holdings=holdings,
        )
        return PortfolioSnapshot(accounts=[acct], server_available=True)

    def _make_rollup(
        self,
        by_symbol: dict,
        window: dict | None = None,
        is_stale: bool = False,
        server_available: bool = True,
    ) -> object:
        from engine.portfolio_sync import DividendsRollupSnapshot

        return DividendsRollupSnapshot(
            server_available=server_available,
            by_symbol=by_symbol,
            window=window or {"from": "2024-06-01", "to": "2025-12-15"},
            freshness={"is_stale": is_stale},
        )

    @property
    def _all_holdings(self):
        """Flatten all holdings from a snapshot for easy assertion."""

        def _get(snap):
            return [h for acct in snap.accounts for h in acct.holdings]

        return _get

    def test_apply_extracts_total_from_value_objects(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL"])
        rollup = self._make_rollup(
            by_symbol={"AAPL": {"by_year": {"2024": {"total": 423.5, "count": 4}}}},
        )
        apply_dividends_rollup(snap, rollup)
        holding = self._all_holdings(snap)[0]
        assert holding.dividends_by_year == {"2024": 423.5}

    def test_apply_renames_window_from_to_start_end(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL"])
        rollup = self._make_rollup(
            by_symbol={"AAPL": {"by_year": {"2024": {"total": 100.0, "count": 2}}}},
            window={"from": "2024-06-01", "to": "2025-12-15"},
        )
        apply_dividends_rollup(snap, rollup)
        holding = self._all_holdings(snap)[0]
        assert holding.dividends_window == {"start": "2024-06-01", "end": "2025-12-15"}
        assert "from" not in holding.dividends_window
        assert "to" not in holding.dividends_window

    def test_apply_propagates_freshness_to_all_holdings(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL", "FBTC"])
        rollup = self._make_rollup(
            by_symbol={
                "AAPL": {"by_year": {"2024": {"total": 423.5, "count": 4}}},
                "FBTC": {"by_year": {"2024": {"total": 0.0, "count": 0}}},
            },
            is_stale=True,
        )
        apply_dividends_rollup(snap, rollup)
        holdings = self._all_holdings(snap)
        assert all(h.dividends_is_stale is True for h in holdings)

    def test_apply_skips_holdings_not_in_rollup(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL", "ZZZZ"])
        rollup = self._make_rollup(
            by_symbol={"AAPL": {"by_year": {"2024": {"total": 100.0, "count": 2}}}},
        )
        apply_dividends_rollup(snap, rollup)
        holdings = {h.symbol: h for h in self._all_holdings(snap)}
        assert holdings["AAPL"].dividends_by_year == {"2024": 100.0}
        assert holdings["ZZZZ"].dividends_by_year is None
        assert holdings["ZZZZ"].dividends_window is None
        assert holdings["ZZZZ"].dividends_is_stale is None

    def test_apply_handles_empty_rollup_gracefully(self):
        from engine.portfolio_sync import DividendsRollupSnapshot, apply_dividends_rollup

        snap = self._make_snap(["AAPL"])
        rollup = DividendsRollupSnapshot(server_available=True, by_symbol={})
        result = apply_dividends_rollup(snap, rollup)
        holding = self._all_holdings(result)[0]
        assert holding.dividends_by_year is None

    def test_apply_handles_server_unavailable_rollup(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL"])
        rollup = self._make_rollup(
            by_symbol={"AAPL": {"by_year": {"2024": {"total": 999.0, "count": 4}}}},
            server_available=False,
        )
        result = apply_dividends_rollup(snap, rollup)
        holding = self._all_holdings(result)[0]
        # server_available=False → snapshot returned unchanged
        assert holding.dividends_by_year is None

    def test_apply_symbol_lookup_is_case_insensitive(self):
        from engine.portfolio_sync import apply_dividends_rollup

        snap = self._make_snap(["AAPL"])
        # FinExtract emits lowercase symbol
        rollup = self._make_rollup(
            by_symbol={"aapl": {"by_year": {"2024": {"total": 423.5, "count": 4}}}},
        )
        apply_dividends_rollup(snap, rollup)
        holding = self._all_holdings(snap)[0]
        assert holding.dividends_by_year == {"2024": 423.5}
