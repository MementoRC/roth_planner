"""Equity compensation awards + shares fetch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

if TYPE_CHECKING:
    pass

from .client import BASE_URL, _flatten_query_rows, _headers


def fetch_equity_awards() -> list[dict[str, Any]]:
    """Fetch equity compensation awards."""
    try:
        resp = requests.get(
            f"{BASE_URL}/query/equity_comp",
            params={"data_type": "equity_awards"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return _flatten_query_rows(data)
    except (requests.RequestException, ValueError):
        return []


def fetch_shares() -> list[dict[str, Any]]:
    """Fetch equity compensation shares held."""
    try:
        resp = requests.get(
            f"{BASE_URL}/query/equity_comp",
            params={"data_type": "shares"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return _flatten_query_rows(data)
    except (requests.RequestException, ValueError):
        return []
