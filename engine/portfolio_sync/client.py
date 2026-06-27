"""FinExtract HTTP client — base URL, auth token, headers, response normalization."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)


BASE_URL = os.environ.get("FINEXTRACT_URL", "http://127.0.0.1:7890")


_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _token_transport_is_safe(base_url: str) -> bool:
    """True if a bearer token may be sent to *base_url* without plaintext exposure.

    Safe when the target host is loopback (any scheme) or the scheme is HTTPS.
    A non-local HTTP endpoint would transmit the token in cleartext, so it is
    treated as unsafe and the Authorization header is omitted by the caller.
    """
    parsed = urlparse(base_url)
    if parsed.hostname in _LOCAL_HOSTS:
        return True
    return parsed.scheme == "https"


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
        except OSError as exc:
            _log.warning("Could not read FinExtract auth token from %s: %s", p, exc)
            return ""
    return ""


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    tok = _load_token()
    if tok:
        if _token_transport_is_safe(BASE_URL):
            h["Authorization"] = f"Bearer {tok}"
        else:
            _log.warning(
                "Refusing to attach FinExtract bearer token: %s is neither a "
                "loopback host nor HTTPS, so the token would be sent in cleartext. "
                "Authorization header omitted. Use https:// or a loopback host.",
                BASE_URL,
            )
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
