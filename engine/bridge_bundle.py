"""Pure build/parse/apply for the consolidated .enc data bridge. No Streamlit imports."""
from dataclasses import asdict, is_dataclass

from engine.pdf_ledger import extract_owner, replace_owner
from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict

# v2 -> v3 (audit-0823: "YTD in the data-bridge bundle"): added the "ytd"
# section. Purely ADDITIVE and compatible both ways -- a v2 bundle simply
# lacks "ytd", so read_bundle_ytd returns None (graceful, not an error); an
# older reader that doesn't know about "ytd" just ignores the unknown
# section. v3 bundles therefore remain fully readable by older code, minus
# the YTD data those older readers never expected anyway.
BUNDLE_FORMAT_VERSION = 3


def _account_to_dict(acct) -> dict:
    if is_dataclass(acct) and not isinstance(acct, type):
        return asdict(acct)
    return dict(acct)


def build_bundle(setup_scalars: dict, snapshot, ledger, *, owner: str = "you", ytd=None) -> dict:
    """Assemble the versioned, JSON-able bundle for one owner (default the exporter, 'you').

    ``ytd`` is keyword-only and optional (default ``None``): YTD is a single
    household-wide snapshot, not per-owner like portfolio/ledger, so there is
    no "owner slice" to take -- the exporter's whole YTDSnapshot goes in, or
    nothing does. sections["ytd"] is None when the caller has no snapshot to
    share (e.g. no data-bridge key configured for encryption at that call
    site), which read_bundle_ytd treats the same as an absent v2 section.
    """
    accounts = []
    if snapshot is not None:
        for acct in getattr(snapshot, "accounts", []) or []:
            acct_owner = getattr(acct, "owner", None) if not isinstance(acct, dict) else acct.get("owner")
            if acct_owner == owner:
                accounts.append(_account_to_dict(acct))
    return {
        "format_version": BUNDLE_FORMAT_VERSION,
        "sections": {
            "setup_scalars": dict(setup_scalars or {}),
            "portfolio": {"accounts": accounts},
            "ledger": extract_owner(ledger or {}, owner),
            "ytd": ytd_to_dict(ytd) if ytd is not None else None,
        },
    }


def read_format_version(raw) -> int | None:
    """Return the bundle format version, or None for legacy/foreign payloads."""
    if isinstance(raw, dict):
        v = raw.get("format_version")
        if isinstance(v, int):
            return v
    return None


def read_bundle_ytd(bundle):
    """Return the bundle's YTDSnapshot, or None if absent/malformed.

    A v2 bundle (pre audit-0823) has no "ytd" key at all; a v3 bundle
    exported with ``ytd=None`` has sections["ytd"] explicitly set to None.
    Both cases -- and any malformed section (wrong type, e.g. a bare string
    or an empty dict that can't reconstruct a YTDSnapshot) -- return None
    rather than raising, mirroring read_format_version's tolerance for
    legacy/foreign payloads. The caller (views/setup/data_bridge.py) treats
    None as "nothing to seed" and falls through to its own fallback.
    """
    if not isinstance(bundle, dict):
        return None
    section = bundle.get("sections", {}).get("ytd")
    if not isinstance(section, dict) or not section:
        return None
    try:
        return ytd_from_dict(section)
    except (TypeError, ValueError, KeyError):
        return None


def apply_bundle(target_owner, bundle, *, existing_snapshot, existing_ledger):
    """Full-replace the target owner's slot. Returns (new_snapshot, new_ledger).
    Grants on the existing snapshot are preserved untouched (grants are local)."""
    sections = bundle.get("sections", {})

    incoming_ledger_slice = sections.get("ledger", {"koinly": {}, "brokerage": {}})
    new_ledger = replace_owner(existing_ledger or {}, target_owner, incoming_ledger_slice)

    kept = [a for a in getattr(existing_snapshot, "accounts", [])
            if getattr(a, "owner", None) != target_owner]
    for acct in sections.get("portfolio", {}).get("accounts", []):
        try:
            acct.owner = target_owner
        except AttributeError:
            acct["owner"] = target_owner
        kept.append(acct)
    existing_snapshot.accounts = kept
    return existing_snapshot, new_ledger
