"""Link an IRA statement to the snapshot account already on file for it.

A FinExtract sync names an account its own way (``Rollover IRA<digits>``, a raw
IBKR id, a long Vanguard registration string), so a statement-sourced account
keyed ``<Broker> <label> <number>`` would count the same IRA twice. The user
picks which account a statement replaces; the choice is remembered per
statement identity (``"<broker>|<account number>"``) in the document store
(namespace ``ira_account_links``), like the PDF owner map (engine/pdf_owner.py).

Pure functions + one document-store row, no Streamlit.
"""

from __future__ import annotations

import re
from typing import Literal

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.ira_statement_balance import to_ira_account_summary
from engine.portfolio_sync.as_of import AccountKey, account_key
from engine.portfolio_sync.shapes import AccountSummary, PortfolioSnapshot
from engine.store import CorruptStoreError, get_store
from engine.store.namespaces import IRA_ACCOUNT_LINKS

NEW: Literal["NEW"] = "NEW"  # remembered choice: "a separate account"

LinkChoice = AccountKey | Literal["NEW"]
LinkMap = dict[str, LinkChoice]

_SNAPSHOT_TYPES = {"traditional_ira": "trad_ira", "roth_ira": "roth_ira"}
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_MIN_ID_LEN = 4  # shorter ids would match too much by substring


def statement_identity(rec: BrokerageStatementRecord) -> str:
    return f"{rec.broker}|{rec.account_number}"


def _squash(text: str) -> str:
    return _NON_ALNUM_RE.sub("", text.lower())


def link_candidates(
    rec: BrokerageStatementRecord, snap: PortfolioSnapshot, owner: str, link_map: LinkMap
) -> list[AccountSummary]:
    """Accounts of the statement's type and *owner* this statement may replace.

    Excluded: the PDF-named account for this statement, any account that is
    another statement's linked target, and any statement-sourced account
    (``as_of_source == "statement"``; synced ones carry ``finextract`` /
    ``sync_time`` / ``""``) unless it is this statement's remembered target.
    Offering another custodian's account would let one click delete it.
    """
    snap_type = _SNAPSHOT_TYPES.get(rec.account_type)
    pdf_key = account_key(to_ira_account_summary(rec, owner))
    me = statement_identity(rec)
    mine = link_map.get(me)
    others_targets = {v for k, v in link_map.items() if k != me and v != NEW}
    return [
        a
        for a in snap.accounts
        if a.account_type == snap_type
        and a.owner == owner
        and account_key(a) != pdf_key
        and account_key(a) not in others_targets
        and (a.as_of_source != "statement" or account_key(a) == mine)
    ]


def _only(hits: list[AccountSummary]) -> AccountKey | None:
    return account_key(hits[0]) if len(hits) == 1 else None


def suggest_link(
    rec: BrokerageStatementRecord, candidates: list[AccountSummary], link_map: LinkMap
) -> LinkChoice | None:
    """The pre-selected choice for *rec*, or ``None`` when the user must choose.

    Order: a remembered choice (``NEW``, or a target still among the
    candidates); exactly one candidate whose name holds the full account
    number; exactly one whose name holds the number's last 4 and the broker
    token. Several hits never pick one.
    """
    keys = {account_key(a) for a in candidates}
    remembered = link_map.get(statement_identity(rec))
    if remembered == NEW or (remembered is not None and remembered in keys):
        return remembered
    number = _squash(rec.account_number)
    full = [
        a for a in candidates if len(number) >= _MIN_ID_LEN and number in _squash(a.account_name)
    ]
    if full:
        return _only(full)
    broker = _squash(rec.broker)
    last4 = number[-_MIN_ID_LEN:]
    if len(number) < _MIN_ID_LEN:
        return None
    both = [
        a
        for a in candidates
        if last4 in _squash(a.account_name) and broker in _squash(a.account_name)
    ]
    return _only(both)


def remember_link(rec: BrokerageStatementRecord, choice: LinkChoice, link_map: LinkMap) -> LinkMap:
    """A NEW map with *rec*'s choice written in (no mutation)."""
    updated = dict(link_map)
    updated[statement_identity(rec)] = choice
    return updated


# ---------------------------------------------------------------------------
# Statement identity -> linked account key (or "NEW"),
# document store namespace ``ira_account_links``
# ---------------------------------------------------------------------------


def save_link_map(link_map: LinkMap) -> None:
    get_store().put(
        IRA_ACCOUNT_LINKS,
        {k: (v if isinstance(v, str) else list(v)) for k, v in link_map.items()},
    )


def load_link_map() -> LinkMap:
    try:
        raw = get_store().get(IRA_ACCOUNT_LINKS)
    except CorruptStoreError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: LinkMap = {}
    for ident, val in raw.items():
        if val == NEW:
            out[str(ident)] = NEW
        elif isinstance(val, list) and len(val) == 3 and all(isinstance(p, str) for p in val):
            out[str(ident)] = (val[0], val[1], val[2])
    return out
