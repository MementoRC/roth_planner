"""Tests for engine.portfolio_sync.holdings.fetch_holdings.

fetch_holdings is a real production entry point (used by portfolio.py) but had
zero direct test coverage, unlike its siblings fetch_equity_awards /
fetch_dividends_rollup which already have redirect-guard tests (see
tests/test_portfolio_sync_options.py::TestAwardsRedirectGuard and
tests/test_portfolio_sync_dividends.py::TestDividendsRollupRedirectGuard).
These tests lock in the current happy-path parse plus the redirect-guard and
exception-swallow safe defaults.
"""

from types import SimpleNamespace

import pytest


class TestFetchHoldingsHappyPath:
    """A well-formed /query/brokerage holdings response parses into rows."""

    def test_fetch_holdings_parses_single_institution_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.portfolio_sync import client as client_module
        from engine.portfolio_sync import fetch_holdings

        payload = {
            "rows": [
                {"symbol": "AAPL", "quantity": 10, "market_value": 1_800.0},
                {"symbol": "TXN", "quantity": 5, "market_value": 950.0},
            ]
        }

        class _FakeResp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return payload

        monkeypatch.setattr(client_module.requests, "get", lambda *a, **kw: _FakeResp())
        result = fetch_holdings()
        assert result == payload["rows"]

    def test_fetch_holdings_parses_multi_institution_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.portfolio_sync import client as client_module
        from engine.portfolio_sync import fetch_holdings

        payload = {
            "institutions": {
                "fidelity": {"rows": [{"symbol": "AAPL", "quantity": 10}]},
                "schwab": {"rows": [{"symbol": "TXN", "quantity": 5}]},
            }
        }

        class _FakeResp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return payload

        monkeypatch.setattr(client_module.requests, "get", lambda *a, **kw: _FakeResp())
        result = fetch_holdings()
        assert {"symbol": "AAPL", "quantity": 10} in result
        assert {"symbol": "TXN", "quantity": 5} in result
        assert len(result) == 2


class TestFetchHoldingsRedirectGuard:
    """S1 — fetch_holdings must not follow 3xx redirects (audit H2), mirroring
    TestAwardsRedirectGuard / TestDividendsRollupRedirectGuard."""

    def test_fetch_holdings_302_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 302 from FinExtract must yield [] for fetch_holdings, not follow the redirect."""
        from engine.portfolio_sync import client as client_module
        from engine.portfolio_sync import fetch_holdings

        def fake_get(url: str, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                status_code=302,
                headers={"Location": "http://attacker.example/steal"},
            )

        monkeypatch.setattr(client_module.requests, "get", fake_get)
        result = fetch_holdings()
        assert result == []

    def test_fetch_holdings_swallows_connection_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A network error must be caught and return [] rather than raise."""
        import requests as req

        from engine.portfolio_sync import client as client_module
        from engine.portfolio_sync import fetch_holdings

        def _raise(*args: object, **kwargs: object) -> None:
            raise req.exceptions.ConnectionError("refused")

        monkeypatch.setattr(client_module.requests, "get", _raise)
        result = fetch_holdings()
        assert result == []

    def test_fetch_holdings_swallows_bad_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A response whose .json() raises ValueError must return [] rather than raise."""
        from engine.portfolio_sync import client as client_module
        from engine.portfolio_sync import fetch_holdings

        class _BadJsonResp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                raise ValueError("not JSON")

        monkeypatch.setattr(client_module.requests, "get", lambda *a, **kw: _BadJsonResp())
        result = fetch_holdings()
        assert result == []
