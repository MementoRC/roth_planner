"""Tests for engine.portfolio_sync — FinExtract integration, classification, response shapes."""

import json

import pytest

from models.grants import StockGrant
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestPortfolioSync:
    """Test portfolio sync parsing and classification logic."""

    def test_classify_brokerage_account(self):
        from engine.portfolio_sync import _classify_account

        acct_type, owner = _classify_account("Claude R. Cirba — Brokerage Account — 39119320*")
        assert acct_type == "brokerage"
        assert owner == "you"

    def test_classify_roth_ira(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Claude R. Cirba — Roth IRA Brokerage Account — 61037368*")
        assert acct_type == "roth_ira"

    def test_classify_trad_ira(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Some Person — Traditional IRA — 12345678*")
        assert acct_type == "trad_ira"

    def test_classify_rollover_ira(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Rollover IRA233813501")
        assert acct_type == "trad_ira"

    def test_classify_403b(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("VANDERBILT 403B59208")
        assert acct_type == "403b"

    def test_classify_hsa(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Health Savings Account178734462")
        assert acct_type == "hsa"

    def test_classify_symbols(self):
        from engine.portfolio_sync import _classify_symbol

        assert _classify_symbol("VTI") == "equity"
        assert _classify_symbol("VXUS") == "equity"
        assert _classify_symbol("BND") == "bond"
        assert _classify_symbol("BNDX") == "bond"
        assert _classify_symbol("ITOT") == "equity"
        assert _classify_symbol("AGG") == "bond"
        assert _classify_symbol("FBTC") == "crypto"
        assert _classify_symbol("SHV") == "cash"
        assert _classify_symbol("Cash HELD IN MONEY MARKET") == "cash"
        assert _classify_symbol("VTTHX") == "target_date"
        assert _classify_symbol("UNKNOWN") == "equity"  # default

    def test_parse_quantity(self):
        from engine.portfolio_sync import _parse_quantity

        assert _parse_quantity(100) == 100.0
        assert _parse_quantity(3.14) == 3.14
        assert _parse_quantity("2,182.861") == approx(2182.861, tol=0.001)
        assert _parse_quantity(None) == 0.0
        assert _parse_quantity("") == 0.0

    def test_account_summary_weighted_return(self):
        from engine.portfolio_sync import AccountSummary

        acct = AccountSummary(
            account_type="brokerage",
            owner="you",
            total_value=100_000,
            equity_value=60_000,
            bond_value=40_000,
        )
        # 60% * 9% + 40% * 4% = 5.4% + 1.6% = 7.0%
        assert acct.weighted_return == approx(0.07, tol=0.001)
        assert acct.equity_pct == approx(0.60, tol=0.001)

    def test_account_summary_with_crypto_and_cash(self):
        from engine.portfolio_sync import AccountSummary

        acct = AccountSummary(
            account_type="trad_ira",
            owner="you",
            total_value=200_000,
            equity_value=80_000,
            bond_value=40_000,
            cash_value=40_000,
            crypto_value=40_000,
        )
        # 80k*9% + 40k*4% + 40k*4.5% + 40k*0% = 7200+1600+1800+0 = 10600
        # 10600/200000 = 5.3%
        assert acct.weighted_return == approx(0.053, tol=0.001)

    def test_account_summary_empty(self):
        from engine.portfolio_sync import AccountSummary

        acct = AccountSummary(account_type="brokerage", owner="you")
        assert acct.weighted_return == 0.0
        assert acct.equity_pct == 0.0

    def test_pretax_accounts(self):
        from engine.portfolio_sync import AccountSummary, PortfolioSnapshot

        snap = PortfolioSnapshot(
            accounts=[
                AccountSummary(
                    account_type="trad_ira",
                    owner="you",
                    total_value=1_500_000,
                    equity_value=500_000,
                    bond_value=500_000,
                    cash_value=500_000,
                ),
                AccountSummary(
                    account_type="403b",
                    owner="you",
                    total_value=140_000,
                    equity_value=100_000,
                    bond_value=40_000,
                ),
                AccountSummary(account_type="hsa", owner="you", total_value=60_000),
                AccountSummary(account_type="brokerage", owner="you", total_value=100_000),
            ],
            server_available=True,
        )
        assert len(snap.pretax_accounts) == 2
        assert snap.pretax_total == approx(1_640_000)
        assert snap.pretax_weighted_return > 0


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


class TestQueryResponseShape:
    """Verify _flatten_query_rows handles both FinExtract response shapes."""

    def test_single_institution_legacy_shape(self):
        from engine.portfolio_sync import _flatten_query_rows

        data = {
            "domain": "brokerage",
            "data_type": "holdings",
            "rows": [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
        }
        assert _flatten_query_rows(data) == [{"symbol": "AAPL"}, {"symbol": "MSFT"}]

    def test_multi_institution_current_shape(self):
        from engine.portfolio_sync import _flatten_query_rows

        data = {
            "domain": "brokerage",
            "data_type": "holdings",
            "institutions": {
                "fidelity": {"rows": [{"symbol": "AAPL"}]},
                "schwab": {"rows": [{"symbol": "MSFT"}, {"symbol": "TXN"}]},
            },
        }
        result = _flatten_query_rows(data)
        # Order across institutions is dict-iteration order — assert as a set / sorted
        assert sorted(r["symbol"] for r in result) == ["AAPL", "MSFT", "TXN"]
        assert len(result) == 3

    def test_empty_institutions(self):
        from engine.portfolio_sync import _flatten_query_rows

        data = {"institutions": {}}
        assert _flatten_query_rows(data) == []

    def test_neither_rows_nor_institutions(self):
        from engine.portfolio_sync import _flatten_query_rows

        # FinExtract returning no data at all should yield [] not raise
        data = {"domain": "brokerage", "data_type": "holdings"}
        assert _flatten_query_rows(data) == []

    def test_institutions_value_not_dict(self):
        from engine.portfolio_sync import _flatten_query_rows

        # Robustness: malformed nested batch should be skipped, not raise
        data = {
            "institutions": {"fidelity": "not-a-dict", "schwab": {"rows": [{"symbol": "MSFT"}]}}
        }
        result = _flatten_query_rows(data)
        assert result == [{"symbol": "MSFT"}]

    def test_institution_batch_missing_rows_key(self):
        from engine.portfolio_sync import _flatten_query_rows

        # If one institution's batch has no 'rows' key, skip silently rather than KeyError
        data = {
            "institutions": {
                "fidelity": {"metadata": "blah"},  # no 'rows' key
                "schwab": {"rows": [{"symbol": "MSFT"}]},
            },
        }
        assert _flatten_query_rows(data) == [{"symbol": "MSFT"}]


class TestAccountTypeOverrides:
    """Verify _classify_account honors user-supplied overrides."""

    def test_override_hit_returns_mapped_type(self):
        from engine.portfolio_sync import _classify_account

        assert _classify_account("U1234567", overrides={"U1234567": "trad_ira"}) == (
            "trad_ira",
            "you",
        )

    def test_override_miss_falls_through_to_substring_scan(self):
        from engine.portfolio_sync import _classify_account

        # Override exists for a different account; the queried name has 'ira' → substring match
        result = _classify_account("Rollover IRA233813501", overrides={"U1234567": "trad_ira"})
        assert result == ("trad_ira", "you")

    def test_empty_overrides_preserves_legacy_behavior(self):
        from engine.portfolio_sync import _classify_account

        assert _classify_account("Rollover IRA233813501") == ("trad_ira", "you")
        assert _classify_account("Individual Brokerage Account") == ("brokerage", "you")

    def test_overrides_supports_multiple_ibkr_accounts(self):
        from engine.portfolio_sync import _classify_account

        overrides = {"U1234567": "trad_ira", "U7654321": "roth_ira", "U9999999": "brokerage"}
        assert _classify_account("U1234567", overrides=overrides) == ("trad_ira", "you")
        assert _classify_account("U7654321", overrides=overrides) == ("roth_ira", "you")
        assert _classify_account("U9999999", overrides=overrides) == ("brokerage", "you")

    def test_override_can_force_brokerage_classification(self):
        from engine.portfolio_sync import _classify_account

        # Even an 'ira'-containing name can be overridden to brokerage if user knows better
        result = _classify_account(
            "Inheritance IRA Account",
            overrides={"Inheritance IRA Account": "brokerage"},
        )
        assert result == ("brokerage", "you")


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


class TestOptionExercisesFetchAndApply:
    """Verify fetch_option_exercises + apply_option_exercises end-to-end."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fake_resp(self, status_code: int, payload: dict):
        """Build a minimal requests.Response stub."""

        class _Resp:
            def __init__(self, code, data):
                self.status_code = code
                self._data = data

            def json(self):
                return self._data

        return _Resp(status_code, payload)

    def _one_row(
        self,
        grant_price: float = 104.0,
        execution_quantity: float = 1000.0,
        gross_proceeds: float = 200_000.0,
        grant_number: str = "G1",
    ) -> dict:
        return {
            "grant_price": grant_price,
            "execution_quantity": execution_quantity,
            "gross_proceeds": gross_proceeds,
            "grant_number": grant_number,
        }

    # ------------------------------------------------------------------
    # fetch_option_exercises tests
    # ------------------------------------------------------------------

    def test_multi_institution_shape_parsed(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {
            "domain": "equity_compensation",
            "data_type": "order_detail_summary",
            "institutions": {
                "UBS": {
                    "rows": [self._one_row()],
                    "captured_at": "2026-03-15T10:00:00Z",
                }
            },
        }
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.rows_count == 1
        expected_spread = 200_000.0 - 104.0 * 1000.0
        assert abs(snap.total_spread - expected_spread) < 0.01

    def test_single_institution_shape_parsed(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {"rows": [self._one_row()]}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.rows_count == 1
        expected_spread = 200_000.0 - 104.0 * 1000.0
        assert abs(snap.total_spread - expected_spread) < 0.01

    def test_empty_rows_zero_spread(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {"institutions": {}}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.total_spread == 0.0
        assert snap.rows_count == 0

    def test_same_day_sale_math(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        # gross=200000, grant_price=104, qty=1000 → spread=200000 - 104*1000 = 96000
        payload = {"rows": [self._one_row(gross_proceeds=200_000.0)]}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert abs(snap.total_spread - 96_000.0) < 0.01

    def test_per_grant_aggregation(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        row1 = self._one_row(
            grant_price=104.0,
            execution_quantity=500.0,
            gross_proceeds=100_000.0,
            grant_number="G1",
        )
        row2 = self._one_row(
            grant_price=104.0,
            execution_quantity=300.0,
            gross_proceeds=60_000.0,
            grant_number="G1",
        )
        payload = {"rows": [row1, row2]}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        # Both rows same grant_number → summed in by_grant_id["G1"]
        assert "G1" in snap.by_grant_id
        spread1 = 100_000.0 - 104.0 * 500.0
        spread2 = 60_000.0 - 104.0 * 300.0
        assert abs(snap.by_grant_id["G1"] - (spread1 + spread2)) < 0.01
        assert snap.rows_count == 2

    def test_per_grant_fallback_when_id_empty(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {"rows": [self._one_row(grant_number="")]}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        # Empty grant_number → contributes to total but NOT to by_grant_id
        assert snap.total_spread > 0.0
        assert snap.by_grant_id == {}

    def test_404_empty_snapshot_server_available(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(404, {}))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.total_spread == 0.0
        assert snap.rows_count == 0
        assert snap.error == ""

    def test_captured_at_propagated_from_multi_institution(self, monkeypatch):
        """captured_at from first institution batch is surfaced on the snapshot."""
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {
            "institutions": {
                "UBS": {
                    "rows": [self._one_row()],
                    "captured_at": "2026-06-10T12:00:00Z",
                }
            }
        }
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.captured_at == "2026-06-10T12:00:00Z"

    def test_captured_at_empty_for_single_institution_shape(self, monkeypatch):
        """Single-institution (rows-only) shape has no captured_at — defaults to empty string."""
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        payload = {"rows": [self._one_row()]}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.captured_at == ""

    # ------------------------------------------------------------------
    # mode=history aggregation tests
    # ------------------------------------------------------------------

    def test_mode_history_aggregates_across_batches(self, monkeypatch):
        """mode=history: rows from all batches are combined, not just the latest."""
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        batches = [
            {
                "batch_id": "b1",
                "captured_at": "2026-06-01T10:00:00Z",
                "row_count": 3,
                "rows": [
                    self._one_row(gross_proceeds=200_000.0),
                    self._one_row(gross_proceeds=200_000.0),
                    self._one_row(gross_proceeds=200_000.0),
                ],
            },
            {
                "batch_id": "b2",
                "captured_at": "2026-06-05T10:00:00Z",
                "row_count": 1,
                "rows": [self._one_row(gross_proceeds=200_000.0)],
            },
            {
                "batch_id": "b3",
                "captured_at": "2026-06-10T10:00:00Z",
                "row_count": 1,
                "rows": [self._one_row(gross_proceeds=200_000.0)],
            },
        ]
        payload = {"batches": batches}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.rows_count == 5
        expected_spread = 5 * (200_000.0 - 104.0 * 1000.0)
        assert abs(snap.total_spread - expected_spread) < 0.01

    def test_mode_history_latest_captured_at_picked(self, monkeypatch):
        """mode=history: snapshot captured_at reflects the most recent batch timestamp."""
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        batches = [
            {
                "batch_id": "b1",
                "captured_at": "2026-06-01T10:00:00Z",
                "rows": [self._one_row()],
            },
            {
                "batch_id": "b2",
                "captured_at": "2026-06-10T12:00:00Z",
                "rows": [self._one_row()],
            },
            {
                "batch_id": "b3",
                "captured_at": "2026-06-05T08:00:00Z",
                "rows": [self._one_row()],
            },
        ]
        payload = {"batches": batches}
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.captured_at == "2026-06-10T12:00:00Z"

    def test_mode_history_fallback_to_legacy_shape(self, monkeypatch):
        """When response has no batches key, falls back to legacy _flatten_query_rows path."""
        import requests as req

        from engine.portfolio_sync import fetch_option_exercises

        # Legacy multi-institution shape — no "batches" key
        payload = {
            "institutions": {
                "UBS": {
                    "rows": [self._one_row()],
                    "captured_at": "2026-06-10T12:00:00Z",
                }
            }
        }
        monkeypatch.setattr(req, "get", lambda *a, **kw: self._fake_resp(200, payload))
        snap = fetch_option_exercises()
        assert snap.server_available is True
        assert snap.rows_count == 1
        expected_spread = 200_000.0 - 104.0 * 1000.0
        assert abs(snap.total_spread - expected_spread) < 0.01

    # ------------------------------------------------------------------
    # apply_option_exercises grant_id normalization tests
    # ------------------------------------------------------------------

    def test_grant_id_match_case_insensitive(self):
        """Household grant_id 'GR-2019'; UBS sends 'gr2019' — normalizes to same key."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(
                    year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"
                )
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=96_000.0,
            by_grant_id={"gr2019": 96_000.0},
        )
        ytd_snap = apply_option_exercises(YTDSnapshot(), exercises, hh)
        # Key remapped to household format; no warning
        assert "GR-2019" in exercises.by_grant_id
        assert "gr2019" not in exercises.by_grant_id
        assert exercises.warnings == []
        assert ytd_snap.nqo_exercise_ytd == 96_000.0

    def test_grant_id_match_strips_special_chars(self):
        """Household grant_id 'GR-2019'; UBS sends 'GR2019' (no dash) — normalized match."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(
                    year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"
                )
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=96_000.0,
            by_grant_id={"GR2019": 96_000.0},
        )
        apply_option_exercises(YTDSnapshot(), exercises, hh)
        assert "GR-2019" in exercises.by_grant_id
        assert "GR2019" not in exercises.by_grant_id
        assert exercises.warnings == []

    def test_grant_id_unmatched_warning_and_total_preserved(self):
        """Unmatched grant_id keeps raw key, emits warning, total_spread unchanged."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(
                    year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"
                )
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=50_000.0,
            by_grant_id={"GR-OTHER": 50_000.0},
        )
        ytd_snap = apply_option_exercises(YTDSnapshot(), exercises, hh)
        assert "GR-OTHER" in exercises.by_grant_id
        assert len(exercises.warnings) == 1
        assert "GR-OTHER" in exercises.warnings[0]
        assert ytd_snap.nqo_exercise_ytd == 50_000.0

    def test_grant_id_prefix_substring_match(self):
        """Household grant_id 'N0000197825'; UBS sends '197825' — tier 3 substring match."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(
                    year=2021, strike=169.0, shares=500, expiry_year=2031, grant_id="N0000197825"
                )
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=75_000.0,
            by_grant_id={"197825": 75_000.0},
        )
        ytd_snap = apply_option_exercises(YTDSnapshot(), exercises, hh)
        assert "N0000197825" in exercises.by_grant_id
        assert "197825" not in exercises.by_grant_id
        assert exercises.warnings == []
        assert ytd_snap.nqo_exercise_ytd == 75_000.0

    def test_grant_id_substring_picks_longest_on_ambiguity(self):
        """Two grants 'N1234' and 'N00001234' both contain '1234'; UBS sends '1234' — picks longer."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(year=2020, strike=130.0, shares=300, expiry_year=2030, grant_id="N1234"),
                StockGrant(
                    year=2021, strike=169.0, shares=400, expiry_year=2031, grant_id="N00001234"
                ),
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=40_000.0,
            by_grant_id={"1234": 40_000.0},
        )
        apply_option_exercises(YTDSnapshot(), exercises, hh)
        # Longest normalized match: "N00001234" (9 chars) beats "N1234" (5 chars)
        assert "N00001234" in exercises.by_grant_id
        assert "N1234" not in exercises.by_grant_id
        assert "1234" not in exercises.by_grant_id

    def test_grant_id_short_substring_does_not_match(self):
        """UBS sends '19' (2 chars after normalization) — below 3-char threshold, no substring match."""
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            apply_option_exercises,
        )
        from models.ytd_income import YTDSnapshot

        hh = Household(
            grants=[
                StockGrant(
                    year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"
                )
            ]
        )
        exercises = OptionExercisesSnapshot(
            server_available=True,
            total_spread=20_000.0,
            by_grant_id={"19": 20_000.0},
        )
        apply_option_exercises(YTDSnapshot(), exercises, hh)
        assert "19" in exercises.by_grant_id
        assert len(exercises.warnings) == 1
        assert "19" in exercises.warnings[0]

    def test_load_path_migration_legacy_cache(self, tmp_path, monkeypatch):
        import json

        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot

        cache_file = tmp_path / "ytd_legacy.json"
        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", cache_file)

        # Write a cache dict that deliberately omits nqo_exercise_ytd
        legacy_data = {
            "tax_year": 2026,
            "snapshot_date": "2026-03-01",
            "wages_ytd": 80_000.0,
            "nec_income_ytd": 0.0,
            "ira_conversions_ytd": 0.0,
            "ira_distributions_ytd": 0.0,
            "ltcg_ytd": 0.0,
            "stcg_ytd": 0.0,
            "qualified_dividends_ytd": 0.0,
            "ordinary_dividends_ytd": 0.0,
            "interest_ytd": 0.0,
            "gain_events": [],
            "manually_entered": True,
            # nqo_exercise_ytd intentionally absent
        }
        cache_file.write_text(json.dumps(legacy_data))

        result = load_ytd_snapshot()
        assert result is not None
        assert result.nqo_exercise_ytd == 0.0

    def test_sale_info_by_grant_populated_from_rows(self):
        """_parse_option_exercises_rows populates sale_info_by_grant with grant_year/strike/shares_ytd."""
        from engine.portfolio_sync import _parse_option_exercises_rows

        rows = [
            {
                "grant_number": "G2019",
                "grant_price": 104.0,
                "execution_quantity": 500.0,
                "gross_proceeds": 100_000.0,
                "grant_date": "2019-03-10",
            },
            {
                "grant_number": "G2019",
                "grant_price": 104.0,
                "execution_quantity": 300.0,
                "gross_proceeds": 60_000.0,
                "grant_date": "2019-03-10",
            },
        ]
        snap = _parse_option_exercises_rows(rows)
        info = snap.sale_info_by_grant.get("G2019", {})
        assert info.get("grant_year") == 2019
        assert abs(info.get("strike", 0) - 104.0) < 0.01
        assert info.get("shares_ytd") == 800  # 500 + 300


class TestEquitySalesCacheConsumer:
    """Verify _parse_equity_sales_lots + fetch_option_exercises_with_cache."""

    def _lot(
        self,
        grant_number: str = "N0000197825",
        grant_price: float = 169.0,
        execution_quantity: str = "100",
        gross_proceeds: float = 24400.0,
    ) -> dict:
        return {
            "grant_number": grant_number,
            "grant_price": grant_price,
            "execution_quantity": execution_quantity,
            "gross_proceeds": gross_proceeds,
        }

    def test_parses_lots_with_string_quantities(self):
        from engine.portfolio_sync import _parse_equity_sales_lots

        lots = [self._lot()]
        snap = _parse_equity_sales_lots(lots)
        assert snap.server_available is True
        assert snap.rows_count == 1
        # 24400 - 169 * 100 = 7500
        assert abs(snap.total_spread - 7500.0) < 0.01
        assert abs(snap.by_grant_id["N0000197825"] - 7500.0) < 0.01

    def test_parses_multiple_lots_per_execution(self):
        from engine.portfolio_sync import _parse_equity_sales_lots

        # 3 lots sharing same grant_number — handoff doc: lots >= executions
        lots = [
            self._lot(execution_quantity="50", gross_proceeds=12200.0),
            self._lot(execution_quantity="30", gross_proceeds=7320.0),
            self._lot(execution_quantity="20", gross_proceeds=4880.0),
        ]
        snap = _parse_equity_sales_lots(lots)
        assert snap.rows_count == 3
        # spreads: 12200-8450=3750, 7320-5070=2250, 4880-3380=1500 → total 7500
        assert abs(snap.total_spread - 7500.0) < 0.01
        assert abs(snap.by_grant_id["N0000197825"] - 7500.0) < 0.01

    def test_empty_lots_returns_empty_snapshot(self):
        from engine.portfolio_sync import _parse_equity_sales_lots

        snap = _parse_equity_sales_lots([])
        assert snap.total_spread == 0.0
        assert snap.rows_count == 0
        assert snap.server_available is True
        assert snap.by_grant_id == {}

    def test_skips_zero_quantity_lots(self):
        from engine.portfolio_sync import _parse_equity_sales_lots

        lots = [self._lot(execution_quantity="0")]
        snap = _parse_equity_sales_lots(lots)
        assert snap.total_spread == 0.0
        assert snap.rows_count == 0
        assert snap.warnings == []

    def test_skips_negative_spread_with_warning(self):
        from engine.portfolio_sync import _parse_equity_sales_lots

        # gross < strike * qty → negative spread
        lots = [self._lot(grant_price=200.0, execution_quantity="100", gross_proceeds=1000.0)]
        snap = _parse_equity_sales_lots(lots)
        assert snap.total_spread == 0.0
        assert snap.rows_count == 0
        assert len(snap.warnings) == 1
        assert "negative spread" in snap.warnings[0]

    def test_fallback_to_query_when_no_lots(self, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import (
            OptionExercisesSnapshot,
            PortfolioSnapshot,
            fetch_option_exercises_with_cache,
        )

        fallback_snap = OptionExercisesSnapshot(server_available=True, total_spread=99.0)
        called = []

        def fake_fetch_option_exercises():
            called.append(True)
            return fallback_snap

        monkeypatch.setattr(portfolio_sync, "fetch_option_exercises", fake_fetch_option_exercises)

        snapshot = PortfolioSnapshot(equity_sales_lots=[])
        result = fetch_option_exercises_with_cache(snapshot)
        assert called == [True]
        assert result.total_spread == 99.0

    def test_uses_captured_at_from_snapshot(self):
        from engine.portfolio_sync import (
            PortfolioSnapshot,
            fetch_option_exercises_with_cache,
        )

        ts = "2026-06-11T22:30Z"
        snapshot = PortfolioSnapshot(
            equity_sales_lots=[self._lot()],
            order_detail_summary_captured_at=ts,
        )
        result = fetch_option_exercises_with_cache(snapshot)
        assert result.captured_at == ts

    def test_save_snapshot_preserves_existing_equity_sales(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import PortfolioSnapshot, save_snapshot

        cache = tmp_path / ".portfolio_cache.json"
        monkeypatch.setattr(portfolio_sync, "_CACHE_PATH", cache)

        # Simulate FinExtract's rebuild write — equity_sales and sources on disk.
        finextract_data = {
            "equity_sales": {
                "lots": [{"grant_number": "N0000197825", "grant_price": 169.0}],
                "executions": [{"id": "E001"}],
            },
            "sources": {
                "order_detail_summary": {"captured_at": "2026-06-10T12:00Z"},
            },
        }
        cache.write_text(json.dumps(finextract_data))

        # Live HTTP sync produces a snap with empty equity_sales_lots.
        snap = PortfolioSnapshot(equity_sales_lots=[], equity_sales_executions=[])
        save_snapshot(snap)

        result = json.loads(cache.read_text())
        assert "equity_sales" in result
        assert result["equity_sales"]["lots"] == [
            {"grant_number": "N0000197825", "grant_price": 169.0}
        ]
        assert result["equity_sales"]["executions"] == [{"id": "E001"}]
        assert result["sources"]["order_detail_summary"]["captured_at"] == "2026-06-10T12:00Z"

    def test_save_snapshot_no_equity_sales_keys_in_new_file(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import PortfolioSnapshot, save_snapshot

        cache = tmp_path / ".portfolio_cache.json"
        monkeypatch.setattr(portfolio_sync, "_CACHE_PATH", cache)

        # No pre-existing file — fresh save should not write equity_sales or sources.
        snap = PortfolioSnapshot(equity_sales_lots=[], equity_sales_executions=[])
        save_snapshot(snap)

        result = json.loads(cache.read_text())
        assert "equity_sales" not in result
        assert "equity_sales_lots" not in result
        assert "equity_sales_executions" not in result
        assert "order_detail_summary_captured_at" not in result
        # sources may be absent or present but must not contain order_detail_summary
        sources = result.get("sources", {})
        assert "order_detail_summary" not in sources

    def test_sale_info_by_grant_populated_per_lot(self):
        """sale_info_by_grant carries grant_year, strike, and cumulative shares_ytd per grant."""
        from engine.portfolio_sync import _parse_equity_sales_lots

        lots = [
            {
                "grant_number": "N0000197825",
                "grant_price": 169.0,
                "execution_quantity": "100",
                "gross_proceeds": 24400.0,
                "grant_date": "2021-01-15",
            },
            {
                "grant_number": "N0000197825",
                "grant_price": 169.0,
                "execution_quantity": "50",
                "gross_proceeds": 12200.0,
                "grant_date": "2021-01-15",
            },
        ]
        snap = _parse_equity_sales_lots(lots)
        info = snap.sale_info_by_grant.get("N0000197825", {})
        assert info.get("grant_year") == 2021
        assert abs(info.get("strike", 0) - 169.0) < 0.01
        assert info.get("shares_ytd") == 150  # 100 + 50


class TestFetchMultiInstitutionShape:
    """Verify fetch_tax_return and fetch_ytd_snapshot handle multi-institution response shape."""

    def _fake_resp(self, status_code: int, payload: dict):
        class _Resp:
            def __init__(self, code, data):
                self.status_code = code
                self._data = data

            def json(self):
                return self._data

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise Exception(f"HTTP {self.status_code}")

        return _Resp(status_code, payload)

    def test_fetch_tax_return_multi_institution_income(self, monkeypatch):
        """fetch_tax_return income endpoint: multi-institution shape rows are flattened."""
        import requests as req

        from engine.portfolio_sync import fetch_tax_return

        income_payload = {
            "institutions": {
                "turbotax": {
                    "rows": [
                        {"form_label": "wages/w-2", "amount_current": 120_000, "amount_prior": 0}
                    ]
                }
            }
        }
        deduction_payload = {"rows": []}
        responses = [
            self._fake_resp(200, {}),  # /status check
            self._fake_resp(200, income_payload),
            self._fake_resp(200, deduction_payload),
        ]
        call_iter = iter(responses)
        monkeypatch.setattr(req, "get", lambda *a, **kw: next(call_iter))
        snap = fetch_tax_return()
        assert snap.wages == 120_000

    def test_fetch_tax_return_multi_institution_deductions(self, monkeypatch):
        """fetch_tax_return deductions endpoint: multi-institution shape rows are flattened."""
        import requests as req

        from engine.portfolio_sync import fetch_tax_return

        income_payload = {"rows": []}
        deduction_payload = {
            "institutions": {
                "turbotax": {
                    "rows": [
                        {
                            "form_label": "hsa contribution",
                            "amount_current": 8_300,
                            "amount_prior": 0,
                        }
                    ]
                }
            }
        }
        responses = [
            self._fake_resp(200, {}),  # /status check
            self._fake_resp(200, income_payload),
            self._fake_resp(200, deduction_payload),
        ]
        call_iter = iter(responses)
        monkeypatch.setattr(req, "get", lambda *a, **kw: next(call_iter))
        snap = fetch_tax_return()
        assert snap.hsa_contributions == 8_300

    def test_fetch_ytd_snapshot_multi_institution_investment_income(self, monkeypatch):
        """fetch_ytd_snapshot investment_income endpoint: multi-institution shape rows are flattened."""
        import requests as req

        from engine.portfolio_sync import fetch_ytd_snapshot

        def _resp_for(url, params=None, **kw):
            data_type = (params or {}).get("data_type", "")
            if "status" in url:
                return self._fake_resp(200, {})
            if data_type == "realized_gains":
                return self._fake_resp(200, {"rows": []})
            if data_type == "investment_income":
                return self._fake_resp(
                    200,
                    {
                        "institutions": {
                            "fidelity": {
                                "rows": [
                                    {"received_dividends": 3_500.0, "received_interest": 200.0}
                                ]
                            }
                        }
                    },
                )
            if data_type == "ytd_income":
                return self._fake_resp(200, {"rows": []})
            return self._fake_resp(200, {})

        monkeypatch.setattr(req, "get", _resp_for)
        snap = fetch_ytd_snapshot()
        assert snap.ordinary_dividends_ytd == 3_500.0
        assert snap.interest_ytd == 200.0

    def test_fetch_ytd_snapshot_multi_institution_ytd_income(self, monkeypatch):
        """fetch_ytd_snapshot ytd_income endpoint: multi-institution shape rows are flattened."""
        import requests as req

        from engine.portfolio_sync import fetch_ytd_snapshot

        def _resp_for(url, params=None, **kw):
            data_type = (params or {}).get("data_type", "")
            if "status" in url:
                return self._fake_resp(200, {})
            if data_type == "realized_gains":
                return self._fake_resp(200, {"rows": []})
            if data_type == "investment_income":
                return self._fake_resp(200, {"rows": []})
            if data_type == "ytd_income":
                return self._fake_resp(
                    200,
                    {
                        "institutions": {
                            "turbotax": {"rows": [{"label": "wages", "amount": 95_000.0}]}
                        }
                    },
                )
            return self._fake_resp(200, {})

        monkeypatch.setattr(req, "get", _resp_for)
        snap = fetch_ytd_snapshot()
        assert snap.wages_ytd == 95_000.0

    # ------------------------------------------------------------------
    # apply_magi tests
    # ------------------------------------------------------------------

    def _make_snap(self):
        from datetime import UTC, datetime

        from engine.portfolio_sync import MagiSnapshot

        return MagiSnapshot(fetched_at=datetime.now(UTC))

    def test_apply_magi_populates_prior_year_magi_and_agi(self):
        from engine.portfolio_sync import apply_magi

        snap = self._make_snap()
        data = {
            "year": 2024,
            "filing_status": "MFJ",
            "agi": 180_000.0,
            "magi": 183_000.0,
        }
        result = apply_magi(snap, data)
        assert result.prior_year_magi[2024] == pytest.approx(183_000.0)
        assert result.agi[2024] == pytest.approx(180_000.0)
        assert result.filing_status[2024] == "MFJ"

    def test_apply_magi_none_input_no_op(self):
        from engine.portfolio_sync import apply_magi

        snap = self._make_snap()
        result = apply_magi(snap, None)
        assert result.prior_year_magi == {}
        assert result.agi == {}

    def test_apply_magi_missing_optional_fields(self):
        from engine.portfolio_sync import apply_magi

        snap = self._make_snap()
        # Only year + magi; no agi or filing_status
        data = {"year": 2023, "magi": 175_000.0}
        result = apply_magi(snap, data)
        assert result.prior_year_magi[2023] == pytest.approx(175_000.0)
        assert 2023 not in result.agi
        assert 2023 not in result.filing_status

    def test_apply_magi_invalid_year_no_op(self):
        from engine.portfolio_sync import apply_magi

        snap = self._make_snap()
        for bad_year in ("abc", None):
            data = {"year": bad_year, "magi": 150_000.0}
            result = apply_magi(snap, data)
            assert result.prior_year_magi == {}
            assert result.agi == {}
