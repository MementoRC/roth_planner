"""Equity compensation awards + shares fetch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

if TYPE_CHECKING:
    pass

from .client import _flatten_query_rows, _get


def _fetch_equity_compensation(data_type: str) -> list[dict[str, Any]] | None:
    """Rows of one equity-compensation query, or None when the fetch failed.

    None and [] mean different things: [] is a successful answer holding
    nothing, None is "no answer". A caller that treats a failed fetch as an
    empty one saves "no grants, no shares" over the cached values
    (audit-1101 F-11).
    """
    try:
        resp = _get(
            "/query/equity_compensation",
            params={"data_type": data_type},
            timeout=5,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return _flatten_query_rows(data)
    except (requests.RequestException, ValueError):
        return None


def fetch_equity_awards() -> list[dict[str, Any]] | None:
    """Fetch equity compensation awards; None when the fetch failed."""
    return _fetch_equity_compensation("equity_awards")


def fetch_shares() -> list[dict[str, Any]] | None:
    """Fetch equity compensation shares held; None when the fetch failed."""
    return _fetch_equity_compensation("shares")
