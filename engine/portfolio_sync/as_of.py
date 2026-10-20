"""Per-account "as of" dates and the newer-as-of-wins merge for the portfolio cache.

Two sources write balances for the same account: a FinExtract holdings sync and
a NetBenefits 403(b) PDF import. Each account carries the date its balances are
true as of; the rule everywhere is *newer as-of wins, a tie goes to the
incoming data*. An undated account (legacy cache) always yields to a dated one.

Pure: no Streamlit, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from models.ytd_income import iso_day

from .shapes import AccountSummary

AS_OF_LABELS: dict[str, str] = {
    "statement": "statement",
    "finextract": "FinExtract",
    "sync_time": "sync time",
    "bundle": "imported bundle",
}

# Sources a FinExtract sync does not own: an account from one of them that the
# sync no longer reports is kept, not treated as closed.
_NOT_OWNED_BY_SYNC = frozenset({"statement", "bundle"})


def as_of_text(acct: AccountSummary) -> str:
    """``2026-09-30 · statement`` (or ``unknown`` for an undated account)."""
    if not acct.as_of:
        return "unknown"
    return f"{acct.as_of} · {AS_OF_LABELS.get(acct.as_of_source, 'unknown')}"


def as_of_label(acct: AccountSummary) -> str:
    """Display text such as ``as of 2026-09-30 · statement``."""
    return f"as of {as_of_text(acct)}"


def is_newer_or_equal(incoming: AccountSummary, existing: AccountSummary) -> bool:
    """True when *incoming* may overwrite *existing*.

    An undated existing account loses to anything; an undated incoming account
    never overwrites a dated one; otherwise the later ISO day wins and a tie
    goes to the incoming data.
    """
    if not existing.as_of:
        return True
    if not incoming.as_of:
        return False
    return incoming.as_of >= existing.as_of


AccountKey = tuple[str, str, str]


def account_key(acct: AccountSummary) -> AccountKey:
    """Identity of an account across sources: (account_type, owner, account_name).

    A 403(b) is keyed by its name too, so one owner's two plans never share a
    key (issue #38). A NetBenefits plan name and a FinExtract custodian label
    for the same plan differ; :func:`_match_incoming` bridges them by
    :func:`plan_identity` instead of by key.
    """
    return (acct.account_type, acct.owner, acct.account_name)


_PLAN_NOISE = re.compile(r"403\s*\(?b\)?|\bplan\b", re.IGNORECASE)


def plan_identity(name: str) -> str:
    """Plan name reduced to its distinguishing words: ``"PLAN A 403B"`` and
    ``"Plan A 403(b) Plan"`` both become ``"a"``."""
    return " ".join(re.findall(r"[a-z0-9]+", _PLAN_NOISE.sub(" ", name.lower())))


def _statement_403b_candidates(
    existing: list[AccountSummary], inc: AccountSummary, taken: set[int]
) -> list[int]:
    return [
        j
        for j, ex in enumerate(existing)
        if j not in taken
        and ex.account_type == "403b"
        and ex.owner == inc.owner
        and ex.as_of_source == "statement"
    ]


def _match_403b_by_plan(
    existing: list[AccountSummary],
    incoming: list[AccountSummary],
    matches: list[int | None],
    taken: set[int],
) -> None:
    """Pass 2 of :func:`_match_incoming`, in place.

    A still-unmatched incoming 403(b) matches an unmatched statement-sourced
    403(b) of the same owner naming the same plan (:func:`plan_identity`).
    Failing that, the two are paired by owner alone only when that is
    unambiguous: the owner has exactly one such statement 403(b) left and the
    sync reports exactly one unmatched 403(b) for them. Otherwise nothing is
    guessed: pairing the wrong plans would overwrite one plan's balance with
    another's, while leaving both is a visible double count.
    """
    pending = [
        i for i, inc in enumerate(incoming) if matches[i] is None and inc.account_type == "403b"
    ]
    for i in pending:
        inc = incoming[i]
        same_plan = [
            j
            for j in _statement_403b_candidates(existing, inc, taken)
            if plan_identity(existing[j].account_name) == plan_identity(inc.account_name)
        ]
        if same_plan:
            matches[i] = same_plan[0]
            taken.add(same_plan[0])
    for i in pending:
        if matches[i] is not None:
            continue
        inc = incoming[i]
        cands = _statement_403b_candidates(existing, inc, taken)
        rivals = [k for k in pending if matches[k] is None and incoming[k].owner == inc.owner]
        if len(cands) == 1 and rivals == [i]:
            matches[i] = cands[0]
            taken.add(cands[0])


def _match_incoming(
    existing: list[AccountSummary], incoming: list[AccountSummary]
) -> list[int | None]:
    """For each incoming account, the index of the existing account it updates.

    Pass 1 matches by (type, owner, name), so two FinExtract 403(b)s of one
    owner stay distinct. Pass 2 (:func:`_match_403b_by_plan`) lets a
    still-unmatched incoming 403(b) match a statement-sourced 403(b) of the
    same owner, since a PDF plan name rarely equals the custodian's label.
    Each existing account is matched at most once.
    """
    matches: list[int | None] = [None] * len(incoming)
    taken: set[int] = set()
    for i, inc in enumerate(incoming):
        for j, ex in enumerate(existing):
            if j not in taken and account_key(ex) == account_key(inc):
                matches[i] = j
                taken.add(j)
                break
    _match_403b_by_plan(existing, incoming, matches, taken)
    return matches


@dataclass(frozen=True)
class MergeResult:
    """Merged accounts plus every incoming account rejected for being older."""

    accounts: list[AccountSummary]
    # (existing account that was kept, incoming account that was rejected)
    kept_newer: list[tuple[AccountSummary, AccountSummary]] = field(default_factory=list)


def merge_sync(existing: list[AccountSummary], incoming: list[AccountSummary]) -> MergeResult:
    """Merge a FinExtract sync into the cached accounts, per account.

    - matching account, incoming newer-or-equal: incoming replaces it
    - matching account, incoming older: existing kept and the rejection recorded
    - incoming with no match: added
    - existing with no incoming match: kept when PDF-sourced
      (``as_of_source == "statement"``) or imported from a data-bridge bundle
      (``"bundle"``: typically the spouse's accounts, which the operator's
      FinExtract never reports), dropped otherwise (a FinExtract-sourced or
      legacy account FinExtract no longer reports is a closed account)
    """
    matches = _match_incoming(existing, incoming)
    by_existing = {j: i for i, j in enumerate(matches) if j is not None}
    out: list[AccountSummary] = []
    kept_newer: list[tuple[AccountSummary, AccountSummary]] = []
    for j, ex in enumerate(existing):
        i = by_existing.get(j)
        if i is None:
            if ex.as_of_source in _NOT_OWNED_BY_SYNC:
                out.append(ex)
        elif is_newer_or_equal(incoming[i], ex):
            out.append(incoming[i])
        else:
            out.append(ex)
            kept_newer.append((ex, incoming[i]))
    out.extend(inc for inc, m in zip(incoming, matches, strict=True) if m is None)
    return MergeResult(accounts=out, kept_newer=kept_newer)


def force_replace(accounts: list[AccountSummary], incoming: AccountSummary) -> list[AccountSummary]:
    """Replace the cached account *incoming* was matched against ("Use anyway").

    Same matching as :func:`merge_sync`: the same-named account, else (for a
    403(b)) the owner's statement-sourced 403(b) of the same plan, or their
    only one. Other accounts are untouched.
    """
    idx = _match_incoming(accounts, [incoming])[0]
    kept = [a for j, a in enumerate(accounts) if j != idx]
    return [*kept, incoming]


def _date_of(batch: dict[str, Any]) -> str:
    """The ISO day of a batch's ``as_of`` or ``freshness.as_of`` ("" when neither parses)."""
    freshness = batch.get("freshness")
    nested = freshness.get("as_of") if isinstance(freshness, dict) else None
    for candidate in (batch.get("as_of"), nested):
        day = iso_day(candidate)
        if day:
            return day
    return ""


def extract_payload_as_of(data: dict[str, Any]) -> tuple[str, list[str]]:
    """Find the dates in a FinExtract /query response.

    Returns ``(top_level_date, per_row_dates)``. Top-level is ``as_of`` then
    ``freshness.as_of`` ("" when absent). ``per_row_dates`` runs parallel to
    ``_flatten_query_rows(data)`` for the multi-institution shape: each row gets
    its institution's own date (``institutions.<inst>.as_of``, then
    ``.freshness.as_of``), "" when that institution has none. It is empty for the
    single-institution shape, where the top-level date covers every row.
    """
    top = _date_of(data)
    institutions = data.get("institutions")
    if not isinstance(institutions, dict):
        return top, []
    per_row: list[str] = []
    for batch in institutions.values():
        if isinstance(batch, dict):
            per_row.extend([_date_of(batch)] * len(batch.get("rows", [])))
    return top, per_row
