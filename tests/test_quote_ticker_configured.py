"""The live-quote fetch requests the household's configured ticker.

The Option Exercises button reads "Fetch <ticker> quote" from ``_stock_ticker``
(the same source Profile › Assumptions labels with); the Yahoo request must
use that same ticker rather than an engine default.
"""

from __future__ import annotations

from typing import Any

import pytest
import streamlit as st

import engine.market_quote as market_quote_mod
from config.loader import load_defaults
from engine.market_quote import QuoteResult
from views.option_exercise._partials._helpers import configured_ticker, handle_stock_quote_fetch


def _ok(ticker: str) -> QuoteResult:
    return QuoteResult(ticker, 123.45, "USD", None, f"stub {ticker}")


def test_fetcher_receives_configured_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st, "session_state", {"_stock_ticker": "ZZZZ"})
    seen: list[str] = []

    def _fetcher(ticker: str) -> QuoteResult:
        seen.append(ticker)
        return _ok(ticker)

    handle_stock_quote_fetch(fetcher=_fetcher)

    assert seen == ["ZZZZ"]


def test_default_fetch_requests_configured_ticker_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st, "session_state", {"_stock_ticker": "ZZZZ"})
    urls: list[str] = []

    class _Resp:
        status_code = 200

        def json(self) -> Any:
            return {"chart": {"result": [{"meta": {"regularMarketPrice": 10.0}}]}}

    def _get(url: str, **_kw: Any) -> _Resp:
        urls.append(url)
        return _Resp()

    monkeypatch.setattr(market_quote_mod.requests, "get", _get)

    result = handle_stock_quote_fetch()

    assert result.ticker == "ZZZZ"
    assert len(urls) == 1
    assert urls[0].endswith("/ZZZZ")


def test_configured_ticker_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st, "session_state", {})
    assert configured_ticker() == load_defaults()["stock_ticker"]
