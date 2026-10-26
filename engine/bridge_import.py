"""Bridge v5 import: plan, apply and commit a decrypted bundle. No Streamlit, no nacl.

Plan: docs/superpowers/plans/2026-09-30-bridge-v5-envelope.md (tasks A6-A8).

The import runs in stages, and nothing is written until the last one:

1. :func:`plan_import` -- a pure function describing what an import WOULD
   write, per slot, so the view can preview it and let the user deselect a
   slot (or redirect a legacy v4 file into the ``you`` slot).
2. :func:`apply_import` -- a pure function building the NEW snapshot, ledger,
   YTD and session-state updates on deep copies, then validating them.
3. :func:`commit_import` -- writes the three caches in order; the caller's
   store transaction rolls the earlier writes back when a later one fails.

Ownership rules this module encodes (spec 2026-09-20 + section 5a):

* Records (accounts, ledger rows) keep the owner the bundle declares; nothing
  is re-stamped, which is what lets ``household`` survive a round trip.
* The exporter's own slot is authoritative about that person. The other slot
  is only an opportunistic copy, so its records and YTD are applied only when
  the bundle actually carries some -- an instance that never held the other
  person's accounts must not be read as "they have none".
* Per-person YTD REPLACES that person's contribution and never adds to it, so
  re-importing the same bundle is idempotent (issue #496).
* A legacy v4 bundle keeps its historical path -- ``apply_bundle`` plus the
  ``upload_merge`` cross-map -- and never adopts the sender's YTD (PR #497).
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Literal

from config.scalar_registry import SCALAR_REGISTRY
from engine.bridge_bundle import (
    BundleV5,
    SlotPayload,
    apply_bundle,
    classify_bundle,
    parse_v5,
)
from engine.pdf_ledger import prune_duplicates_elsewhere, replace_owner
from engine.portfolio_sync.classify import _resolve_override
from engine.portfolio_sync.portfolio import EmptySnapshotWriteRefusedError
from engine.portfolio_sync.shapes import (
    AccountSummary,
    PortfolioSnapshot,
    account_summary_from_dict,
)
from engine.upload_merge import (
    build_user_defaults_session_updates,
    extract_bundle_magi,
    receiver_joint_value_is_set,
)
from models.ytd_income import OWNED_YTD_FIELDS, YTDSnapshot, iso_day, manual_key

SlotName = Literal["you", "spouse"]
_SLOTS: tuple[str, ...] = ("you", "spouse")

# Per-person IRA conversions live in a named field per owner (plan R1).
_CONVERSION_FIELD: dict[str, str] = {
    "you": "ira_conversions_ytd",
    "spouse": "spouse_ira_conversions_ytd",
}

# Bundle (config) key -> session_state key. Only stock_price_now differs.
_SESSION_KEY: dict[str, str] = {
    (spec.config_key or spec.session_key): spec.session_key for spec in SCALAR_REGISTRY
}


def _session_key(config_key: str) -> str:
    return _SESSION_KEY.get(config_key, config_key)


@dataclass
class LocalState:
    """Everything the receiver holds before an import. Never mutated by this module.

    ``scalars`` are session-state values (session keys); ``persisted_scalars``
    is the raw ``.user_defaults.json`` used by the joint receiver-wins rule;
    ``slot_captured_at`` is the per-slot capture time persisted at the last
    import or export (plan R12), empty when unknown.
    """

    scalars: Mapping[str, Any] = field(default_factory=dict)
    persisted_scalars: Mapping[str, Any] = field(default_factory=dict)
    snapshot: PortfolioSnapshot | None = None
    ledger: Mapping[str, Any] = field(default_factory=dict)
    ytd: YTDSnapshot | None = None
    account_type_overrides: Mapping[str, Any] = field(default_factory=dict)
    slot_captured_at: Mapping[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class SlotPlan:
    """What importing one slot would do. ``*_writes`` map a field to (old, new)."""

    slot: str
    incoming_captured_at: str | None
    local_captured_at: str | None
    stale: bool | None  # None = unknown (a timestamp is missing or unreadable)
    scalar_writes: dict[str, tuple[Any, Any]]
    accounts_incoming: int
    accounts_replaced: int
    accounts_removed: int
    ledger_rows: int
    ytd_writes: dict[str, tuple[float, float]]
    grants_replaced: bool


@dataclass(frozen=True)
class HouseholdPlan:
    """Household-owned records: upserted by account_name, never deleted (plan R8)."""

    accounts_upserted: int
    accounts_added: int


@dataclass(frozen=True)
class ImportPlan:
    """A previewable description of an import. ``declared_by`` is None for v4."""

    version: Literal["v4", "v5"]
    declared_by: str | None
    slots: dict[str, SlotPlan]
    household: HouseholdPlan
    joint_writes: dict[str, tuple[Any, Any]]
    joint_conflicts: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


# ---------------------------------------------------------------------------
# helpers shared by plan and apply
# ---------------------------------------------------------------------------


def _as_bundle(bundle: BundleV5 | Mapping[str, Any]) -> BundleV5 | dict[str, Any]:
    """A parsed v5 bundle, or the raw legacy (v4) payload."""
    if isinstance(bundle, BundleV5):
        return bundle
    raw = dict(bundle)
    if classify_bundle(raw) == "v5":
        return parse_v5(raw)
    return raw


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _is_stale(incoming: str | None, local: str | None) -> bool | None:
    inc, loc = _parse_time(incoming), _parse_time(local)
    if inc is None or loc is None:
        return None
    try:
        return inc < loc
    except TypeError:  # naive vs aware -- not comparable, so unknown
        return None


def _newer_captured_at(local: str | None, incoming: str) -> str:
    """The capture time to keep after importing *incoming* over *local*.

    A deliberately imported older copy must never move the local time
    backwards, or the next staleness check would call a stale copy fresh.
    An unreadable incoming time never replaces a local one, but a readable
    incoming time does replace an unreadable local one -- otherwise a corrupt
    value would freeze the slot's capture time forever.
    """
    if not local:
        return incoming
    if _parse_time(incoming) is None:
        return local
    if _parse_time(local) is None:
        return incoming
    stale = _is_stale(incoming, local)
    if stale is None:
        return local
    return local if stale else incoming


def _owner(acct: Any) -> Any:
    return acct.get("owner") if isinstance(acct, dict) else getattr(acct, "owner", None)


def _name(acct: Any) -> Any:
    if isinstance(acct, dict):
        return acct.get("account_name")
    return getattr(acct, "account_name", None)


def _local_accounts(local: LocalState) -> list[Any]:
    return list(getattr(local.snapshot, "accounts", []) or [])


def _slot_has_records(bundle: BundleV5, slot: str) -> bool:
    ledger = bundle.records.ledger
    return any(a.owner == slot for a in bundle.records.accounts) or any(
        ledger.get(section, {}).get(slot) for section in ("koinly", "brokerage")
    )


def _applies_records(bundle: BundleV5, slot: str) -> bool:
    """Exporter slot: always authoritative. Other slot: only when it carries records."""
    return slot == bundle.exported_by or _slot_has_records(bundle, slot)


def _applies_ytd(bundle: BundleV5, slot: str) -> bool:
    """Same rule for YTD: a zero-filled other-slot ytd is "nothing known", not zeros."""
    payload = bundle.slots[slot].ytd
    if payload is None:
        return False
    if slot == bundle.exported_by:
        return True
    return bool(payload.values or payload.income_events or payload.ira_conversions)


def _ledger_rows(bundle: BundleV5, slot: str) -> int:
    ledger = bundle.records.ledger
    koinly = 1 if ledger.get("koinly", {}).get(slot) else 0
    return koinly + len(ledger.get("brokerage", {}).get(slot) or {})


def _joint_updates(
    joint: Mapping[str, Any], local: LocalState
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Receiver-wins-when-set rule (engine/upload_merge.py, plan R10; audit-1008 F-04).

    Returns (updates keyed by session key, conflicts). ``prior_year_magi`` is
    excluded: it only ever becomes Source.BUNDLE candidates (audit defect #2).
    """
    updates: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    for key in sorted(joint):
        if key == "prior_year_magi":
            continue
        theirs = joint[key]
        if not receiver_joint_value_is_set(key, local.persisted_scalars):
            updates[_session_key(key)] = theirs
        elif local.persisted_scalars[key] != theirs:
            conflicts.append({"field": key, "mine": local.persisted_scalars[key], "theirs": theirs})
    return updates, tuple(conflicts)


def _writes(updates: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, tuple[Any, Any]]:
    """(old, new) for every update that actually changes a value."""
    return {k: (current.get(k), v) for k, v in updates.items() if current.get(k) != v}


def _pre_1b_withholding_base(base: YTDSnapshot, slot: str, payload: Any) -> float:
    """Base withholding for *slot* when a bundle carries only its total.

    Redesign PR 1b: the total already includes the receiver's UBS component
    for *slot*, so the base is the remainder, floored at 0.
    """
    total = float(payload.values.get("federal_withholding_ytd", 0.0))
    return max(0.0, total - base.ubs_withholding_by_owner[slot])


def _pre_1b_withholding_warning(
    local_ytd: YTDSnapshot | None, slot: str, payload: Any
) -> str | None:
    """A warning when a total-only bundle is below the receiver's UBS component."""
    if local_ytd is None or payload is None or payload.ubs_federal_withholding is not None:
        return None
    component = local_ytd.ubs_withholding_by_owner.get(slot)
    total = float(payload.values.get("federal_withholding_ytd", 0.0))
    if component is None or total >= component:
        return None
    return (
        f"The {slot} slot's federal withholding in this bundle ({total:,.2f}) is below the "
        f"UBS option-exercise withholding already recorded here ({component:,.2f}); the UBS "
        f"amount is kept and {slot}'s other withholding is set to 0."
    )


def _slot_ytd_after(
    base: YTDSnapshot, slot: str, payload: Any, *, as_of: str | None
) -> YTDSnapshot:
    """*base* (already attributed) with *slot*'s YTD replaced by *payload* -- never added.

    Income events owned by *slot* are replaced wholesale; ``ira_distributions_ytd``
    moves only by the delta between that owner's old and new distribution
    events, so a legacy snapshot holding a total with no events is never zeroed
    (plan R3). The conversion field takes the slot's declared total.

    Redesign PR 1b: a local hand entry for *slot* whose as-of is later than
    *as_of* (the slot's captured_at) is restored over the incoming value; the
    sender's marks are adopted for every field where the incoming value won,
    but only when a mark matches the value the bundle carries and is not dated
    after the slot's capture (or after today, for an undated slot). An
    unparsable captured_at counts as undated: the import wins, as before 1b.

    The slot's UBS withholding component is replaced too when the bundle
    carries one. A bundle without it (a pre-1b sender) carries only the total,
    which already includes UBS: a receiver that holds a component for *slot*
    keeps it and takes the rest as base (see _pre_1b_withholding_base), so a
    later UBS re-scan cannot add it twice; with no local component the total
    is the base, as before.
    """
    as_of = iso_day(as_of)
    values = dict(payload.values)
    components = {o: v for o, v in base.ubs_withholding_by_owner.items() if o != slot}
    if payload.ubs_federal_withholding is not None:
        components[slot] = payload.ubs_federal_withholding
    elif slot in base.ubs_withholding_by_owner:
        components[slot] = base.ubs_withholding_by_owner[slot]
        values["federal_withholding_ytd"] = _pre_1b_withholding_base(base, slot, payload)
    out = base.replace_owner_values(slot, values)
    out = replace(out, ubs_withholding_by_owner=components)
    kept = set(out.settle_manual_marks(as_of=as_of))
    cutoff = as_of or date.today().isoformat()
    for name, mark in payload.manual.items():
        key = manual_key(name, slot)
        if key in kept:
            continue
        if abs(mark.value - out.owner_value(slot, name)) >= 0.005 or mark.as_of > cutoff:
            continue
        out.manual_marks[key] = mark
    old_events = [e for e in out.income_events if e.owner == slot]
    kept_events = [e for e in out.income_events if e.owner != slot]
    new_events = [copy.deepcopy(e) for e in payload.income_events]
    old_dist = sum(e.amount for e in old_events if e.kind == "distribution")
    new_dist = sum(e.amount for e in new_events if e.kind == "distribution")
    setattr(out, _CONVERSION_FIELD[slot], float(payload.ira_conversions))
    out.income_events = kept_events + new_events
    out.ira_distributions_ytd = max(0.0, out.ira_distributions_ytd - old_dist + new_dist)
    return out


def _ytd_fields(ytd: YTDSnapshot | None, slot: str) -> dict[str, float]:
    """The values a slot's YTD import touches, for preview diffs."""
    names = (*OWNED_YTD_FIELDS, _CONVERSION_FIELD[slot])
    if ytd is None:
        return dict.fromkeys(names, 0.0)
    owned = ytd.by_owner.get(slot, {})
    out = {f: float(owned.get(f, 0.0)) for f in OWNED_YTD_FIELDS}
    if ytd.by_owner:
        # Redesign PR 1b: preview the person's withholding TOTAL (base + UBS).
        out["federal_withholding_ytd"] = ytd.withholding_total(slot)
    out[_CONVERSION_FIELD[slot]] = float(getattr(ytd, _CONVERSION_FIELD[slot]))
    return out


def _check_operator(operator: str) -> None:
    if operator not in _SLOTS:
        raise ValueError(f"unknown operator slot {operator!r}; expected one of {_SLOTS}")


# ---------------------------------------------------------------------------
# A6: plan
# ---------------------------------------------------------------------------


def _plan_v5_slot(
    bundle: BundleV5, slot: str, payload: SlotPayload, local: LocalState, operator: str
) -> SlotPlan:
    local_captured = local.slot_captured_at.get(slot)
    scalar_updates = {_session_key(k): v for k, v in payload.setup_scalars.items()}
    applies_records = _applies_records(bundle, slot)
    incoming = [a for a in bundle.records.accounts if a.owner == slot] if applies_records else []
    incoming_names = {a.account_name for a in incoming}
    local_owned = [a for a in _local_accounts(local) if _owner(a) == slot]
    replaced = sum(1 for a in local_owned if _name(a) in incoming_names) if applies_records else 0
    removed = (
        sum(1 for a in local_owned if _name(a) not in incoming_names) if applies_records else 0
    )

    ytd_writes: dict[str, tuple[float, float]] = {}
    if _applies_ytd(bundle, slot) and payload.ytd is not None:
        # Same legacy migration apply performs (plan R5): an unattributed
        # local total belongs to the operator before any slot is replaced.
        base = (local.ytd or YTDSnapshot()).attribute_unowned(operator)
        before = _ytd_fields(base, slot)
        after = _ytd_fields(
            _slot_ytd_after(base, slot, payload.ytd, as_of=payload.captured_at), slot
        )
        ytd_writes = {f: (before[f], after[f]) for f in after if before[f] != after[f]}

    return SlotPlan(
        slot=slot,
        incoming_captured_at=payload.captured_at,
        local_captured_at=local_captured,
        stale=_is_stale(payload.captured_at, local_captured),
        scalar_writes=_writes(scalar_updates, local.scalars),
        accounts_incoming=len(incoming),
        accounts_replaced=replaced,
        accounts_removed=removed,
        ledger_rows=_ledger_rows(bundle, slot) if applies_records else 0,
        ytd_writes=ytd_writes,
        grants_replaced=bool(payload.grants),
    )


def _household_plan(bundle: BundleV5, local: LocalState) -> HouseholdPlan:
    local_names = {_name(a) for a in _local_accounts(local) if _owner(a) == "household"}
    incoming = [a for a in bundle.records.accounts if a.owner == "household"]
    upserted = sum(1 for a in incoming if a.account_name in local_names)
    return HouseholdPlan(accounts_upserted=upserted, accounts_added=len(incoming) - upserted)


def _plan_v5(bundle: BundleV5, local: LocalState, operator: str) -> ImportPlan:
    warnings = list(bundle.warnings)
    slots: dict[str, SlotPlan] = {}
    for slot, payload in bundle.slots.items():
        slots[slot] = _plan_v5_slot(bundle, slot, payload, local, operator)
        if _applies_ytd(bundle, slot):
            low = _pre_1b_withholding_warning(local.ytd, slot, payload.ytd)
            if low:
                warnings.append(low)
        if not _applies_records(bundle, slot):
            warnings.append(
                f"The {slot} slot carries no accounts or ledger rows; "
                f"your local {slot} records are kept as they are."
            )
    joint_updates, conflicts = _joint_updates(bundle.joint, local)
    return ImportPlan(
        version="v5",
        declared_by=bundle.exported_by,
        slots=slots,
        household=_household_plan(bundle, local),
        joint_writes=_writes(joint_updates, local.scalars),
        joint_conflicts=conflicts,
        warnings=tuple(warnings),
    )


def _v4_updates(raw: Mapping[str, Any], target: str, local: LocalState) -> dict[str, Any]:
    """The historical v4 session updates, exactly as the view computed them."""
    setup = dict(raw.get("sections", {}).get("setup_scalars") or {})
    as_spouse = target == "spouse"
    return build_user_defaults_session_updates(
        setup,
        as_spouse=as_spouse,
        receiver_persisted=dict(local.persisted_scalars) if as_spouse else None,
    )


def _plan_v4(raw: Mapping[str, Any], local: LocalState, target: str) -> ImportPlan:
    sections = raw.get("sections", {}) or {}
    updates = _v4_updates(raw, target, local)
    conflicts = tuple(updates.pop("_bundle_joint_conflicts", ()) or ())
    scalar_updates = {
        k: v for k, v in updates.items() if not k.startswith("_") and k != "account_type_overrides"
    }
    incoming = list((sections.get("portfolio") or {}).get("accounts") or [])
    incoming_names = {_name(a) for a in incoming}
    local_owned = [a for a in _local_accounts(local) if _owner(a) == target]
    ledger_slice = sections.get("ledger") or {}
    ledger_rows = (1 if ledger_slice.get("koinly") else 0) + len(
        ledger_slice.get("brokerage") or {}
    )
    slot = SlotPlan(
        slot=target,
        incoming_captured_at=None,
        local_captured_at=local.slot_captured_at.get(target),
        stale=None,
        scalar_writes=_writes(scalar_updates, local.scalars),
        accounts_incoming=len(incoming),
        accounts_replaced=sum(1 for a in local_owned if _name(a) in incoming_names),
        accounts_removed=sum(1 for a in local_owned if _name(a) not in incoming_names),
        ledger_rows=ledger_rows,
        ytd_writes={},  # PR #497: a v4 import never adopts the sender's YTD
        grants_replaced=isinstance(sections.get("grants"), list),
    )
    return ImportPlan(
        version="v4",
        declared_by=None,
        slots={target: slot},
        household=HouseholdPlan(accounts_upserted=0, accounts_added=0),
        joint_writes={},
        joint_conflicts=conflicts,
        warnings=(),
    )


def plan_import(
    bundle: BundleV5 | Mapping[str, Any],
    local: LocalState,
    *,
    v4_target: str = "spouse",
    operator: str = "you",
) -> ImportPlan:
    """Describe what importing *bundle* would do. Pure: mutates nothing.

    *bundle* is a parsed :class:`BundleV5` or a decrypted payload dict (v1-v5).
    For a legacy v4 file, which cannot declare whose data it is, ``v4_target``
    names the slot it lands in -- ``spouse`` by default (the user's rule), or
    ``you`` to restore one's own backup. ``operator`` is who holds this copy
    (``"you"`` when identity is unset, plan R4); it owns any legacy,
    unattributed YTD total, exactly as :func:`apply_import` will treat it.
    """
    if v4_target not in _SLOTS:
        raise ValueError(f"v4_target must be one of {_SLOTS}, got {v4_target!r}")
    _check_operator(operator)
    parsed = _as_bundle(bundle)
    local = copy.deepcopy(local)
    if isinstance(parsed, BundleV5):
        return _plan_v5(parsed, local, operator)
    return _plan_v4(parsed, local, v4_target)


# ---------------------------------------------------------------------------
# A7: apply (pure, transactional)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportResult:
    """The complete post-import state, built without touching the caller's.

    ``scalar_updates`` are session-state writes for the view to stage;
    ``magi_candidates`` go through ``record_magi_candidates`` (Source.BUNDLE),
    never a session replace (audit defect #2).
    """

    snapshot: PortfolioSnapshot
    ledger: dict[str, Any]
    ytd: YTDSnapshot | None
    scalar_updates: dict[str, Any]
    account_type_overrides: dict[str, Any]
    slot_captured_at: dict[str, str | None]
    magi_candidates: dict[int, float]
    joint_conflicts: tuple[dict[str, Any], ...]


# Accounts are grouped by owner after an import, keeping each owner's own
# order, so re-applying the same bundle yields the same list (idempotence).
_OWNER_RANK: dict[str, int] = {"you": 0, "spouse": 1, "household": 2}


def _imported(acct: AccountSummary) -> AccountSummary:
    """A copy of a bundle account, marked as bundle-sourced.

    The receiver's FinExtract sync closes any account it no longer reports
    unless it is statement- or bundle-sourced, so without the mark a sync from
    the operator's own FinExtract silently dropped the spouse's accounts
    (audit-1008 F-11). A statement-sourced account keeps its stronger mark.
    """
    out = copy.deepcopy(acct)
    if out.as_of_source != "statement":
        out.as_of_source = "bundle"
    return out


def _apply_accounts(
    snapshot: PortfolioSnapshot, bundle: BundleV5, record_slots: frozenset[str], any_selected: bool
) -> None:
    """Replace each record slot's accounts; upsert household ones by account_name."""
    incoming_household = (
        {a.account_name: a for a in bundle.records.accounts if a.owner == "household"}
        if any_selected
        else {}
    )
    out: list[Any] = []
    upserted: set[str] = set()
    for acct in snapshot.accounts:
        owner, name = _owner(acct), _name(acct)
        if owner in record_slots:
            continue
        if owner == "household" and name in incoming_household:
            out.append(_imported(incoming_household[name]))
            upserted.add(name)
            continue
        out.append(acct)
    out += [_imported(a) for a in bundle.records.accounts if a.owner in record_slots]
    out += [_imported(a) for n, a in incoming_household.items() if n not in upserted]
    out.sort(key=lambda a: _OWNER_RANK.get(_owner(a), len(_OWNER_RANK)))
    snapshot.accounts = out


def _apply_ledger(
    ledger: Mapping[str, Any], bundle: BundleV5, record_slots: frozenset[str], any_selected: bool
) -> dict[str, Any]:
    """Replace each record slot's ledger rows; merge household rows, never delete them."""
    out: dict[str, Any] = copy.deepcopy(dict(ledger))
    out.setdefault("koinly", {})
    out.setdefault("brokerage", {})
    incoming = bundle.records.ledger
    for slot in sorted(record_slots):
        out = replace_owner(
            out, slot, {s: incoming.get(s, {}).get(slot) or {} for s in ("koinly", "brokerage")}
        )
    winners = set(record_slots)
    if any_selected:
        household_koinly = incoming.get("koinly", {}).get("household")
        if household_koinly:
            out["koinly"]["household"] = copy.deepcopy(household_koinly)
            winners.add("household")
        household_brokerage = incoming.get("brokerage", {}).get("household")
        if household_brokerage:
            out["brokerage"].setdefault("household", {}).update(copy.deepcopy(household_brokerage))
            winners.add("household")
    # Move, never copy: the same account / report under a receiver-side owner
    # would be summed twice by derive_*_totals (audit-1018 F-08).
    return prune_duplicates_elsewhere(out, frozenset(winners))


def _apply_ytd(
    ytd: YTDSnapshot | None, bundle: BundleV5, ytd_slots: frozenset[str], operator: str
) -> YTDSnapshot | None:
    """Attribute any legacy total to the operator (R5), then replace each slot."""
    if not ytd_slots:
        return copy.deepcopy(ytd)
    out = (copy.deepcopy(ytd) or YTDSnapshot()).attribute_unowned(operator)
    for slot in sorted(ytd_slots):
        out = _slot_ytd_after(
            out, slot, bundle.slots[slot].ytd, as_of=bundle.slots[slot].captured_at
        )
    return out


def _apply_overrides(
    overrides: Mapping[str, Any], bundle: BundleV5, record_slots: frozenset[str]
) -> dict[str, Any]:
    """Replace each record slot's account_type_overrides; keep the rest in their own form."""
    out = {
        k: copy.deepcopy(v)
        for k, v in overrides.items()
        if _resolve_override(v)[1] not in record_slots
    }
    for account_id, entry in bundle.records.account_type_overrides.items():
        if entry["owner"] in record_slots:
            out[account_id] = dict(entry)
    return out


def _apply_v5(
    bundle: BundleV5, local: LocalState, selected: frozenset[str], operator: str
) -> ImportResult:
    any_selected = bool(selected)
    record_slots = frozenset(s for s in selected if _applies_records(bundle, s))
    ytd_slots = frozenset(s for s in selected if _applies_ytd(bundle, s))

    snapshot = copy.deepcopy(local.snapshot) if local.snapshot is not None else PortfolioSnapshot()
    _apply_accounts(snapshot, bundle, record_slots, any_selected)
    if any_selected:
        # audit-1008 F-01/F-06: mark the data as real even though nothing was
        # fetched, so growth and grants are derived from it downstream.
        snapshot.imported_from_bundle = True
    for slot in sorted(selected):
        grants = bundle.slots[slot].grants
        if grants:  # an empty slot list is a no-op (plan R7)
            snapshot.equity_grants = [copy.deepcopy(g) for g in grants]
            # audit-1008 F-05: remember whose grants these are, so this
            # instance's next export files them under that person's slot.
            snapshot.equity_grants_owner = slot

    ledger = _apply_ledger(local.ledger, bundle, record_slots, any_selected)
    ytd = _apply_ytd(local.ytd, bundle, ytd_slots, operator)

    scalar_updates: dict[str, Any] = {}
    captured = dict(local.slot_captured_at)
    for slot in sorted(selected):
        payload = bundle.slots[slot]
        scalar_updates.update({_session_key(k): v for k, v in payload.setup_scalars.items()})
        if payload.grant_strikes:
            scalar_updates["_user_grant_strikes"] = dict(payload.grant_strikes)
        if payload.captured_at:
            captured[slot] = _newer_captured_at(captured.get(slot), payload.captured_at)

    conflicts: tuple[dict[str, Any], ...] = ()
    magi: dict[int, float] = {}
    if any_selected:
        joint_updates, conflicts = _joint_updates(bundle.joint, local)
        if "survivor" in joint_updates:
            # Keep the Enable checkbox in step, as the v4 passthrough does.
            joint_updates["_survivor_enabled"] = bool(joint_updates["survivor"])
        scalar_updates.update(joint_updates)
        magi = extract_bundle_magi(dict(bundle.joint))

    return ImportResult(
        snapshot=snapshot,
        ledger=ledger,
        ytd=ytd,
        scalar_updates=scalar_updates,
        account_type_overrides=_apply_overrides(local.account_type_overrides, bundle, record_slots),
        slot_captured_at=captured,
        magi_candidates=magi,
        joint_conflicts=conflicts,
    )


def _apply_v4(
    raw: Mapping[str, Any],
    local: LocalState,
    target: str,
    apply_v4_bundle: Callable[..., tuple[PortfolioSnapshot, dict[str, Any]]],
) -> ImportResult:
    """The historical v4 path: apply_bundle + the upload_merge cross-map, on copies.

    YTD is the receiver's own, unchanged -- a v4 bundle's ytd section is
    household-wide from the SENDER's perspective and is never adopted (PR #497).
    """
    payload: dict[str, Any] = copy.deepcopy(dict(raw))
    sections = payload.setdefault("sections", {})
    accounts = (sections.get("portfolio") or {}).get("accounts") or []
    sections["portfolio"] = {
        "accounts": [account_summary_from_dict(a) if isinstance(a, dict) else a for a in accounts]
    }
    snapshot = copy.deepcopy(local.snapshot) if local.snapshot is not None else PortfolioSnapshot()
    new_snapshot, new_ledger = apply_v4_bundle(
        target,
        payload,
        existing_snapshot=snapshot,
        existing_ledger=copy.deepcopy(dict(local.ledger)),
    )
    # audit-1008 F-01/F-06/F-05: see _apply_v5.
    new_snapshot.imported_from_bundle = True
    if isinstance(sections.get("grants"), list):
        new_snapshot.equity_grants_owner = target
    updates = _v4_updates(payload, target, local)
    conflicts = tuple(updates.pop("_bundle_joint_conflicts", ()) or ())
    overrides = updates.pop("account_type_overrides", None)
    setup = sections.get("setup_scalars") or {}
    return ImportResult(
        snapshot=new_snapshot,
        ledger=new_ledger,
        ytd=copy.deepcopy(local.ytd),
        scalar_updates=updates,
        account_type_overrides=(
            overrides
            if overrides is not None
            else copy.deepcopy(dict(local.account_type_overrides))
        ),
        slot_captured_at=dict(local.slot_captured_at),
        # As the view did: the spouse cross-map never touches prior_year_magi.
        magi_candidates=extract_bundle_magi(setup) if target != "spouse" else {},
        joint_conflicts=conflicts,
    )


def _validate(result: ImportResult, local: LocalState) -> None:
    """Refuse a result that would empty a populated portfolio (audit-0823 PS-2b)."""
    had_accounts = bool(getattr(local.snapshot, "accounts", None))
    if had_accounts and not result.snapshot.accounts:
        raise EmptySnapshotWriteRefusedError(
            "This import would remove every account from the portfolio; nothing was changed."
        )


def apply_import(
    bundle: BundleV5 | Mapping[str, Any],
    plan: ImportPlan,
    local: LocalState,
    *,
    selected: frozenset[str],
    operator: str,
    apply_v4_bundle: Callable[..., tuple[PortfolioSnapshot, dict[str, Any]]] | None = None,
) -> ImportResult:
    """Build the post-import state for the *selected* slots. Pure and transactional.

    Works on deep copies of *local* and *bundle*: on any failure the caller's
    objects are untouched, and on success they are untouched too -- the view
    swaps in the returned :class:`ImportResult` only after
    :func:`commit_import` has written it. Deselected slots keep their scalars,
    records and YTD exactly as they were.

    ``apply_v4_bundle`` is the legacy v4 apply step, defaulting to
    :func:`engine.bridge_bundle.apply_bundle`; it is injectable, like
    :func:`commit_import`'s savers, so the view can supply its own seam.
    """
    _check_operator(operator)
    unknown = set(selected) - set(plan.slots)
    if unknown:
        raise ValueError(
            f"selected slots {sorted(unknown)} are not in the plan {sorted(plan.slots)}"
        )
    parsed = _as_bundle(bundle)
    if isinstance(parsed, BundleV5) != (plan.version == "v5"):
        raise ValueError(f"plan is for a {plan.version} bundle but a different one was supplied")
    local = copy.deepcopy(local)
    if isinstance(parsed, BundleV5):
        result = _apply_v5(parsed, local, frozenset(selected), operator)
    elif selected:
        result = _apply_v4(parsed, local, next(iter(plan.slots)), apply_v4_bundle or apply_bundle)
    else:
        result = _unchanged(local)
    _validate(result, local)
    return result


def commit_import(
    result: ImportResult,
    *,
    save_snapshot: Callable[..., None],
    save_ledger: Callable[[Any], None],
    save_ytd: Callable[[Any], None],
) -> None:
    """Write *result* to the three caches in order: snapshot, ledger, YTD.

    The caller runs this inside one store transaction (spec §2), so a failed
    write rolls back the ones before it; this function only orders the writes
    and stops at the first failure. The savers are injected so this module
    stays pure (no Streamlit, no store). The snapshot write passes
    ``allow_empty=False`` (audit-0823 PS-2b).
    """
    save_snapshot(result.snapshot, allow_empty=False)
    save_ledger(result.ledger)
    if result.ytd is not None:
        save_ytd(result.ytd)


def _unchanged(local: LocalState) -> ImportResult:
    """Nothing selected: the result is the local state, unchanged."""
    return ImportResult(
        snapshot=local.snapshot if local.snapshot is not None else PortfolioSnapshot(),
        ledger=dict(local.ledger),
        ytd=local.ytd,
        scalar_updates={},
        account_type_overrides=dict(local.account_type_overrides),
        slot_captured_at=dict(local.slot_captured_at),
        magi_candidates={},
        joint_conflicts=(),
    )
