"""Owner role vocabulary and learned name->owner map for PDF import attribution.

Three roles, reusing the portfolio flow's terms (views/setup/portfolio.py's
"you"/"spouse" owner vocabulary) plus a joint category for documents/accounts
that are inherently shared (the 1040, jointly-held brokerage accounts):

- "you"
- "spouse"
- "household" -- joint documents and jointly-held accounts/crypto.

Import-time only -- see docs/superpowers/specs/2026-07-13-spouse-pdf-owner-
attribution-design.md. Pure functions + a small JSON cache, no Streamlit
import (engine/ purity rule).
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path

from engine.secure_io import read_pii_json, write_pii_json


class OwnerRole(StrEnum):
    """The three owner roles a PDF-derived contribution can be attributed to."""

    YOU = "you"
    SPOUSE = "spouse"
    HOUSEHOLD = "household"


OWNER_ROLES: frozenset[str] = frozenset({r.value for r in OwnerRole})

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_owner_key(raw: str | None) -> str | None:
    """Normalize a raw extracted owner key (name/email) for stable map lookup.

    Lowercases, strips leading/trailing whitespace, and collapses internal
    whitespace runs to a single space. Returns None for None or blank input
    so callers can treat "no key extracted" uniformly.
    """
    if raw is None:
        return None
    collapsed = _WHITESPACE_RE.sub(" ", raw.strip())
    return collapsed.lower() if collapsed else None


def resolve_owner(key: str | None, owner_map: dict[str, str]) -> str | None:
    """Look up *key* in the learned owner_map. Returns None if key is None or unknown.

    *key* is normalized before lookup so callers may pass the raw extracted
    string directly.
    """
    normalized = normalize_owner_key(key)
    if normalized is None:
        return None
    return owner_map.get(normalized)


def learn_owner(key: str | None, role: str, owner_map: dict[str, str]) -> dict[str, str]:
    """Return a NEW owner_map with *key* -> *role* written in (pure, no mutation).

    Raises ValueError if *role* is not one of OWNER_ROLES or *key* normalizes
    to None (nothing to learn).
    """
    if role not in OWNER_ROLES:
        raise ValueError(f"Invalid owner role {role!r}, must be one of {sorted(OWNER_ROLES)}")
    normalized = normalize_owner_key(key)
    if normalized is None:
        raise ValueError("Cannot learn an owner mapping for an empty/None owner key")
    updated = dict(owner_map)
    updated[normalized] = role
    return updated


# ---------------------------------------------------------------------------
# JSON cache -- learned name/email -> owner role map
# ---------------------------------------------------------------------------

_OWNER_MAP_PATH = Path(__file__).resolve().parent.parent / ".pdf_owner_map.json"


def save_owner_map(owner_map: dict[str, str]) -> None:
    write_pii_json(_OWNER_MAP_PATH, owner_map)


def load_owner_map() -> dict[str, str]:
    if not _OWNER_MAP_PATH.exists():
        return {}
    try:
        raw = read_pii_json(_OWNER_MAP_PATH)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}
