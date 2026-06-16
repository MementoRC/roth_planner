"""MAGI lookup fetch + apply."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

if TYPE_CHECKING:
    pass

from .client import BASE_URL, _headers
from .shapes import MagiSnapshot


def fetch_magi(year: int, *, timeout: float = 3.0) -> dict[str, Any] | None:
    """Fetch MAGI for a single tax year from FinExtract.

    Endpoint: GET /query/tax_return?data_type=magi&year=YYYY
    Returns a flat single-object response dict on 200. Returns None on 404
    (year outside the 2-year coverage window), HTTP error, network error, or
    malformed shape. Errors are swallowed so callers can batch fetches across
    multiple years without try/except plumbing.

    Response shape (per A3 contract):
        {year, filing_status, agi, magi, tax_exempt_interest,
         ss_taxable_amount, foreign_earned_income_exclusion, source}
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/query/tax_return",
            params={"data_type": "magi", "year": str(year)},
            headers=_headers(),
            timeout=timeout,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def apply_magi(snap: MagiSnapshot, magi: dict[str, Any] | None) -> MagiSnapshot:
    """Merge a fetch_magi response into snap. Tolerates None (no-op).

    Reads `year` (required), `magi` (preferred IRMAA-MAGI), `agi`, and
    `filing_status` from the response dict. Missing optional fields are
    skipped silently. Malformed year is skipped silently.
    """
    if not magi:
        return snap
    year_raw = magi.get("year")
    if year_raw is None:
        return snap
    try:
        year = int(year_raw)
    except (TypeError, ValueError):
        return snap

    magi_val = magi.get("magi")
    if magi_val is not None:
        with contextlib.suppress(TypeError, ValueError):
            snap.prior_year_magi[year] = float(magi_val)

    agi_val = magi.get("agi")
    if agi_val is not None:
        with contextlib.suppress(TypeError, ValueError):
            snap.agi[year] = float(agi_val)

    fs = magi.get("filing_status")
    if fs is not None:
        snap.filing_status[year] = str(fs)

    return snap
