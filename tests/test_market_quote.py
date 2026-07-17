"""Tests for engine/market_quote.py — TXN live-quote fetcher (Part 1).

Pure engine tests: no streamlit. The HTTP call is monkeypatched via a fake
``session`` object — these tests never hit the network.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import requests

from engine.market_quote import QuoteResult, fetch_txn_quote


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no JSON payload")
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


def _chart_payload(price: float | None, currency: str = "USD") -> dict:
    return {
        "chart": {
            "result": [{"meta": {"regularMarketPrice": price, "currency": currency}}],
        }
    }


class TestFetchTxnQuoteHappyPath:
    def test_parses_regular_market_price(self) -> None:
        session = _FakeSession(_FakeResponse(200, _chart_payload(202.35)))

        result = fetch_txn_quote(session=session)

        assert isinstance(result, QuoteResult)
        assert result.ok is True
        assert result.price == 202.35
        assert result.currency == "USD"
        assert result.error is None
        assert isinstance(result.fetched_at, datetime)
        assert result.ticker == "TXN"

    def test_sends_browser_like_user_agent(self) -> None:
        session = _FakeSession(_FakeResponse(200, _chart_payload(150.0)))

        fetch_txn_quote(session=session)

        assert len(session.calls) == 1
        ua = session.calls[0]["headers"]["User-Agent"]
        assert "Mozilla" in ua

    def test_custom_ticker(self) -> None:
        session = _FakeSession(_FakeResponse(200, _chart_payload(99.0)))

        result = fetch_txn_quote("AAPL", session=session)

        assert result.ticker == "AAPL"
        assert "AAPL" in session.calls[0]["url"]


class TestFetchTxnQuoteFailureModes:
    def test_non_200_status_returns_error(self) -> None:
        session = _FakeSession(_FakeResponse(503, None))

        result = fetch_txn_quote(session=session)

        assert result.ok is False
        assert result.price is None
        assert result.error is not None
        assert "503" in result.error

    def test_network_exception_returns_error(self) -> None:
        session = _FakeSession(exc=requests.ConnectionError("boom"))

        result = fetch_txn_quote(session=session)

        assert result.ok is False
        assert result.price is None
        assert result.error is not None

    def test_malformed_json_returns_error(self) -> None:
        session = _FakeSession(_FakeResponse(200, None))  # .json() raises ValueError

        result = fetch_txn_quote(session=session)

        assert result.ok is False
        assert result.price is None
        assert result.error is not None

    def test_missing_meta_field_returns_error(self) -> None:
        session = _FakeSession(_FakeResponse(200, {"chart": {"result": [{}]}}))

        result = fetch_txn_quote(session=session)

        assert result.ok is False
        assert result.price is None

    def test_none_price_returns_error(self) -> None:
        session = _FakeSession(_FakeResponse(200, _chart_payload(None)))

        result = fetch_txn_quote(session=session)

        assert result.ok is False
        assert result.price is None
        assert result.error is not None

    def test_empty_result_list_returns_error(self) -> None:
        session = _FakeSession(_FakeResponse(200, {"chart": {"result": []}}))

        result = fetch_txn_quote(session=session)

        assert result.ok is False
        assert result.price is None

    @pytest.mark.parametrize(
        "exc",
        [
            requests.ConnectionError("no route"),
            requests.Timeout("timed out"),
            requests.RequestException("generic"),
        ],
    )
    def test_never_raises_on_any_request_exception(self, exc: Exception) -> None:
        session = _FakeSession(exc=exc)

        result = fetch_txn_quote(session=session)  # must not raise

        assert result.ok is False


class TestNoStreamlitImport:
    def test_market_quote_module_does_not_import_streamlit(self) -> None:
        source = Path("engine/market_quote.py").read_text()
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module.split(".")[0])
        assert "streamlit" not in imported_names
