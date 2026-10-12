"""Unsaved YTD manual-entry drafts (redesign PR 1b). Pure: no Streamlit import.

Drafts live in two non-widget session keys, so they survive page switches and
You/Spouse toggling (Streamlit drops the state of widgets that are not
rendered, never of plain keys). They are session-only: a full reload loses them.

- DRAFT_KEY: {manual_key: {"value": typed, "base": shown saved value when editing began}}
- DRAFT_META_KEY: {owner: {"as_of": "YYYY-MM-DD", "note": str}}

``base`` makes a draft stale-aware: when the saved value no longer matches it,
an import changed the field after editing began, and the draft is discarded
rather than silently overriding newer imported data.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from models.ytd_income import (
    HOUSEHOLD,
    MANUAL_HOUSEHOLD_FIELDS,
    OWNED_YTD_FIELDS,
    YTD_OWNERS,
    YTDSnapshot,
    split_manual_key,
)

DRAFT_KEY = "ytd_draft"
DRAFT_META_KEY = "ytd_draft_meta"
_SAME = 0.005

OWNER_LABELS = {"you": "You", "spouse": "Spouse", HOUSEHOLD: "Household"}
FIELD_LABELS = {
    "wages_ytd": "Wages",
    "federal_withholding_ytd": "Federal tax withheld",
    "qualified_dividends_ytd": "Qualified dividends",
    "nqo_exercise_ytd": "NQO exercise income",
    "nec_income_ytd": "1099-NEC / self-employment income",
    "ltcg_ytd": "Long-term capital gains",
    "stcg_ytd": "Short-term capital gains",
    "ordinary_dividends_ytd": "Ordinary dividends",
    "interest_ytd": "Interest",
    "tax_exempt_interest_ytd": "Tax-exempt (muni) interest",
    "estimated_payments_ytd": "Estimated tax payments",
    "hsa_contribution_ytd": "HSA contribution",
    "deductible_ira_contribution_ytd": "Deductible IRA contribution",
    "crypto_stcg_ytd": "Crypto short-term gains",
    "crypto_ltcg_ytd": "Crypto long-term gains",
    "crypto_income_ytd": "Crypto income",
    "crypto_interest_ytd": "Crypto lending interest",
}
# Fields shown in a %.2f widget; every other manual field is an integer widget.
FLOAT_FIELDS = frozenset(
    {"crypto_stcg_ytd", "crypto_ltcg_ytd", "crypto_income_ytd", "crypto_interest_ytd"}
)
_FIELD_ORDER = {f: i for i, f in enumerate((*OWNED_YTD_FIELDS, *MANUAL_HOUSEHOLD_FIELDS))}
_OWNER_ORDER = {o: i for i, o in enumerate((*YTD_OWNERS, HOUSEHOLD))}


def shown(field_name: str, value: float) -> float:
    """What the form displays for a saved *value* (int widgets truncate, as before)."""
    return round(value, 2) if field_name in FLOAT_FIELDS else float(int(value))


def _parse(key: object) -> tuple[str, str] | None:
    if not isinstance(key, str):
        return None
    try:
        return split_manual_key(key)
    except ValueError:
        return None


def _entry(raw: Any) -> tuple[float, float] | None:
    if not isinstance(raw, Mapping):
        return None
    value, base = raw.get("value"), raw.get("base")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (value, base)):
        return None
    return float(value), float(base)


def _shown_now(key: str, ytd: YTDSnapshot) -> float:
    field_name = split_manual_key(key)[1]
    return shown(field_name, ytd.manual_value(key))


def put_draft(
    drafts: Mapping[str, Any] | None, key: str, value: float, ytd: YTDSnapshot
) -> dict[str, dict[str, float]]:
    """A copy of *drafts* with *key* set to *value*; the base is kept from the first edit."""
    out = {k: dict(v) for k, v in (drafts or {}).items() if isinstance(v, Mapping)}
    existing = _entry(out.get(key))
    base = existing[1] if existing else _shown_now(key, ytd)
    out[key] = {"value": float(value), "base": base}
    return out


def reconcile_drafts(
    drafts: Mapping[str, Any] | None, ytd: YTDSnapshot | None
) -> tuple[dict[str, dict[str, float]], list[str]]:
    """(live drafts, keys discarded as stale) against the current saved snapshot.

    A draft equal to what the form shows for the saved value is dropped
    (nothing to save). A draft whose base no longer matches the saved value is
    stale -- an import changed the field after editing began -- and is dropped
    and reported. Malformed entries are dropped silently.
    """
    current = ytd or YTDSnapshot()
    live: dict[str, dict[str, float]] = {}
    stale: list[str] = []
    for key, raw in (drafts or {}).items():
        entry = _entry(raw)
        if _parse(key) is None or entry is None:
            continue
        value, base = entry
        now = _shown_now(key, current)
        if abs(now - base) >= _SAME:
            stale.append(key)
        elif abs(value - now) >= _SAME:
            live[key] = {"value": value, "base": base}
    return live, stale


def has_unsaved(drafts: Mapping[str, Any] | None, ytd: YTDSnapshot | None) -> bool:
    """True when at least one draft would change a saved value."""
    return bool(reconcile_drafts(drafts, ytd)[0])


def describe_keys(keys: Iterable[object]) -> list[str]:
    """ "Person: Field" per key, people then fields in form order."""
    parsed = [p for p in map(_parse, keys) if p is not None]
    parsed.sort(key=lambda p: (_OWNER_ORDER[p[0]], _FIELD_ORDER[p[1]]))
    return [f"{OWNER_LABELS[o]}: {FIELD_LABELS[f]}" for o, f in parsed]


def commit_drafts(
    ytd: YTDSnapshot,
    drafts: Mapping[str, Any],
    meta: Mapping[str, Mapping[str, str]],
    *,
    current_owner: str,
    today: str,
) -> YTDSnapshot:
    """Write every draft into a new snapshot, marking each with its as-of and note.

    Each person's drafts use that person's meta; household drafts use the meta
    of *current_owner* (selected when Save was clicked). Missing meta means
    as-of *today* and no note. A legacy snapshot is attributed to
    *current_owner* first. Callers pass reconciled (live) drafts.
    """
    out = ytd if ytd.by_owner else ytd.attribute_unowned(current_owner)
    grouped: dict[str, dict[str, float]] = {}
    for key, raw in drafts.items():
        parsed, entry = _parse(key), _entry(raw)
        if parsed is not None and entry is not None:
            grouped.setdefault(parsed[0], {})[parsed[1]] = entry[0]

    def _meta(owner: str) -> tuple[str, str]:
        m = meta.get(owner) or {}
        return (m.get("as_of") or today), (m.get("note") or "")

    for owner in YTD_OWNERS:
        owned = grouped.get(owner)
        if owned:
            as_of, note = _meta(owner)
            out = out.with_manual_values(
                owner=owner, owned=owned, household={}, as_of=as_of, note=note
            )
    household = grouped.get(HOUSEHOLD)
    if household:
        as_of, note = _meta(current_owner)
        out = out.with_manual_values(
            owner=current_owner, owned={}, household=household, as_of=as_of, note=note
        )
    return out
