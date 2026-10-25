"""Durable machine-local record of which person this planner instance belongs to.

No streamlit. Saved in the document store, namespace ``instance_owner``, as
``{"version": 1, "instance_owner": owner}``.

An "instance" is a single deployment/session of this planner (a dev laptop
install, one browser's session on the public site). This value is defaulted
to "you" on first use by Setup ▸ Command Center (views/setup/command_center.py)
and is user-correctable there at any time; it is still deliberately narrower
than engine.pdf_owner.OwnerRole:
an instance can be "you" or "spouse" but never "household" -- an instance
belongs to a single person, even though a specific ACCOUNT it later observes
may be jointly titled (see engine/account_attribution.py for that distinct,
per-account concept).
"""

from __future__ import annotations

from pathlib import Path

from engine.pdf_owner import OwnerRole
from engine.store import CorruptStoreError, get_store
from engine.store.namespaces import INSTANCE_OWNER

_VALID_INSTANCE_OWNERS = frozenset({OwnerRole.YOU.value, OwnerRole.SPOUSE.value})

__all__ = [
    "CorruptInstanceOwnerError",
    "load_instance_owner",
    "save_instance_owner",
]


class CorruptInstanceOwnerError(Exception):
    """Raised when the saved instance owner is not a valid
    instance_owner payload (an undecodable store row, a missing key, or an
    invalid value).

    Deliberately NOT the same outcome as no saved owner: no saved owner means
    "this instance has never been assigned an owner yet" -- safe for a
    caller to treat as a first-run prompt case. A saved owner that fails
    to parse means real data is sitting in the store in an unreadable state, and
    silently treating it as "unset" would let save_instance_owner clobber the
    only copy. Mirrors
    engine.data_sources.committed.CorruptCommittedCacheError's shape exactly.
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"saved instance owner ({path}) is corrupt: {cause!r}")


def load_instance_owner() -> str | None:
    """Return the saved instance owner ("you"/"spouse"), or None if unset.

    None means "this instance has no identity yet": callers must treat that as
    a first-run case, never silently default it. Raises
    CorruptInstanceOwnerError if the saved owner is unreadable or invalid, so
    callers never re-prompt over broken data.
    """
    return _read_owner()


def _read_owner() -> str | None:
    """The saved owner (None if absent); CorruptInstanceOwnerError if unreadable or wrong-shape."""
    try:
        payload = get_store().get(INSTANCE_OWNER)
    except CorruptStoreError as exc:
        raise CorruptInstanceOwnerError(INSTANCE_OWNER, exc) from exc
    if payload is None:
        return None
    try:
        owner = payload["instance_owner"]
        if owner not in _VALID_INSTANCE_OWNERS:
            raise ValueError(f"invalid instance_owner value {owner!r}")
    except (KeyError, ValueError, TypeError) as exc:
        raise CorruptInstanceOwnerError(INSTANCE_OWNER, exc) from exc
    return str(owner)


def save_instance_owner(owner: str) -> None:
    """Save *owner* ("you" or "spouse" only).

    Rejects "household" and any other value: an instance belongs to one
    person (see module docstring). Refuses, with CorruptInstanceOwnerError,
    to replace a saved owner that load_instance_owner() could not read
    (undecodable, wrong shape, or an invalid value; mirrors save_committed's
    audit-0809 #11 guard). The check and the write are one transaction.
    """
    if owner not in _VALID_INSTANCE_OWNERS:
        raise ValueError(
            f"Invalid instance owner {owner!r}, must be one of {sorted(_VALID_INSTANCE_OWNERS)}"
        )
    store = get_store()
    with store.transaction():
        _read_owner()
        store.put(INSTANCE_OWNER, {"version": 1, "instance_owner": owner})
