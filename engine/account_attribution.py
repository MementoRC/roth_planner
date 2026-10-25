"""Per-account owner-attribution overrides, keyed by (broker, account_number).

Holds account numbers. Saved in the document store (namespace
``account_attribution``), whose file is mode 0600. That is filesystem hardening
only, NOT encryption: the database is plaintext, readable by anyone with local
access to this machine/account.

Narrower and account-scoped compared to engine/instance_identity.py's
per-instance default: resolve_account_owner() falls back to instance_owner
whenever no per-account override exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.pdf_owner import OWNER_ROLES, resolve_owner
from engine.store import CorruptStoreError, get_store
from engine.store.namespaces import ACCOUNT_ATTRIBUTION

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
    the saved overrides cannot be read.

    Mirrors ``CorruptCommittedCacheError`` (engine/data_sources/committed.py,
    audit-0809 #11 / PR #442) and ``CandidateStore.save``'s
    ``CorruptCandidateStoreError`` (engine/data_sources/candidate_store.py,
    audit-0823 / PR #447): ``load_account_overrides`` tolerates an unreadable
    row by degrading to ``{}`` (callers depend on that resilience -- a
    broken cache must not crash the app), but if a write then merged onto
    that degraded empty dict and saved it, every saved override
    would be permanently destroyed and replaced with just the one entry
    being written. Once this plan's account-attribution table retires the
    interactive owner-confirm selectboxes, ``resolve_account_owner`` becomes
    the sole non-interactive authority -- a lost override would silently
    reattribute an account with no human step left to catch it.
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"account attribution store ({path}) is corrupt: {cause!r}")


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
    rather than raising, so an unreadable row can't crash the app
    (mirrors engine/pdf_owner.py's load_owner_map tolerant shape). This is
    deliberately NOT mirrored on the write path -- see
    CorruptAccountAttributionError's docstring for why a write must raise
    instead of silently compounding a corrupt read into permanent loss.

    audit-0823: the value vocabulary is closed on the way IN as well as the
    way out. ``save_account_override`` validates its ``owner`` argument, but
    an imported legacy file, a truncated legacy
    entry, or a future role rename can still put an unknown value there.
    Entries whose owner is not in ``OWNER_ROLES`` are dropped exactly as
    undecodable keys already are, because consumers treat the loaded
    vocabulary as closed: ``views/setup/command_center.py`` indexes the
    resolved owner into a fixed three-item selectbox list (an unknown role
    raised ``ValueError`` and took the whole Setup page down, including the
    UI needed to repair it), and ``views/ytd_income/_partials/_sync_scan.py``
    writes it as a ledger bucket key that ``engine/pdf_ledger.py`` then
    persists verbatim. Dropping the entry degrades that account to the
    instance owner -- the same outcome as if no override had been recorded.
    """
    try:
        raw = _read_document()
    except CorruptAccountAttributionError:
        return {}
    if not isinstance(raw, dict):
        return {}
    overrides_raw = raw.get("overrides")
    if not isinstance(overrides_raw, dict):
        return {}
    result: dict[tuple[str, str], str] = {}
    for key, owner in overrides_raw.items():
        decoded = _decode_key(str(key))
        if decoded is not None and str(owner) in OWNER_ROLES:
            result[decoded] = str(owner)
    return result


def _read_document() -> Any:
    """The saved document (``None`` if absent); CorruptAccountAttributionError if unreadable.

    Both writers call this before merging onto what the tolerant
    ``load_account_overrides()`` returns, so an unreadable row stops the write
    instead of being replaced by a fresh dict holding only the one entry being
    saved or deleted (see CorruptAccountAttributionError).
    """
    try:
        return get_store().get(ACCOUNT_ATTRIBUTION)
    except CorruptStoreError as exc:
        raise CorruptAccountAttributionError(ACCOUNT_ATTRIBUTION, exc) from exc


def _write(overrides: dict[tuple[str, str], str]) -> None:
    encoded = {
        _encode_key(broker, account_number): owner
        for (broker, account_number), owner in overrides.items()
    }
    get_store().put(ACCOUNT_ATTRIBUTION, {"version": 1, "overrides": encoded})


def save_account_override(broker: str, account_number: str, owner: str) -> None:
    """Save an override for ``(broker, account_number)``.

    Raises CorruptAccountAttributionError (without writing) if the saved
    overrides cannot be read. The check and the write are one transaction.
    """
    if owner not in OWNER_ROLES:
        raise ValueError(f"Invalid owner role {owner!r}, must be one of {sorted(OWNER_ROLES)}")
    with get_store().transaction():
        _read_document()
        overrides = load_account_overrides()
        overrides[(broker, account_number)] = owner
        _write(overrides)


def delete_account_override(broker: str, account_number: str) -> None:
    """Remove any override for ``(broker, account_number)``.

    Raises CorruptAccountAttributionError (without writing) if the saved
    overrides cannot be read. The check and the write are one transaction.
    """
    with get_store().transaction():
        _read_document()
        overrides = load_account_overrides()
        overrides.pop((broker, account_number), None)
        _write(overrides)


def resolve_account_owner(
    broker: str,
    account_number: str,
    account_overrides: dict[tuple[str, str], str],
    instance_owner: str,
    *,
    holder_key: str | None = None,
    owner_map: dict[str, str] | None = None,
) -> str:
    """TOTAL: an override wins, then the learned holder-name owner_map entry
    (only when *holder_key* and *owner_map* are given), otherwise instance_owner.

    Never returns None and never prompts -- this is the single replacement
    for the ad-hoc st.selectbox owner-confirm prompts this plan retires (see
    views/ytd_income/_partials/_sync_scan.py, Task 6).

    The owner_map fallback exists for documents with no account number
    (Koinly): without it two people's reports both land on instance_owner
    and the second overwrites the first (audit-1018 F-07).
    """
    override = account_overrides.get((broker, account_number))
    if override:
        return override
    if holder_key is not None and owner_map:
        learned = resolve_owner(holder_key, owner_map)
        if learned:
            return learned
    return instance_owner
