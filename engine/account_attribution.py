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
import os
from pathlib import Path

from engine.pdf_owner import OWNER_ROLES
from engine.secure_io import read_pii_json, write_pii_json

_ACCOUNT_ATTRIBUTION_PATH = Path(__file__).resolve().parent.parent / ".account_attribution.json"

_KEY_DELIMITER = "|"

__all__ = [
    "CorruptAccountAttributionError",
    "delete_account_override",
    "load_account_overrides",
    "resolve_account_owner",
    "save_account_override",
]


class CorruptAccountAttributionError(Exception):
    """Raised by ``save_account_override``/``delete_account_override`` when
    ``_ACCOUNT_ATTRIBUTION_PATH`` exists but its content is not valid JSON
    (truncated/malformed write, e.g. process killed mid-write).

    Mirrors ``CorruptCommittedCacheError`` (engine/data_sources/committed.py,
    audit-0809 #11 / PR #442) and ``CandidateStore.save``'s
    ``CorruptCandidateStoreError`` (engine/data_sources/candidate_store.py,
    audit-0823 / PR #447): ``load_account_overrides`` tolerates a corrupt
    file by degrading to ``{}`` (callers depend on that resilience -- a
    broken cache must not crash the app), but if a write then merged onto
    that degraded empty dict and saved it, every prior override on disk
    would be permanently destroyed and replaced with just the one entry
    being written. Once this plan's account-attribution table retires the
    interactive owner-confirm selectboxes, ``resolve_account_owner`` becomes
    the sole non-interactive authority -- a lost override would silently
    reattribute an account with no human step left to catch it.
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"account attribution store at {path!r} is corrupt: {cause!r}")


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

    Read-path only: any read/parse failure or wrong shape degrades to {}
    rather than raising, so a broken cache file can't crash the app
    (mirrors engine/pdf_owner.py's load_owner_map tolerant shape). This is
    deliberately NOT mirrored on the write path -- see
    CorruptAccountAttributionError's docstring for why a write must raise
    instead of silently compounding a corrupt read into permanent loss.
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


def _refuse_if_corrupt() -> None:
    """Raise CorruptAccountAttributionError if the store exists but its
    current on-disk content fails to parse.

    Called by both write paths before they merge onto whatever
    ``load_account_overrides()`` (tolerant) returns, so a corrupt file
    stops the write instead of being silently replaced by a fresh dict
    holding only the one entry being saved/deleted (see
    CorruptAccountAttributionError's docstring). A missing file is not
    corrupt -- first run, nothing to protect.
    """
    if not _ACCOUNT_ATTRIBUTION_PATH.exists():
        return
    try:
        read_pii_json(_ACCOUNT_ATTRIBUTION_PATH)
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise CorruptAccountAttributionError(_ACCOUNT_ATTRIBUTION_PATH, exc) from exc


def _write(overrides: dict[tuple[str, str], str]) -> None:
    """Write *overrides* atomically via a tmp file + ``os.replace``.

    Callers must call ``_refuse_if_corrupt()`` first -- this function does
    not pre-check, it only writes.
    """
    encoded = {
        _encode_key(broker, account_number): owner
        for (broker, account_number), owner in overrides.items()
    }
    tmp_path = _ACCOUNT_ATTRIBUTION_PATH.with_name(
        f"{_ACCOUNT_ATTRIBUTION_PATH.name}.tmp-{os.getpid()}"
    )
    write_pii_json(tmp_path, {"version": 1, "overrides": encoded})
    os.replace(tmp_path, _ACCOUNT_ATTRIBUTION_PATH)


def save_account_override(broker: str, account_number: str, owner: str) -> None:
    """Persist an override for ``(broker, account_number)``, atomically.

    Raises CorruptAccountAttributionError (without writing) if the file
    already exists but fails to parse -- see that class's docstring for why
    this refuses rather than clobbers.
    """
    if owner not in OWNER_ROLES:
        raise ValueError(f"Invalid owner role {owner!r}, must be one of {sorted(OWNER_ROLES)}")
    _refuse_if_corrupt()
    overrides = load_account_overrides()
    overrides[(broker, account_number)] = owner
    _write(overrides)


def delete_account_override(broker: str, account_number: str) -> None:
    """Remove any override for ``(broker, account_number)``, atomically.

    Raises CorruptAccountAttributionError (without writing) if the file
    already exists but fails to parse -- see that class's docstring for why
    this refuses rather than clobbers.
    """
    _refuse_if_corrupt()
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
