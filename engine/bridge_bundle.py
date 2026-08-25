"""Pure build/parse/apply for the consolidated .enc data bridge. No Streamlit imports."""
from dataclasses import asdict, is_dataclass

from engine.pdf_ledger import extract_owner, replace_owner
from engine.portfolio_sync.shapes import EquityGrant
from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict

# v2 -> v3 (audit-0823: "YTD in the data-bridge bundle"): added the "ytd"
# section. Purely ADDITIVE and compatible both ways -- a v2 bundle simply
# lacks "ytd", so read_bundle_ytd returns None (graceful, not an error); an
# older reader that doesn't know about "ytd" just ignores the unknown
# section. v3 bundles therefore remain fully readable by older code, minus
# the YTD data those older readers never expected anyway.
#
# v3 -> v4: added the "grants" section. The user's real option/RSU grants
# (engine.portfolio_sync.shapes.EquityGrant) were previously DROPPED on
# export -- PortfolioSnapshot.equity_grants never made it into any section,
# so an importer's Household.grants silently kept its synthetic demo-grant
# default_factory forever, even though the matching strike prices (bundle
# key "grant_strikes" in setup_scalars) traveled just fine and sat inert.
# Purely ADDITIVE and compatible both ways, exactly like v2 -> v3: a v3
# bundle simply lacks "grants", so read_bundle_grants returns None (the
# caller then preserves whatever grants already exist locally); an older
# reader that doesn't know about "grants" just ignores the unknown section.
BUNDLE_FORMAT_VERSION = 4


def _account_to_dict(acct) -> dict:
    if is_dataclass(acct) and not isinstance(acct, type):
        return asdict(acct)
    return dict(acct)


def _equity_grant_to_dict(g) -> dict:
    """Serialize one EquityGrant (or an already-dict-shaped grant) to a plain dict."""
    if is_dataclass(g) and not isinstance(g, type):
        return asdict(g)
    return dict(g)


def _equity_grant_from_dict(d: dict) -> EquityGrant:
    """Reconstruct an EquityGrant from a bundle dict, tolerating unknown/missing keys.

    Unknown keys (e.g. from a future peer's schema additions) are ignored;
    missing keys (e.g. from an older peer's schema, or a hand-built test
    fixture) fall back to the same defaults EquityGrant's own fields would
    hold on a freshly-constructed empty grant. Present values are coerced to
    their expected type -- EquityGrant is a plain dataclass with no runtime
    type checking of its own, so this is what lets read_bundle_grants detect
    (via the TypeError/ValueError this raises) and skip a genuinely
    corrupt entry instead of silently admitting garbage.
    """
    return EquityGrant(
        grant_id=str(d.get("grant_id", "")),
        grant_type=str(d.get("grant_type", "")),
        grant_date=str(d.get("grant_date", "")),
        shares_granted=int(d.get("shares_granted", 0)),
        outstanding=int(d.get("outstanding", 0)),
        current_value=float(d.get("current_value", 0.0)),
    )


def build_bundle(setup_scalars: dict, snapshot, ledger, *, owner: str = "you", ytd=None, grants=None) -> dict:
    """Assemble the versioned, JSON-able bundle for one owner (default the exporter, 'you').

    ``ytd`` is keyword-only and optional (default ``None``): YTD is a single
    household-wide snapshot, not per-owner like portfolio/ledger, so there is
    no "owner slice" to take -- the exporter's whole YTDSnapshot goes in, or
    nothing does. sections["ytd"] is None when the caller has no snapshot to
    share (e.g. no data-bridge key configured for encryption at that call
    site), which read_bundle_ytd treats the same as an absent v2 section.

    ``grants`` is likewise keyword-only, optional, and top-level -- a sibling
    of "ytd", NOT nested under "portfolio". "portfolio" is owner-sliced
    (accounts are filtered by ``owner`` above and re-stamped on apply), but
    EquityGrant (engine.portfolio_sync.shapes) carries no ``owner`` field at
    all, so there is no owner slice to take for grants either, same reasoning
    as ytd. sections["grants"] is None when the caller passes no grants list
    (e.g. no PortfolioSnapshot loaded at export time), which read_bundle_grants
    treats the same as an absent v3 section.
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
            "grants": [_equity_grant_to_dict(g) for g in grants] if grants is not None else None,
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


def read_bundle_grants(bundle):
    """Return the bundle's list of EquityGrant, or None if absent/malformed.

    A v3 bundle (pre this change) has no "grants" key at all; a v4 bundle
    exported with ``grants=None`` has sections["grants"] explicitly set to
    None. Both cases return None, mirroring read_bundle_ytd's tolerance for
    legacy payloads -- the caller (apply_bundle) treats None as "nothing to
    replace" and leaves the existing snapshot's grants untouched.

    An empty list (sections["grants"] == []) is DISTINCT from None: it means
    the exporter genuinely has zero grants right now (e.g. all options
    exercised/expired), which is real information the caller SHOULD apply
    -- it is not the same as "this exporter predates the grants section" and
    must not be treated as a no-op.

    Individual malformed entries (not a dict, or one that fails to
    reconstruct into an EquityGrant) are skipped rather than failing the
    whole section, so one bad grant in a hand-edited or foreign-schema
    bundle doesn't cost the user every other real grant in the same file.
    """
    if not isinstance(bundle, dict):
        return None
    section = bundle.get("sections", {}).get("grants")
    if not isinstance(section, list):
        return None
    grants = []
    for entry in section:
        if not isinstance(entry, dict):
            continue
        try:
            grants.append(_equity_grant_from_dict(entry))
        except (TypeError, ValueError, KeyError):
            continue
    return grants


def apply_bundle(target_owner, bundle, *, existing_snapshot, existing_ledger):
    """Full-replace the target owner's slot. Returns (new_snapshot, new_ledger).

    Grants on the existing snapshot are REPLACED when the bundle carries a
    "grants" section (v4+), and preserved untouched when it does not (v3 and
    older bundles) -- so local runs against a real .portfolio_cache.json
    keep today's behaviour until the sender re-exports with a current
    version.
    """
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

    incoming_grants = read_bundle_grants(bundle)
    if incoming_grants is not None:
        existing_snapshot.equity_grants = incoming_grants

    return existing_snapshot, new_ledger
