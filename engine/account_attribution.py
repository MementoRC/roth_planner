"""Per-account owner-attribution overrides, keyed by (broker, account_number).

Holds account numbers, so persisted via engine.secure_io's PII helpers (0o600
+ O_NOFOLLOW). NOTE: this is filesystem hardening only, NOT encryption -- the
file itself is plaintext JSON, readable by anyone with local access to this
machine/account, same as engine/pdf_owner.py's .pdf_owner_map.json.

Narrower and account-scoped compared to engine/instance_identity.py's
per-instance default: resolve_account_owner() falls back to instance_owner
whenever no per-account override exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.pdf_owner import OWNER_ROLES
from engine.secure_io import read_pii_json, write_pii_json

_ACCOUNT_ATTRIBUTION_PATH = Path(__file__).resolve().parent.parent / ".account_attribution.json"

_KEY_DELIMITER = "|"

__all__ = [
    "delete_account_override",
    "load_account_overrides",
    "resolve_account_owner",
    "save_account_override",
]


def _encode_key(broker: str, account_number: str) -> str:
    if _KEY_DELIMITER in broker:
        raise ValueError(
            f"broker name {broker!r} may not contain {_KEY_DELIMITER!r} (used as the key delimiter)"
        )
    return f"{broker}{_KEY_DELIMITER}{account_number}"


def _decode_key(raw: str) -> tuple[str, str] | None:
    broker, sep, account_number = raw.partition(_KEY_DELIMITER)
    if not sep:
        return None
    return broker, account_number


def load_account_overrides() -> dict[tuple[str, str], str]:
    """Return {(broker, account_number): owner}.

    Tolerant: any read/parse failure or wrong shape degrades to {} rather
    than raising -- this file only ever holds convenience overrides layered
    on top of instance_owner, never the sole copy of anything (mirrors
    engine/pdf_owner.py's load_owner_map tolerant shape).
    """
    if not _ACCOUNT_ATTRIBUTION_PATH.exists():
        return {}
    try:
        raw = read_pii_json(_ACCOUNT_ATTRIBUTION_PATH)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    overrides_raw = raw.get("overrides")
    if not isinstance(overrides_raw, dict):
        return {}
    result: dict[tuple[str, str], str] = {}
    for key, owner in overrides_raw.items():
        decoded = _decode_key(str(key))
        if decoded is not None:
            result[decoded] = str(owner)
    return result


def _write(overrides: dict[tuple[str, str], str]) -> None:
    encoded = {
        _encode_key(broker, account_number): owner
        for (broker, account_number), owner in overrides.items()
    }
    write_pii_json(_ACCOUNT_ATTRIBUTION_PATH, {"version": 1, "overrides": encoded})


def save_account_override(broker: str, account_number: str, owner: str) -> None:
    if owner not in OWNER_ROLES:
        raise ValueError(f"Invalid owner role {owner!r}, must be one of {sorted(OWNER_ROLES)}")
    overrides = load_account_overrides()
    overrides[(broker, account_number)] = owner
    _write(overrides)


def delete_account_override(broker: str, account_number: str) -> None:
    overrides = load_account_overrides()
    overrides.pop((broker, account_number), None)
    _write(overrides)


def resolve_account_owner(
    broker: str,
    account_number: str,
    account_overrides: dict[tuple[str, str], str],
    instance_owner: str,
) -> str:
    """TOTAL: an override wins, otherwise fall back to instance_owner.

    Never returns None and never prompts -- this is the single replacement
    for the ad-hoc st.selectbox owner-confirm prompts this plan retires (see
    views/ytd_income/_partials/_sync_scan.py, Task 6).
    """
    return account_overrides.get((broker, account_number)) or instance_owner
