"""Pure data-completeness/staleness/conflict computation for governed fields.

Pure module: stdlib + models/ only. No streamlit imports (this repo's
``engine/`` package is strictly Streamlit-free).

This is the shared substrate behind the Setup / Command Center's governed
fields (``your_ira``, ``spouse_ira``, per-year ``prior_year_magi.<year>``,
``grants``, ...) — see ``engine.data_sources.resolver`` /
``engine.data_sources.confirm`` for how those fields get their provenance
(``models.sourced.Provenance``) and how a pending Command Center candidate
is surfaced as ``ResolveResult.pending_review``.

Deliberately reusable beyond the Phase 1 Contextual shell's status bar: a
future Wizard shell's step-validation should extend this SAME module rather
than duplicate it (see the UI-shell-theme-toggle plan's "Future
Enhancements" section). Keep it pure and keep ``now`` an explicit parameter
(never ``datetime.now()`` internally) so callers can evaluate "as of when"
deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from models.household import Household
from models.sourced import Provenance, SourcedDict, SourcedList, SourcedValue
from models.ytd_income import YTDSnapshot

STALE_THRESHOLD_DAYS = 7

GRANTS_KEY = "grants"
_MAGI_PREFIX = "prior_year_magi."

_FIELD_LABELS: dict[str, str] = {
    "your_ira": "Your IRA balance",
    "spouse_ira": "Spouse IRA balance",
    "your_roth": "Your Roth balance",
    "spouse_roth": "Spouse Roth balance",
    "txn_price_now": "Stock price",
    "your_ss_fra": "Your SS at FRA ($/mo)",
    "spouse_ss_fra": "Spouse SS at FRA ($/mo)",
    GRANTS_KEY: "Option grants",
}


def _field_label(field_key: str) -> str:
    """Human-readable label for a sourced field key."""
    if field_key.startswith(_MAGI_PREFIX):
        return f"Prior-year MAGI ({field_key[len(_MAGI_PREFIX) :]})"
    return _FIELD_LABELS.get(field_key, field_key)


def _magi_provenance(hh: Household, year: int) -> Provenance | None:
    baseline = hh.prior_year_magi
    if not isinstance(baseline, SourcedDict):
        return None
    return baseline.prov.get(year)


def _grants_provenance(hh: Household) -> Provenance | None:
    grants = hh.grants
    if not isinstance(grants, SourcedList) or not grants.prov:
        return None
    return grants.prov[0]


def _field_provenance(hh: Household, field_key: str) -> Provenance | None:
    """Return the confirmed ``Provenance`` for ``field_key``, or ``None`` if unsourced."""
    if field_key.startswith(_MAGI_PREFIX):
        return _magi_provenance(hh, int(field_key[len(_MAGI_PREFIX) :]))
    if field_key == GRANTS_KEY:
        return _grants_provenance(hh)
    if not hasattr(hh, field_key):
        return None
    value = getattr(hh, field_key)
    if not isinstance(value, SourcedValue):
        return None
    return value.prov


@dataclass(frozen=True)
class DataStatusItem:
    """One flagged governed field: missing, in conflict, or stale."""

    field: str
    label: str
    severity: str
    detail: str


def compute_data_status(
    hh: Household,
    sourced_fields: list[str],
    pending_candidates: set[str],
    *,
    now: datetime,
) -> list[DataStatusItem]:
    """Flag each field in ``sourced_fields`` that is missing, conflicted, or stale.

    - ``conflict``: ``field_key`` is in ``pending_candidates`` (an unconfirmed
      Command Center candidate is currently blocking it). Takes precedence
      over the other two checks.
    - ``missing``: no confirmed (provenance-carrying) value exists for the field.
    - ``stale``: a confirmed value exists, but its provenance ``recorded_at``
      is older than ``STALE_THRESHOLD_DAYS`` (as of ``now``).

    A field that is confirmed, not pending, and within the threshold is
    ``ok`` and is omitted from the returned list.
    """
    items: list[DataStatusItem] = []
    for field_key in sourced_fields:
        label = _field_label(field_key)

        if field_key in pending_candidates:
            items.append(
                DataStatusItem(
                    field=field_key,
                    label=label,
                    severity="conflict",
                    detail="A newer value is pending review in the Command Center.",
                )
            )
            continue

        prov = _field_provenance(hh, field_key)
        if prov is None:
            items.append(
                DataStatusItem(
                    field=field_key,
                    label=label,
                    severity="missing",
                    detail="No confirmed value on record.",
                )
            )
            continue

        age = now - prov.recorded_at
        if age > timedelta(days=STALE_THRESHOLD_DAYS):
            items.append(
                DataStatusItem(
                    field=field_key,
                    label=label,
                    severity="stale",
                    detail=f"Last confirmed {age.days} days ago.",
                )
            )

    return items


@dataclass(frozen=True)
class DataCompleteness:
    total: int
    ok: int
    issues: tuple[DataStatusItem, ...]

    @property
    def by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.issues:
            counts[item.severity] = counts.get(item.severity, 0) + 1
        return counts

    @property
    def is_complete(self) -> bool:
        return not any(i.severity in ('missing', 'conflict') for i in self.issues)

    @property
    def fraction(self) -> float:
        return self.ok / self.total if self.total else 1.0


def compute_data_completeness(
    hh: Household,
    sourced_fields: list[str],
    pending_candidates: set[str],
    *,
    now: datetime,
) -> DataCompleteness:
    items = compute_data_status(hh, sourced_fields, pending_candidates, now=now)
    flagged = {i.field for i in items}
    ok = len(sourced_fields) - len(flagged)
    return DataCompleteness(total=len(sourced_fields), ok=ok, issues=tuple(items))


# Best-guess assignment of governed scalar fields to the 5 Setup steps.
# Static scalar groups only; dynamic prior_year_magi.<year> keys are appended
# at runtime for the "assumptions" step (see governed_fields_for_step).
SETUP_STEP_GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("household", "Household", ("your_ss_fra", "spouse_ss_fra")),
    ("accounts", "Accounts", ("your_ira", "spouse_ira", "your_roth", "spouse_roth")),
    ("options", "Options", ("txn_price_now", GRANTS_KEY)),
    ("portfolio", "Portfolio", ()),
    ("assumptions", "Assumptions", ()),
]


def governed_fields_for_step(hh: Household, step_key: str) -> list[str]:
    for key, _label, fields in SETUP_STEP_GROUPS:
        if key == step_key:
            result = list(fields)
            if step_key == "assumptions":
                from engine.data_sources.resolver import magi_field_key  # lazy: avoid import cycle

                result += [magi_field_key(y) for y in sorted(hh.prior_year_magi.keys())]
            return result
    raise ValueError("Unknown setup step: " + repr(step_key))


YTD_STALE_AFTER_DAYS = 14


def compute_ytd_completeness(snapshot: YTDSnapshot, *, now: datetime) -> DataCompleteness:
    """Flag a YTDSnapshot as missing (never saved) or stale (not updated recently).

    Unlike compute_data_completeness, this does not check field-level presence:
    YTDSnapshot's manual-entry fields legitimately default to 0.0 (e.g. no wages
    recorded yet in January), so an "is it populated" check would false-positive
    constantly. The only meaningful signal available is when the snapshot was
    last saved (snapshot_date), so this checks staleness only.
    """
    if not snapshot.snapshot_date:
        item = DataStatusItem(
            field="snapshot_date",
            label="YTD snapshot",
            severity="missing",
            detail="No YTD data recorded yet.",
        )
        return DataCompleteness(total=1, ok=0, issues=(item,))

    try:
        recorded = datetime.fromisoformat(snapshot.snapshot_date)
    except ValueError:
        item = DataStatusItem(
            field="snapshot_date",
            label="YTD snapshot",
            severity="missing",
            detail=f"Unrecognized snapshot_date value: {snapshot.snapshot_date!r}.",
        )
        return DataCompleteness(total=1, ok=0, issues=(item,))

    age = now - recorded
    if age > timedelta(days=YTD_STALE_AFTER_DAYS):
        item = DataStatusItem(
            field="snapshot_date",
            label="YTD snapshot",
            severity="stale",
            detail=f"Last updated {age.days} days ago.",
        )
        return DataCompleteness(total=1, ok=0, issues=(item,))

    return DataCompleteness(total=1, ok=1, issues=())


def compute_exercise_completeness(hh: Household) -> DataCompleteness:
    """Flag each non-expired StockGrant with unallocated shares.

    Setup-style presence/allocation check, not YTD-style staleness:
    ExerciseSchedule carries no timestamp field, so "is it fully allocated"
    is the only meaningful signal. Reads hh.exercise_schedule directly
    (NOT hh.effective_schedule()) -- the default_at_expiry() fallback that
    effective_schedule() applies for a missing/empty schedule is always
    100%-allocated by construction, which would mask exactly the "nothing
    confirmed yet" case this validator exists to flag.
    """
    non_expired = [g for g in hh.grants if g.expiry_year >= hh.base_year]
    if not non_expired:
        return DataCompleteness(total=0, ok=0, issues=())

    schedule = hh.exercise_schedule
    if schedule is None:
        item = DataStatusItem(
            field="exercise_schedule",
            label="Exercise schedule",
            severity="missing",
            detail="No exercise plan confirmed -- using default hold-to-expiry allocation.",
        )
        return DataCompleteness(total=len(non_expired), ok=0, issues=(item,))

    issues: list[DataStatusItem] = []
    for grant in non_expired:
        remaining = schedule.remaining(grant)
        if remaining > 0:
            issues.append(
                DataStatusItem(
                    field=grant.key(),
                    label=f"{grant.year} grant (${grant.strike:g})",
                    severity="missing",
                    detail=f"{remaining:,} shares not yet allocated to an exercise year.",
                )
            )
    ok = len(non_expired) - len(issues)
    return DataCompleteness(total=len(non_expired), ok=ok, issues=tuple(issues))
