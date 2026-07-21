"""TXN market-quote fetcher (live-quote growth-projection feature, Part 1).

Pure engine module: network I/O is fine here (mirrors
``engine/portfolio_sync/client.py``), but this module must never import
streamlit — it is called from views, not the other way around.

``fetch_txn_quote`` NEVER raises: any network, HTTP, or parsing failure is
captured into ``QuoteResult.error`` so callers (a future Command Center
"refresh live quote" action) can surface a clean message instead of an
unhandled exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests  # type: ignore[import-untyped]

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class QuoteResult:
    """Outcome of a single market-quote fetch attempt."""

    ticker: str
    price: float | None
    currency: str | None
    fetched_at: datetime | None
    detail: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.price is not None and self.error is None


def fetch_txn_quote(
    ticker: str = "TXN",
    *,
    timeout: float = 5.0,
    session: Any = None,
) -> QuoteResult:
    """Fetch the current market price for *ticker* from Yahoo Finance.

    Never raises: any failure (network exception, non-200 response, missing
    or malformed JSON) is captured as a concise ``QuoteResult.error`` with
    ``price=None``. ``session`` defaults to the top-level ``requests`` module
    (tests monkeypatch it) but accepts anything exposing a ``.get(url,
    headers=, timeout=)`` method, e.g. a ``requests.Session``.
    """
    http = session or requests
    detail = f"Yahoo Finance {ticker}"
    url = _CHART_URL.format(ticker=ticker)

    try:
        resp = http.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
    except requests.RequestException as exc:
        return QuoteResult(
            ticker=ticker, price=None, currency=None, fetched_at=None, detail=detail, error=str(exc)
        )

    if resp.status_code != 200:
        return QuoteResult(
            ticker=ticker,
            price=None,
            currency=None,
            fetched_at=None,
            detail=detail,
            error=f"HTTP {resp.status_code}",
        )

    try:
        payload = resp.json()
        meta = payload["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        currency = meta.get("currency")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return QuoteResult(
            ticker=ticker,
            price=None,
            currency=None,
            fetched_at=None,
            detail=detail,
            error=f"malformed response: {exc}",
        )

    if price is None:
        return QuoteResult(
            ticker=ticker,
            price=None,
            currency=currency,
            fetched_at=None,
            detail=detail,
            error="regularMarketPrice missing",
        )

    try:
        price_value = float(price)
    except (ValueError, TypeError) as exc:
        return QuoteResult(
            ticker=ticker,
            price=None,
            currency=currency,
            fetched_at=None,
            detail=detail,
            error=f"non-numeric price: {exc}",
        )

    return QuoteResult(
        ticker=ticker,
        price=price_value,
        currency=currency,
        fetched_at=datetime.now(UTC),
        detail=detail,
        error=None,
    )
