"""Durable machine-local record of which person this planner instance belongs to.

Pure module: stdlib only. No streamlit, no other engine imports beyond
engine.pdf_owner's role vocabulary (mirrors engine/data_sources/paths.py's
purity rule). Sits directly in engine/, not engine/data_sources/, so
_REPO_ROOT climbs one fewer parent than paths.py does.

An "instance" is a single deployment/session of this planner (a dev laptop
install, one browser's session on the public site). This value never changes
automatically and is deliberately narrower than engine.pdf_owner.OwnerRole:
an instance can be "you" or "spouse" but never "household" -- an instance
belongs to a single person, even though a specific ACCOUNT it later observes
may be jointly titled (see engine/account_attribution.py for that distinct,
per-account concept).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from engine.pdf_owner import OwnerRole

_REPO_ROOT = Path(__file__).resolve().parents[1]

INSTANCE_OWNER_PATH = _REPO_ROOT / ".instance_owner.json"

_VALID_INSTANCE_OWNERS = frozenset({OwnerRole.YOU.value, OwnerRole.SPOUSE.value})

__all__ = [
    "INSTANCE_OWNER_PATH",
    "CorruptInstanceOwnerError",
    "load_instance_owner",
    "save_instance_owner",
]


class CorruptInstanceOwnerError(Exception):
    """Raised when INSTANCE_OWNER_PATH exists but its content is not a valid
    instance_owner payload (truncated/malformed JSON, missing key, or an
    invalid value).

    Deliberately NOT the same outcome as a missing file: a missing file means
    "this instance has never been assigned an owner yet" -- safe for a
    caller to treat as a first-run prompt case. A file that exists but fails
    to parse means real data is sitting on disk in an unreadable state, and
    silently treating it as "unset" would let save_instance_owner clobber the
    only copy. Mirrors
    engine.data_sources.committed.CorruptCommittedCacheError's shape exactly.
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"instance owner cache at {path!r} is corrupt: {cause!r}")


def load_instance_owner() -> str | None:
    """Return the persisted instance owner ("you"/"spouse"), or None if unset.

    None means "this instance has no identity yet" -- callers must treat
    that as a first-run prompt case, never silently default it. Raises
    CorruptInstanceOwnerError if the file exists but its content is
    unreadable or invalid -- callers must not silently re-prompt over broken
    data (see CorruptInstanceOwnerError's docstring).
    """
    try:
        raw = INSTANCE_OWNER_PATH.read_text()
    except OSError:
        return None
    try:
        payload = json.loads(raw)
        owner = payload["instance_owner"]
        if owner not in _VALID_INSTANCE_OWNERS:
            raise ValueError(f"invalid instance_owner value {owner!r}")
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise CorruptInstanceOwnerError(INSTANCE_OWNER_PATH, exc) from exc
    return str(owner)


def save_instance_owner(owner: str) -> None:
    """Persist *owner* ("you" or "spouse" only) atomically.

    Rejects "household" and any other value -- an instance belongs to one
    person, never a joint identity (see module docstring). Pre-checks that
    an existing file still parses before writing: if it exists but is
    corrupt, this raises rather than silently clobbering the only copy of
    whatever is on disk (mirrors
    engine.data_sources.committed.save_committed's audit-0809 #11 guard).
    Writes via a tmp file + os.replace() so a crash mid-write cannot
    truncate a previously-good file.
    """
    if owner not in _VALID_INSTANCE_OWNERS:
        raise ValueError(
            f"Invalid instance owner {owner!r}, must be one of {sorted(_VALID_INSTANCE_OWNERS)}"
        )
    if INSTANCE_OWNER_PATH.exists():
        try:
            json.loads(INSTANCE_OWNER_PATH.read_text())
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise CorruptInstanceOwnerError(INSTANCE_OWNER_PATH, exc) from exc
    tmp_path = INSTANCE_OWNER_PATH.with_name(f"{INSTANCE_OWNER_PATH.name}.tmp-{os.getpid()}")
    tmp_path.write_text(json.dumps({"version": 1, "instance_owner": owner}))
    os.replace(tmp_path, INSTANCE_OWNER_PATH)
