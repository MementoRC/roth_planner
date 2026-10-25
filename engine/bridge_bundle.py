"""Pure build/parse/apply for the consolidated .enc data bridge. No Streamlit imports."""

import copy
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Literal

from config.scalar_registry import SCALAR_REGISTRY, joint_keys
from engine.pdf_ledger import extract_owner, replace_owner
from engine.portfolio_sync.classify import _resolve_override
from engine.portfolio_sync.shapes import AccountSummary, EquityGrant, account_summary_from_dict
from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict
from models.legacy_keys import migrate_legacy_keys
from models.ytd_income import (
    OWNED_YTD_FIELDS,
    IncomeEvent,
    ManualMark,
    manual_mark_from_dict,
    split_manual_key,
)

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


def build_bundle(
    setup_scalars: dict, snapshot, ledger, *, owner: str = "you", ytd=None, grants=None
) -> dict:
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
            acct_owner = (
                getattr(acct, "owner", None) if not isinstance(acct, dict) else acct.get("owner")
            )
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
    older bundles) -- so local runs against a real saved portfolio
    keep today's behaviour until the sender re-exports with a current
    version.
    """
    sections = bundle.get("sections", {})

    incoming_ledger_slice = sections.get("ledger", {"koinly": {}, "brokerage": {}})
    new_ledger = replace_owner(existing_ledger or {}, target_owner, incoming_ledger_slice)

    kept = [
        a
        for a in getattr(existing_snapshot, "accounts", [])
        if getattr(a, "owner", None) != target_owner
    ]
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


# ---------------------------------------------------------------------------
# v5 envelope (bridge v5 / issue #496)
# ---------------------------------------------------------------------------
#
# v4 -> v5 is NOT additive: the payload is restructured so the file itself
# says whose data it carries, instead of the receiver inferring it.
#
#   slots.{you,spouse}  per-person scalars in ABSOLUTE keys (your_* / spouse_*),
#                       grants + grant_strikes, the person's non-ledger YTD,
#                       and a captured_at used for staleness checks.
#   records             portfolio accounts and ledger rows for EVERY owner,
#                       each keeping its own owner (including "household"),
#                       plus account_type_overrides in the nested form.
#   joint               household-level scalars (filing_status et al.).
#
# The v4 builder/reader/apply above stay untouched for the legacy path.
BUNDLE_FORMAT_VERSION_V5 = 5

SLOT_NAMES: tuple[str, ...] = ("you", "spouse")
SLOT_PREFIX: dict[str, str] = {"you": "your_", "spouse": "spouse_"}
RECORD_OWNERS: tuple[str, ...] = ("you", "spouse", "household")

# Keys that are not in SCALAR_KEYS but are household-level (plan R9).
_EXTRA_JOINT_KEYS: frozenset[str] = frozenset({"survivor", "inherited_iras", "prior_year_magi"})

# Per-person scalar config keys, by slot, from the registry.
_SLOT_SCALAR_KEYS: dict[str, frozenset[str]] = {
    slot: frozenset(
        (spec.config_key or spec.session_key)
        for spec in SCALAR_REGISTRY
        if spec.scope == "per_person" and (spec.config_key or spec.session_key).startswith(prefix)
    )
    for slot, prefix in SLOT_PREFIX.items()
}
_JOINT_SCALAR_KEYS: frozenset[str] = joint_keys() | _EXTRA_JOINT_KEYS

# Slot conversion field for each person (IRA conversions are per-person by
# field name and deliberately NOT in YTDSnapshot.by_owner -- plan R1).
_CONVERSION_FIELD: dict[str, str] = {
    "you": "ira_conversions_ytd",
    "spouse": "spouse_ira_conversions_ytd",
}


def _normalise_overrides(overrides) -> dict:
    """account_type_overrides in the nested {id: {type, owner}} form only."""
    if not isinstance(overrides, dict):
        return {}
    out = {}
    for account_id, entry in overrides.items():
        acct_type, owner = _resolve_override(entry)
        out[str(account_id)] = {"type": acct_type, "owner": owner}
    return out


def _slot_ytd(ytd, slot: str) -> dict | None:
    """The v5 slot ``ytd`` payload for *slot*, from an ATTRIBUTED snapshot."""
    if ytd is None:
        return None
    payload: dict = {
        "values": {f: v for f, v in ytd.by_owner.get(slot, {}).items() if f in OWNED_YTD_FIELDS},
        "ira_conversions": float(getattr(ytd, _CONVERSION_FIELD[slot])),
        "income_events": [asdict(e) for e in ytd.income_events if e.owner == slot],
    }
    # Redesign PR 1b: values.federal_withholding_ytd stays the person's TOTAL so
    # builds before 1b read the right number; ubs_federal_withholding lets newer
    # builds split it back into base + UBS component.
    if slot in ytd.ubs_withholding_by_owner:
        ubs = float(ytd.ubs_withholding_by_owner[slot])
        values = payload["values"]
        values["federal_withholding_ytd"] = float(values.get("federal_withholding_ytd", 0.0)) + ubs
        payload["ubs_federal_withholding"] = ubs
    # Redesign PR 1b: provenance of this person's hand-entered values. Optional
    # and emitted only when present: builds before 1b ignore unknown keys here.
    # Household marks stay local (household YTD fields never travel in v5).
    manual = {}
    for key, mark in sorted(ytd.manual_marks.items()):
        owner, field_name = split_manual_key(key)
        if owner == slot:
            manual[field_name] = asdict(mark)
    if manual:
        payload["manual"] = manual
    return payload


def build_bundle_v5(
    *,
    setup_scalars: dict | None,
    snapshot,
    ledger,
    ytd,
    grants,
    exporter: str,
    slot_captured_at: dict | None,
    now: str,
    grants_owner: str | None = None,
) -> dict:
    """Assemble a v5 envelope. Pure: inputs are deep-copied, never mutated.

    ``grants`` and ``grant_strikes`` ride in ``grants_owner``'s slot -- the
    person the grants belong to, as recorded when they were imported
    (``PortfolioSnapshot.equity_grants_owner``). ``None``/"" means the
    exporter's own. Filing an imported copy under the exporter's slot made the
    grant holder re-import their own stale grants as authoritative
    (audit-1008 F-05).

    ``exporter`` is the slot that is the exporter's own ("you" or "spouse");
    that slot is always emitted. The other slot is emitted only when this
    instance holds something for that person (a per-person scalar or
    per-owner YTD values) -- the exporter fills it opportunistically.

    ``now`` (ISO string) becomes ``exported_at`` and the exporter slot's
    ``captured_at``; the other slot's ``captured_at`` comes from the caller's
    persisted ``slot_captured_at`` and is None when unknown.

    An unattributed (legacy) YTD snapshot is attributed wholly to the
    exporter on a copy, so its totals travel in the exporter slot.

    Keys in ``setup_scalars`` that are neither per-person, joint, nor one of
    ``grant_strikes`` / ``account_type_overrides`` are not carried.
    """
    if exporter not in SLOT_NAMES:
        raise ValueError(f"unknown exporter slot {exporter!r}; expected one of {SLOT_NAMES}")
    grants_slot = grants_owner or exporter
    if grants_slot not in SLOT_NAMES:
        raise ValueError(f"unknown grants owner {grants_owner!r}; expected one of {SLOT_NAMES}")
    grant_list = [_equity_grant_to_dict(g) for g in grants or []]
    scalars = copy.deepcopy(dict(setup_scalars or {}))
    grant_strikes = dict(scalars.get("grant_strikes") or {})
    captured = dict(slot_captured_at or {})
    if ytd is not None and not ytd.by_owner:
        ytd = ytd.attribute_unowned(exporter)

    slots: dict[str, dict] = {}
    for slot in SLOT_NAMES:
        slot_scalars = {k: v for k, v in scalars.items() if k in _SLOT_SCALAR_KEYS[slot]}
        has_ytd_values = ytd is not None and bool(ytd.by_owner.get(slot))
        holds_grants = slot == grants_slot and bool(grant_list or grant_strikes)
        if slot != exporter and not slot_scalars and not has_ytd_values and not holds_grants:
            continue
        is_exporter = slot == exporter
        owns_grants = slot == grants_slot
        slots[slot] = {
            "captured_at": now if is_exporter else captured.get(slot),
            "setup_scalars": slot_scalars,
            "grants": copy.deepcopy(grant_list) if owns_grants else [],
            "grant_strikes": dict(grant_strikes) if owns_grants else {},
            "ytd": _slot_ytd(ytd, slot),
        }

    accounts = [
        _account_to_dict(acct)
        for acct in (getattr(snapshot, "accounts", []) or [] if snapshot is not None else [])
    ]
    records = {
        "portfolio": copy.deepcopy(accounts),
        "ledger": copy.deepcopy(ledger) if ledger else {"koinly": {}, "brokerage": {}},
        "account_type_overrides": _normalise_overrides(scalars.get("account_type_overrides")),
    }
    joint = {k: v for k, v in scalars.items() if k in _JOINT_SCALAR_KEYS}

    return {
        "format_version": BUNDLE_FORMAT_VERSION_V5,
        "exported_at": now,
        "exported_by": exporter,
        "slots": slots,
        "records": records,
        "joint": joint,
    }


# ---------------------------------------------------------------------------
# v5: version classification + strict parsing
# ---------------------------------------------------------------------------


class UnsupportedBundleVersionError(ValueError):
    """The payload's format_version is missing, malformed, or not one we read.

    A ValueError so the view's existing pre-parse ``except`` tuple reports it
    as an invalid file rather than a traceback.
    """


class BundleFormatError(ValueError):
    """The payload claims a supported version but its structure is invalid."""


_LEGACY_VERSIONS: frozenset[int] = frozenset({1, 2, 3, BUNDLE_FORMAT_VERSION})


def classify_bundle(raw: Any) -> Literal["v4", "v5"]:
    """Classify a decrypted payload by ``format_version`` -- never guess.

    1-4 are the additive legacy history (see the notes at the top of this
    module) and all route through the v4 path; 5 is the slot envelope.
    Anything else -- missing, a string, a bool, a float, a future version --
    is refused with the offending value named. Note that an unversioned
    legacy ``.user_defaults.json`` payload is refused here too: callers that
    still accept those must branch on ``read_format_version(raw) is None``
    BEFORE classifying.
    """
    if not isinstance(raw, dict):
        raise BundleFormatError("bundle payload is not a JSON object")
    if "format_version" not in raw:
        raise UnsupportedBundleVersionError("bundle has no format_version; refusing to guess")
    version = raw["format_version"]
    if type(version) is int:
        if version in _LEGACY_VERSIONS:
            return "v4"
        if version == BUNDLE_FORMAT_VERSION_V5:
            return "v5"
    raise UnsupportedBundleVersionError(
        f"unsupported bundle format_version {version!r}; this build reads 1-5"
    )


@dataclass(frozen=True)
class SlotYtd:
    """One person's non-ledger YTD contribution, as carried in a v5 slot.

    ``values['federal_withholding_ytd']`` is the BASE once parsed: a bundle's
    ``ubs_federal_withholding`` (redesign PR 1b) is split back out of the
    carried total into ``ubs_federal_withholding``; None when the bundle
    carries no split (the total is then the base).
    """

    values: dict[str, float]
    ira_conversions: float
    income_events: tuple[IncomeEvent, ...]
    manual: dict[str, ManualMark] = field(default_factory=dict)
    ubs_federal_withholding: float | None = None


@dataclass(frozen=True)
class SlotPayload:
    """One person's slot in a v5 bundle, validated."""

    captured_at: str | None
    setup_scalars: dict[str, Any]
    grants: tuple[EquityGrant, ...]
    grant_strikes: dict[str, Any]
    ytd: SlotYtd | None


@dataclass(frozen=True)
class Records:
    """Owner-carrying records of a v5 bundle (owners preserved, never reassigned)."""

    accounts: tuple[AccountSummary, ...]
    ledger: dict[str, dict[str, Any]]
    account_type_overrides: dict[str, dict[str, str]]


@dataclass(frozen=True)
class BundleV5:
    """A parsed, validated v5 bundle. ``warnings`` lists anything dropped."""

    exported_at: str | None
    exported_by: str
    slots: dict[str, SlotPayload]
    records: Records
    joint: dict[str, Any]
    warnings: tuple[str, ...]


def _require_dict(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise BundleFormatError(f"{where} must be an object, got {type(value).__name__}")
    return value


def _parse_slot_ytd(raw: Any, slot: str, warnings: list[str]) -> SlotYtd | None:
    if raw is None:
        return None
    raw = _require_dict(raw, f"slots.{slot}.ytd")
    values: dict[str, float] = {}
    for name, value in _require_dict(raw.get("values", {}), f"slots.{slot}.ytd.values").items():
        if name not in OWNED_YTD_FIELDS or isinstance(value, bool):
            warnings.append(f"slots.{slot}.ytd: dropped non per-person field {name!r}")
            continue
        try:
            values[name] = float(value)
        except (TypeError, ValueError):
            warnings.append(f"slots.{slot}.ytd: dropped non-numeric {name!r}")
    try:
        conversions = float(raw.get("ira_conversions", 0.0))
    except (TypeError, ValueError) as exc:
        raise BundleFormatError(f"slots.{slot}.ytd.ira_conversions is not a number") from exc
    events: list[IncomeEvent] = []
    for entry in raw.get("income_events") or []:
        try:
            event = IncomeEvent(**entry)
        except TypeError:
            warnings.append(f"slots.{slot}.ytd: skipped a malformed income event")
            continue
        if event.owner != slot:
            warnings.append(f"slots.{slot}.ytd: dropped an income event owned by {event.owner!r}")
            continue
        events.append(event)
    ubs: float | None = None
    raw_ubs = raw.get("ubs_federal_withholding")
    if raw_ubs is not None:
        total = values.get("federal_withholding_ytd", 0.0)
        if (
            isinstance(raw_ubs, bool)
            or not isinstance(raw_ubs, (int, float))
            or raw_ubs < 0
            or raw_ubs > total + 0.005
        ):
            warnings.append(f"slots.{slot}.ytd: ignored malformed ubs_federal_withholding")
        else:
            ubs = float(raw_ubs)
            values["federal_withholding_ytd"] = max(0.0, total - ubs)
    manual: dict[str, ManualMark] = {}
    raw_manual = raw.get("manual") or {}
    if not isinstance(raw_manual, dict):
        warnings.append(f"slots.{slot}.ytd: ignored malformed manual-entry provenance")
        raw_manual = {}
    for name, entry in raw_manual.items():
        mark = manual_mark_from_dict(entry)
        if name not in OWNED_YTD_FIELDS or mark is None:
            warnings.append(f"slots.{slot}.ytd: dropped malformed manual-entry provenance {name!r}")
            continue
        manual[name] = mark
    return SlotYtd(
        values=values,
        ira_conversions=conversions,
        income_events=tuple(events),
        manual=manual,
        ubs_federal_withholding=ubs,
    )


def _parse_slot(raw: Any, slot: str, warnings: list[str]) -> SlotPayload:
    raw = _require_dict(raw, f"slots.{slot}")
    scalars: dict[str, Any] = {}
    for key, value in migrate_legacy_keys(
        _require_dict(raw.get("setup_scalars", {}), f"slots.{slot}.setup_scalars")
    ).items():
        if key in _SLOT_SCALAR_KEYS[slot]:
            scalars[key] = value
        else:
            warnings.append(f"slots.{slot}: dropped scalar {key!r} (not a {slot} field)")
    grants: list[EquityGrant] = []
    for entry in raw.get("grants") or []:
        if not isinstance(entry, dict):
            warnings.append(f"slots.{slot}: skipped a malformed grant")
            continue
        try:
            grants.append(_equity_grant_from_dict(entry))
        except (TypeError, ValueError):
            warnings.append(f"slots.{slot}: skipped a malformed grant")
    strikes = raw.get("grant_strikes") or {}
    if not isinstance(strikes, dict):
        warnings.append(f"slots.{slot}: dropped malformed grant_strikes")
        strikes = {}
    captured_at = raw.get("captured_at")
    if captured_at is not None and not isinstance(captured_at, str):
        warnings.append(f"slots.{slot}: ignored malformed captured_at")
        captured_at = None
    return SlotPayload(
        captured_at=captured_at,
        setup_scalars=scalars,
        grants=tuple(grants),
        grant_strikes=dict(strikes),
        ytd=_parse_slot_ytd(raw.get("ytd"), slot, warnings),
    )


def _parse_records(raw: Any, warnings: list[str]) -> Records:
    raw = _require_dict(raw, "records")
    accounts: list[AccountSummary] = []
    for i, entry in enumerate(raw.get("portfolio") or []):
        entry = _require_dict(entry, f"records.portfolio[{i}]")
        owner = entry.get("owner")
        if owner not in RECORD_OWNERS:
            raise BundleFormatError(
                f"records.portfolio[{i}] has owner {owner!r}; expected one of {RECORD_OWNERS}"
            )
        try:
            accounts.append(account_summary_from_dict(entry))
        except TypeError as exc:
            raise BundleFormatError(
                f"records.portfolio[{i}] is not a valid account: {exc}"
            ) from exc
    ledger = _require_dict(raw.get("ledger") or {}, "records.ledger")
    clean_ledger: dict[str, dict[str, Any]] = {"koinly": {}, "brokerage": {}}
    for section in ("koinly", "brokerage"):
        by_owner = _require_dict(ledger.get(section) or {}, f"records.ledger.{section}")
        for owner, payload in by_owner.items():
            if owner not in RECORD_OWNERS:
                raise BundleFormatError(
                    f"records.ledger.{section} has owner {owner!r}; expected one of {RECORD_OWNERS}"
                )
            clean_ledger[section][owner] = payload
    overrides: dict[str, dict[str, str]] = {}
    raw_overrides = _require_dict(
        raw.get("account_type_overrides") or {}, "records.account_type_overrides"
    )
    for account_id, entry in _normalise_overrides(raw_overrides).items():
        if entry["owner"] not in SLOT_NAMES:
            warnings.append(
                f"records.account_type_overrides: dropped {account_id!r} "
                f"with owner {entry['owner']!r}"
            )
            continue
        overrides[account_id] = entry
    return Records(accounts=tuple(accounts), ledger=clean_ledger, account_type_overrides=overrides)


def parse_v5(raw: Any) -> BundleV5:
    """Parse and validate a v5 payload. Pure: *raw* is deep-copied, never mutated.

    Structural problems that would make the import ambiguous -- an unknown
    exporter or slot name, a missing exporter slot, a record whose owner is
    not you/spouse/household -- raise :class:`BundleFormatError`. Individual
    entries that are merely out of place (a ``your_*`` scalar in the spouse
    slot, a malformed grant, another owner's income event) are dropped and
    named in ``warnings`` so the preview can show them.
    """
    if classify_bundle(raw) != "v5":
        raise BundleFormatError(f"not a v5 bundle (format_version {raw.get('format_version')!r})")
    raw = copy.deepcopy(raw)
    warnings: list[str] = []
    exporter = raw.get("exported_by")
    if exporter not in SLOT_NAMES:
        raise BundleFormatError(f"exported_by {exporter!r} is not one of {SLOT_NAMES}")
    raw_slots = _require_dict(raw.get("slots"), "slots")
    for name in raw_slots:
        if name not in SLOT_NAMES:
            raise BundleFormatError(f"unknown slot {name!r}; expected one of {SLOT_NAMES}")
    if exporter not in raw_slots:
        raise BundleFormatError(f"exporter slot {exporter!r} is missing")
    slots = {
        name: _parse_slot(raw_slots[name], name, warnings)
        for name in SLOT_NAMES
        if name in raw_slots
    }
    records = _parse_records(raw.get("records") or {}, warnings)
    joint: dict[str, Any] = {}
    # Bundles exported before the stock_* rename carry the old key names.
    for key, value in migrate_legacy_keys(_require_dict(raw.get("joint") or {}, "joint")).items():
        if key in _JOINT_SCALAR_KEYS:
            joint[key] = value
        else:
            warnings.append(f"joint: dropped unknown key {key!r}")
    exported_at = raw.get("exported_at")
    return BundleV5(
        exported_at=exported_at if isinstance(exported_at, str) else None,
        exported_by=exporter,
        slots=slots,
        records=records,
        joint=joint,
        warnings=tuple(warnings),
    )
