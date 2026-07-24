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
