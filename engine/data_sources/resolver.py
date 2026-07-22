"""Freeze-until-confirm arbitration between competing candidate values.

Pure module: stdlib + models/ + sibling engine.data_sources modules only. No
streamlit imports.

Core invariant: **a committed (Sourced*) value never changes on resolve()**.
Loading a fresh candidate (e.g. a new FinExtract sync) never silently
clobbers a value the user has already confirmed — it only raises a flag
(``pending_review``) so the UI can surface "here's a newer number, want to
switch?" without ever acting on it automatically. ``confirm()`` is the only
function that mutates a committed value.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from engine.data_sources.candidate_store import Candidate, CandidateStore
from engine.data_sources.choices import ChoiceMap, TrustChoice
from models.household import Household
from models.sourced import Provenance, Source, SourcedDict, SourcedList, SourcedValue

DEFAULT_LADDER: list[Source] = [
    Source.MANUAL,
    Source.PDF,
    Source.FINEXTRACT_LIVE,
    Source.MARKET_QUOTE,
    Source.ESTIMATE,
    Source.BUNDLE,
    Source.DEFAULT,
    Source.UNKNOWN,
]

# All fields the Setup / Command Center arbitrates over conceptually. Some of
# these (the *_ytd fields) are not yet attributes on Household — they live in
# YTD snapshots today. Those keys are handled gracefully (skipped) below;
# HOUSEHOLD_SCALAR_FIELDS is the subset that actually exists on Household.
SOURCED_SCALAR_FIELDS: list[str] = [
    "your_ira",
    "spouse_ira",
    "your_roth",
    "spouse_roth",
    "txn_price_now",
    "your_ss_fra",
    "spouse_ss_fra",
    "interest_ytd",
    "tax_exempt_interest_ytd",
    "ordinary_dividends_ytd",
    "stcg_ytd",
    "ltcg_ytd",
    "nqo_exercise_ytd",
]

HOUSEHOLD_SCALAR_FIELDS: list[str] = [
    "your_ira",
    "spouse_ira",
    "your_roth",
    "spouse_roth",
    "txn_price_now",
    "your_ss_fra",
    "spouse_ss_fra",
]

GRANTS_KEY = "grants"
_MAGI_PREFIX = "prior_year_magi."


def magi_field_key(year: int) -> str:
    return f"{_MAGI_PREFIX}{year}"


@dataclass
class ResolveResult:
    household: Household
    pending_review: set[str]


def _clone_value(value: Any) -> Any:
    """Deep-copy ``value``, special-casing Sourced* wrappers.

    ``copy.deepcopy`` cannot reconstruct a ``SourcedValue`` (a ``float``
    subclass) via the pickle-style reduction protocol, since its ``__new__``
    requires a mandatory ``prov`` argument the reducer doesn't supply. Since
    ``Provenance`` is itself frozen/immutable, a shallow rewrap is a correct
    deep copy for these three wrapper types.
    """
    if isinstance(value, SourcedValue):
        return SourcedValue(float(value), value.prov)
    if isinstance(value, SourcedDict):
        return SourcedDict(dict(value), dict(value.prov))
    if isinstance(value, SourcedList):
        return SourcedList(list(value), list(value.prov))
    return copy.deepcopy(value)


def _clone_household(hh: Household) -> Household:
    """Return a deep, independent copy of ``hh`` safe for Sourced* fields."""
    updates = {f.name: _clone_value(getattr(hh, f.name)) for f in dataclasses.fields(hh)}
    return Household(**updates)


def _candidate_for_source(candidates: list[Candidate], source: Source) -> Candidate | None:
    for c in candidates:
        if c.prov.source == source:
            return c
    return None


def _best_candidate(candidates: list[Candidate], ladder: list[Source]) -> Candidate | None:
    by_source = {c.prov.source: c for c in candidates}
    for source in ladder:
        if source in by_source:
            return by_source[source]
    return None


def _trusted_source_differs(
    candidates: list[Candidate],
    choice: TrustChoice | None,
    value_differs: Callable[[Candidate], bool],
) -> bool:
    """True if a candidate that should force ``pending_review`` exists.

    Once a field has an explicit trust ``choice`` (set by ``confirm()``),
    freeze-until-confirm means only THAT source's own candidates can
    re-open review — other sources continuing to report differing values
    (e.g. every FinExtract sync re-recording a stale number) must not nag.
    With no choice recorded yet, any differing candidate from any source
    still pends, preserving prior behavior.
    """
    if choice is not None:
        return any(value_differs(c) for c in candidates if str(c.prov.source) == str(choice.source))
    return any(value_differs(c) for c in candidates)


def _scalar_differs(target: float) -> Callable[[Candidate], bool]:
    """Bind ``target`` now so the returned predicate is safe in a loop body."""
    return lambda c: float(c.value) != float(target)


def _pick_candidate(
    field_key: str,
    candidates: list[Candidate],
    choices: ChoiceMap,
    ladder: list[Source],
) -> Candidate | None:
    choice = choices.get(field_key)
    if choice is not None:
        picked = _candidate_for_source(candidates, choice.source)
        if picked is not None:
            return picked
    return _best_candidate(candidates, ladder)


def _resolve_scalar_field(
    field_key: str,
    baseline: Any,
    store: CandidateStore,
    choices: ChoiceMap,
    ladder: list[Source],
) -> tuple[Any, bool]:
    """Return (resolved_value, is_pending) for one scalar field."""
    candidates = store.candidates_for(field_key)

    if isinstance(baseline, SourcedValue):
        choice = choices.get(field_key)
        is_pending = _trusted_source_differs(
            candidates, choice, lambda c: float(c.value) != float(baseline)
        )
        return baseline, is_pending

    picked = _pick_candidate(field_key, candidates, choices, ladder)
    if picked is not None:
        return SourcedValue(float(picked.value), picked.prov), True

    return baseline, False


def _resolve_magi(
    committed: Household,
    resolved: Household,
    store: CandidateStore,
    choices: ChoiceMap,
    ladder: list[Source],
    pending: set[str],
) -> None:
    baseline_dict = committed.prior_year_magi
    baseline_prov: dict[int, Provenance] = (
        dict(baseline_dict.prov) if isinstance(baseline_dict, SourcedDict) else {}
    )

    years: set[int] = set(baseline_dict.keys())
    for key in store.field_keys():
        if key.startswith(_MAGI_PREFIX):
            years.add(int(key[len(_MAGI_PREFIX) :]))

    if not years:
        return

    new_data: dict[int, float] = dict(baseline_dict)
    new_prov: dict[int, Provenance] = dict(baseline_prov)

    for year in years:
        field_key = magi_field_key(year)
        candidates = store.candidates_for(field_key)

        if year in baseline_prov:
            baseline_value = baseline_dict[year]
            choice = choices.get(field_key)
            if _trusted_source_differs(candidates, choice, _scalar_differs(baseline_value)):
                pending.add(field_key)
            continue

        picked = _pick_candidate(field_key, candidates, choices, ladder)
        if picked is not None:
            new_data[year] = float(picked.value)
            new_prov[year] = picked.prov
            pending.add(field_key)

    resolved.prior_year_magi = SourcedDict(new_data, new_prov)


def _resolve_grants(
    committed: Household,
    resolved: Household,
    store: CandidateStore,
    choices: ChoiceMap,
    ladder: list[Source],
    pending: set[str],
) -> None:
    baseline = committed.grants
    candidates = store.candidates_for(GRANTS_KEY)

    if isinstance(baseline, SourcedList):
        choice = choices.get(GRANTS_KEY)
        baseline_by_key = {g.key(): g for g in baseline}

        def _grants_differ(c: Candidate) -> bool:
            # Identity (key()) alone isn't enough: a grant matched by id/year
            # can still have drifted values (strike, shares, expiry_year) —
            # compare the full dataclass, not just its key set (audit-0721 C19).
            candidate_by_key = {g.key(): g for g in c.value}
            if set(candidate_by_key) != set(baseline_by_key):
                return True
            return any(candidate_by_key[k] != baseline_by_key[k] for k in candidate_by_key)

        if _trusted_source_differs(candidates, choice, _grants_differ):
            pending.add(GRANTS_KEY)
        return

    picked = _pick_candidate(GRANTS_KEY, candidates, choices, ladder)
    if picked is not None:
        value_list = list(picked.value)
        resolved.grants = SourcedList(value_list, [picked.prov] * len(value_list))
        pending.add(GRANTS_KEY)


def resolve(
    committed: Household,
    store: CandidateStore,
    choices: ChoiceMap,
    ladder: list[Source] = DEFAULT_LADDER,
) -> ResolveResult:
    """Arbitrate candidates against ``committed`` without mutating it.

    ``pending_review`` is derived fresh on every call (never persisted): a
    committed field is nudged into it only when a *different* candidate value
    exists; an uncommitted field is nudged into it whenever it was resolved
    from a candidate (choice or ladder), since that resolution wasn't yet
    explicitly confirmed by the user.
    """
    resolved = _clone_household(committed)
    pending: set[str] = set()

    for field_key in SOURCED_SCALAR_FIELDS:
        if not hasattr(committed, field_key):
            continue
        baseline = getattr(committed, field_key)
        new_value, is_pending = _resolve_scalar_field(field_key, baseline, store, choices, ladder)
        if new_value is not baseline:
            setattr(resolved, field_key, new_value)
        if is_pending:
            pending.add(field_key)

    _resolve_magi(committed, resolved, store, choices, ladder, pending)
    _resolve_grants(committed, resolved, store, choices, ladder, pending)

    return ResolveResult(household=resolved, pending_review=pending)


def confirm(
    field_key: str,
    source: Source,
    committed: Household,
    store: CandidateStore,
    choices: ChoiceMap,
    now: datetime,
    override_value: float | None = None,
) -> Household:
    """Freeze ``field_key`` on a NEW Household, locking in ``source``.

    This is the only function in this module that commits a value. It never
    mutates ``committed`` in place — it returns an updated copy.
    """
    updated = _clone_household(committed)

    if override_value is not None:
        value: Any = override_value
        prov = Provenance(source=Source.MANUAL, recorded_at=now, detail="manual entry")
    else:
        candidate = _candidate_for_source(store.candidates_for(field_key), source)
        if candidate is None:
            raise ValueError(f"No candidate for field {field_key!r} from source {source!r}")
        value = candidate.value
        prov = candidate.prov

    choices.set_choice(field_key, source, now)

    if field_key.startswith(_MAGI_PREFIX):
        year = int(field_key[len(_MAGI_PREFIX) :])
        baseline_dict = updated.prior_year_magi
        new_data = dict(baseline_dict)
        new_prov = dict(baseline_dict.prov) if isinstance(baseline_dict, SourcedDict) else {}
        new_data[year] = float(value)
        new_prov[year] = prov
        updated.prior_year_magi = SourcedDict(new_data, new_prov)
    elif field_key == GRANTS_KEY:
        value_list = list(value)
        updated.grants = SourcedList(value_list, [prov] * len(value_list))
    elif hasattr(updated, field_key):
        setattr(updated, field_key, SourcedValue(float(value), prov))
    else:
        raise ValueError(f"Unknown or unsupported field: {field_key!r}")

    return updated
