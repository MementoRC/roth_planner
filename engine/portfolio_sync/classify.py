"""Account/symbol classification helpers (owner, type, quantity, overrides)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from .shapes import ASSET_CLASS

_OWNER_HINT_MAP: dict[str, str] = {
    "primary": "you",
    "secondary": "spouse",
    "joint": "you",
    "trust": "you",
    "you": "you",
    "spouse": "spouse",
}


def _resolve_owner_hint(owner_raw: object) -> str | None:
    """Map a normalized owner-field value to 'you' / 'spouse', or None."""
    if owner_raw is None:
        return None
    key = str(owner_raw).strip().lower()
    if not key:
        return None
    return _OWNER_HINT_MAP.get(key)


def _resolve_override(entry: str | dict[str, str]) -> tuple[str, str]:
    """Return (account_type, owner) from either override form.

    Accepts both the legacy flat string form and the new nested dict form:
    - ``"trad_ira"``  →  ``("trad_ira", "you")``
    - ``{"type": "trad_ira", "owner": "spouse"}``  →  ``("trad_ira", "spouse")``

    ``"owner"`` defaults to ``"you"`` when absent from the dict.
    """
    if isinstance(entry, dict):
        return entry.get("type", ""), entry.get("owner", "you")
    return entry, "you"


_NAME_SEPARATOR = " — "


def _account_type_text(account_name: str) -> str:
    """The part of *account_name* that describes the account type.

    Vanguard names are ``<Owner full name> — <Account type> — <number>*``; the
    owner segment is dropped so a name like "Dorothy" or "Mira" cannot decide
    the type (audit-1008 F-12). Names without that separator are used whole.
    """
    parts = account_name.split(_NAME_SEPARATOR)
    if len(parts) >= 3:
        return _NAME_SEPARATOR.join(parts[1:])
    return account_name


def _has_word(text: str, word: str) -> bool:
    """True when *word* appears in *text* not flanked by other letters.

    Digits may touch it, so Fidelity's "Rollover IRA999000002" still matches
    "ira"; "Hiram" and "Dorothy" do not.
    """
    return re.search(rf"(?<![a-z]){word}(?![a-z])", text) is not None


def _classify_account(
    account_name: str,
    overrides: dict[str, str | dict[str, str]] | None = None,
    *,
    owner_hint: object = None,
) -> tuple[str, str]:
    """Determine account type and owner from account name string.

    Handles Vanguard ("John Q. Sample — Roth IRA Brokerage Account — ..."),
    Fidelity ("Rollover IRA999000002"), and 403b/HSA patterns.

    ``overrides`` is an optional mapping of raw account name/ID → either a
    canonical account type string (legacy flat form, e.g. ``{"U1234567":
    "trad_ira"}``) or a dict carrying both type and owner (new nested form,
    e.g. ``{"U1234567": {"type": "trad_ira", "owner": "spouse"}}``).  When a
    match is found the override is returned immediately, bypassing the
    substring scan.  This supports FinExtract IBKR accounts whose names
    are raw account IDs with no "ira" substring.

    ``owner_hint`` accepts the FinExtract normalized ``owner`` field
    (``primary`` / ``secondary`` / ``joint`` / ``trust`` / ``unknown`` plus
    legacy ``you`` / ``spouse``).  It wins over the substring-scan default
    but loses to a dict-shaped override that already carries an explicit owner.

    Returns (account_type, owner).
    """
    if overrides:
        entry = overrides.get(account_name)
        if entry is not None:
            acct_type, owner = _resolve_override(entry)
            if acct_type:  # guard against malformed override with empty type
                return acct_type, owner

    name_lower = _account_type_text(account_name).lower()

    if "403b" in name_lower or "403(b)" in name_lower:
        acct_type = "403b"
    elif _has_word(name_lower, "roth"):
        acct_type = "roth_ira"
    elif "health savings" in name_lower or _has_word(name_lower, "hsa"):
        acct_type = "hsa"
    elif _has_word(name_lower, "ira"):
        # "Rollover IRA", "Traditional IRA", just "IRA"
        acct_type = "trad_ira"
    else:
        acct_type = "brokerage"

    resolved_hint = _resolve_owner_hint(owner_hint)
    owner = resolved_hint if resolved_hint is not None else "you"

    return acct_type, owner


def _classify_symbol(symbol: str) -> str:
    """Classify a symbol as an asset class.

    Cash holdings from Fidelity have symbol like "Cash HELD IN MONEY MARKET"
    or "Cash FDIC-INSURED DEPOSIT SWEEP".
    """
    if symbol.lower().startswith("cash"):
        return "cash"
    return ASSET_CLASS.get(symbol, "equity")


def _parse_quantity(raw: Any) -> float:
    """Parse quantity which may be a string with commas or a number."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    # String like "2,182.861"
    try:
        return float(str(raw).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
