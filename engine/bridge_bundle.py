"""Pure build/parse/apply for the consolidated .enc data bridge. No Streamlit imports."""
from dataclasses import asdict, is_dataclass

from engine.pdf_ledger import extract_owner, replace_owner

BUNDLE_FORMAT_VERSION = 2


def _account_to_dict(acct) -> dict:
    if is_dataclass(acct) and not isinstance(acct, type):
        return asdict(acct)
    return dict(acct)


def build_bundle(setup_scalars: dict, snapshot, ledger, *, owner: str = "you") -> dict:
    """Assemble the versioned, JSON-able bundle for one owner (default the exporter, 'you')."""
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
        },
    }


def read_format_version(raw) -> int | None:
    """Return the bundle format version, or None for legacy/foreign payloads."""
    if isinstance(raw, dict):
        v = raw.get("format_version")
        if isinstance(v, int):
            return v
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
