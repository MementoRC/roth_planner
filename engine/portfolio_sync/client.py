"""FinExtract HTTP client — base URL, auth token, headers, response normalization."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


BASE_URL = os.environ.get("FINEXTRACT_URL", "http://127.0.0.1:7890")


def _load_token() -> str:
    """Resolve FinExtract bearer token.

    Order: FINEXTRACT_TOKEN env, FINEXT_TOKEN env, ~/.finextract/auth-token file.
    Re-evaluated on every call so a token written after Streamlit launch is picked up.
    """
    tok = os.environ.get("FINEXTRACT_TOKEN") or os.environ.get("FINEXT_TOKEN")
    if tok:
        return tok.strip()
    p = Path.home() / ".finextract" / "auth-token"
    if p.is_file():
        try:
            return p.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    tok = _load_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _flatten_query_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract rows from a FinExtract /query response.

    Handles both shapes:
    - Single-institution: {..., "rows": [...]}
    - Multi-institution: {"institutions": {"<inst>": {"rows": [...]}, ...}}
    """
    if "institutions" in data and isinstance(data["institutions"], dict):
        return [
            row
            for batch in data["institutions"].values()
            if isinstance(batch, dict)
            for row in batch.get("rows", [])
        ]
    rows: list[dict[str, Any]] = data.get("rows", []) or []
    return rows
