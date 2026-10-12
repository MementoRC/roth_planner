"""FinExtract HTTP client — base URL, auth token, headers, response normalization."""

from __future__ import annotations

import ipaddress
import logging
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests  # type: ignore[import-untyped]

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)


BASE_URL = os.environ.get("FINEXTRACT_URL", "http://127.0.0.1:7890")


_LOCAL_HOSTS = frozenset({"localhost", "0.0.0.0", "::"})
"""Host names/addresses that are always safe without HTTPS.

Includes:
- "localhost" — hostname alias for loopback
- "0.0.0.0"  — wildcard bind address; only reachable from the same host
- "::"        — IPv6 wildcard; same rationale as 0.0.0.0
Any other numeric IP is checked via ipaddress.ip_address(host).is_loopback,
which covers 127.0.0.1, 127.0.0.2 … 127.255.255.255, ::1, etc.
"""


def _token_transport_is_safe(base_url: str) -> bool:
    """True if a bearer token may be sent to *base_url* without plaintext exposure.

    Safe when the target host is loopback (any scheme) or the scheme is HTTPS.
    A non-local HTTP endpoint would transmit the token in cleartext, so it is
    treated as unsafe and the Authorization header is omitted by the caller.

    Loopback detection (crypto-security-0 / crypto-security-5):
    - Explicit membership in _LOCAL_HOSTS for names and wildcard addresses.
    - ipaddress.ip_address(host).is_loopback for all other numeric addresses
      (covers 127.0.0.1, 127.0.0.2…127.255.255.255, ::1, etc.).
    """
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if host in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    return parsed.scheme == "https"


def _sanitise_env_token(tok: str) -> str:
    """Strip whitespace and reject tokens containing embedded CR or LF.

    crypto-security-3: An attacker who can inject a newline into the env var
    could split HTTP headers.  Treat any such token as absent and log an error.
    """
    cleaned = tok.strip()
    if "\r" in cleaned or "\n" in cleaned:
        _log.error(
            "FINEXTRACT_TOKEN / FINEXT_TOKEN contains embedded CR or LF — "
            "rejecting token to prevent header-injection. Fix the token value."
        )
        return ""
    return cleaned


def _load_token() -> str:
    """Resolve FinExtract bearer token.

    Order: FINEXTRACT_TOKEN env, FINEXT_TOKEN env, ~/.finextract/auth-token file.
    Re-evaluated on every call so a token written after Streamlit launch is picked up.
    """
    raw_tok = os.environ.get("FINEXTRACT_TOKEN") or os.environ.get("FINEXT_TOKEN")
    if raw_tok:
        return _sanitise_env_token(raw_tok)
    p = Path.home() / ".finextract" / "auth-token"
    if p.is_file():
        try:
            # crypto-security-2 + crypto-security-9: open first (O_NOFOLLOW
            # rejects symlinks atomically), then fstat the live descriptor to
            # avoid a TOCTOU race between stat() and open().
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                _mode = os.fstat(fd).st_mode
                if _mode & 0o077:
                    # SEC-02: fail CLOSED — lax perms on a token file are a
                    # security risk; refuse to load the token rather than
                    # warning and continuing (fail-open).
                    _log.warning(
                        "%s is group/world-accessible (mode %#o); refusing to load "
                        "bearer token. Restrict it with: chmod 600 %s",
                        p,
                        stat.S_IMODE(_mode),
                        p,
                    )
                    return ""
                raw = os.read(fd, 65536)
            finally:
                os.close(fd)
            # SEC-03: route file token through _sanitise_env_token so embedded
            # CR/LF (e.g. from a Windows-edited file) cannot reach the
            # Authorization header and split HTTP headers.
            return _sanitise_env_token(raw.decode("utf-8"))
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


def _get(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float,
) -> requests.Response:
    """GET ``BASE_URL + path`` with auth headers and redirects disabled.

    Token-bearing FinExtract requests must never auto-follow a 3xx to an
    attacker-controlled ``Location`` (audit H2). ``allow_redirects=False`` plus an
    explicit 3xx->error guard keeps the redirect policy and :func:`_headers` from
    drifting apart across call sites. Raising :class:`requests.HTTPError` (a
    ``RequestException`` subclass) means existing ``except requests.RequestException``
    handlers treat an unexpected redirect as a normal transport failure.
    """
    resp = requests.get(
        f"{BASE_URL}{path}",
        params=params,
        headers=_headers(),
        timeout=timeout,
        allow_redirects=False,
    )
    if 300 <= resp.status_code < 400:
        raise requests.HTTPError(
            f"Unexpected redirect ({resp.status_code}) from {path}; refusing to follow.",
            response=resp,
        )
    return resp


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
