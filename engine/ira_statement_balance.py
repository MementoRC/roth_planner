"""IRA balances from brokerage statements (Vanguard, Fidelity, IBKR).

A brokerage statement already feeds YTD income; its stated account value
additionally sets the owner's IRA balance, mirroring the NetBenefits 403(b)
flow in engine/netbenefits_statement_pdf.py: the statement becomes one
``AccountSummary`` in the portfolio snapshot (newer as-of wins) and the
owner's new pre-tax (or Roth) total is what the ``your_ira``/``spouse_ira``
(``your_roth``/``spouse_roth``) candidate carries.

The PDF-sourced account is keyed ``(type, owner, "<Broker> <label> <number>")``
-- stable per statement account, but NOT equal to the custodian label a
FinExtract sync uses, so an account already synced under its own name is not
replaced by this (it is listed for the user instead; see the review panel).

Pure: no Streamlit, no I/O.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.pdf_owner import resolve_owner
from engine.portfolio_sync.as_of import AccountKey, account_key, is_newer_or_equal
from engine.portfolio_sync.shapes import AccountSummary, PortfolioSnapshot

_BROKER_LABELS = {"vanguard": "Vanguard", "fidelity": "Fidelity", "ibkr": "IBKR"}
_TYPE_LABELS = {"traditional_ira": "Traditional IRA", "roth_ira": "Roth IRA"}
_SNAPSHOT_TYPES = {"traditional_ira": "trad_ira", "roth_ira": "roth_ira"}


def is_ira_record(rec: BrokerageStatementRecord) -> bool:
    """True for a traditional/Roth IRA statement that states its account value."""
    return rec.account_type in _SNAPSHOT_TYPES and rec.ending_value is not None


def _broker_label(rec: BrokerageStatementRecord) -> str:
    return _BROKER_LABELS.get(rec.broker, rec.broker.title())


def describe_ira_record(rec: BrokerageStatementRecord) -> str:
    """Short display label, e.g. ``Fidelity Traditional IRA …6789``."""
    return f"{_broker_label(rec)} {_TYPE_LABELS[rec.account_type]} …{rec.account_number[-4:]}"


def pick_latest_ira_records(
    records: Iterable[BrokerageStatementRecord],
) -> list[BrokerageStatementRecord]:
    """Newest IRA-eligible record per (broker, account number)."""
    latest: dict[tuple[str, str], BrokerageStatementRecord] = {}
    for rec in records:
        if not is_ira_record(rec):
            continue
        key = (rec.broker, rec.account_number)
        if key not in latest or rec.statement_period_end > latest[key].statement_period_end:
            latest[key] = rec
    return list(latest.values())


def to_ira_account_summary(rec: BrokerageStatementRecord, owner: str) -> AccountSummary:
    """The IRA ``AccountSummary`` this statement describes for *owner*.

    The statement states only a total, so the whole balance is carried as
    ``target_date`` -- the blended class, as NetBenefits does for a statement
    without usable holdings -- giving a neutral growth rate. An all-zero class
    split would make ``weighted_return`` 0.0 and drive the owner's derived IRA
    growth to 0%/yr (audit-1008 F-08).
    """
    if owner not in ("you", "spouse"):
        raise ValueError(f"IRA owner must be 'you' or 'spouse', got {owner!r}")
    if rec.ending_value is None or rec.account_type not in _SNAPSHOT_TYPES:
        raise ValueError("record is not an IRA statement with an ending value")
    return AccountSummary(
        account_type=_SNAPSHOT_TYPES[rec.account_type],
        owner=owner,
        account_name=f"{_broker_label(rec)} {_TYPE_LABELS[rec.account_type]} {rec.account_number}",
        total_value=rec.ending_value,
        target_date_value=rec.ending_value,
        as_of=rec.statement_period_end,
        as_of_source="statement",
    )


_CLASS_FIELDS = ("equity_value", "bond_value", "cash_value", "crypto_value", "target_date_value")


def default_ira_owner(
    rec: BrokerageStatementRecord, owner_map: dict[str, str], instance_owner: str
) -> str:
    """Pre-select whose IRA this is: learned holder name, then this instance's
    owner, then "you". An IRA belongs to one person, so "household" is skipped."""
    named = resolve_owner(rec.owner_key, owner_map)
    for candidate in (named, instance_owner):
        if candidate in ("you", "spouse"):
            return candidate
    return "you"


@dataclass(frozen=True)
class IraApplyOutcome:
    """What applying one IRA statement to the portfolio snapshot produces."""

    snapshot: PortfolioSnapshot
    replaced: list[AccountSummary]
    field_key: str  # "your_ira"/"spouse_ira" (traditional) or "your_roth"/"spouse_roth"
    total: float  # that owner's new pre-tax (traditional) or Roth total
    detail: str  # provenance detail for the balance candidate
    # The cached account that is newer than the statement; set (and *snapshot*
    # left unchanged) when the apply was refused. ``new_account`` is what the
    # statement would have written, for a "Use anyway" force-apply.
    kept_newer: AccountSummary | None = None
    new_account: AccountSummary | None = None


def _field_key(rec: BrokerageStatementRecord, owner: str) -> str:
    prefix = "your" if owner == "you" else "spouse"
    return f"{prefix}_{'roth' if rec.account_type == 'roth_ira' else 'ira'}"


def _owner_total(snap: PortfolioSnapshot, owner: str, *, roth: bool) -> float:
    return float(
        sum(
            a.total_value
            for a in snap.accounts
            if a.owner == owner and (a.is_roth if roth else a.is_pretax)
        )
    )


def _linked_account(
    snap: PortfolioSnapshot, rec: BrokerageStatementRecord, owner: str, replaces: AccountKey
) -> AccountSummary | None:
    """The statement's value written INTO the account on file under *replaces*.

    Keeps that account's type, owner and name (so later FinExtract syncs land
    on it). Holdings are cleared: they were true as of the sync and the
    statement only states a total. The per-class split is the account's prior
    mix scaled to the new total, so its growth rate carries over; with no prior
    mix the balance is carried as ``target_date`` (audit-1008 F-08). ``None``
    when no account carries *replaces* any more.
    """
    target = next((a for a in snap.accounts if account_key(a) == replaces), None)
    if target is None:
        return None
    pdf_acct = to_ira_account_summary(rec, owner)
    total = pdf_acct.total_value
    prior_classes = {f: getattr(target, f) for f in _CLASS_FIELDS}
    prior_sum = sum(prior_classes.values())
    if prior_sum > 0:
        classes = {f: v / prior_sum * total for f, v in prior_classes.items()}
    else:
        classes = {f: getattr(pdf_acct, f) for f in _CLASS_FIELDS}
    linked = AccountSummary(
        account_type=target.account_type,
        owner=target.owner,
        account_name=target.account_name,
        total_value=total,
        as_of=rec.statement_period_end,
        as_of_source="statement",
    )
    for name, value in classes.items():
        setattr(linked, name, value)
    return linked


def apply_ira_statement(
    snap: PortfolioSnapshot,
    rec: BrokerageStatementRecord,
    owner: str,
    *,
    force: bool = False,
    replaces: AccountKey | None = None,
) -> IraApplyOutcome:
    """Set *owner*'s account for *rec* in *snap* and total their balance.

    The account with the same identity (type, owner, name) is replaced; every
    other account is untouched. Newer as-of wins: when the cached account is
    dated after the statement, nothing is replaced and ``kept_newer`` is set,
    unless ``force`` (the user's "Use anyway"). *snap* is never modified.

    With ``replaces`` (the key of an account already on file, e.g. synced under
    the custodian's own label) the value is written into THAT account, keeping
    its name, and any account under the PDF-named key left by an earlier
    unlinked apply is removed so the IRA is counted once.
    """
    pdf_acct = to_ira_account_summary(rec, owner)
    new_acct = pdf_acct
    if replaces is not None:
        new_acct = _linked_account(snap, rec, owner, replaces) or pdf_acct
    roth = rec.account_type == "roth_ira"
    detail = f"{describe_ira_record(rec)} statement PDF, as of {rec.statement_period_end}"
    key = account_key(new_acct)
    drop = {key, account_key(pdf_acct)} if new_acct is not pdf_acct else {key}
    cached = [a for a in snap.accounts if account_key(a) == key]
    newest = max(cached, key=lambda a: a.as_of, default=None)
    if newest is not None and not force and not is_newer_or_equal(new_acct, newest):
        # Newer-wins keeps the linked account untouched, but a PDF-named
        # duplicate of this same statement (from an earlier unlinked apply) is
        # still dropped when the link points at a different, existing account,
        # so the IRA is not counted twice.
        out_snap = snap
        if new_acct is not pdf_acct and any(
            account_key(a) == account_key(pdf_acct) for a in snap.accounts
        ):
            out_snap = dataclasses.replace(
                snap, accounts=[a for a in snap.accounts if account_key(a) != account_key(pdf_acct)]
            )
        return IraApplyOutcome(
            snapshot=out_snap,
            replaced=[],
            field_key=_field_key(rec, owner),
            total=_owner_total(out_snap, owner, roth=roth),
            detail=detail,
            kept_newer=newest,
            new_account=new_acct,
        )
    kept = [a for a in snap.accounts if account_key(a) not in drop]
    removed = [a for a in snap.accounts if account_key(a) in drop]
    new_snap = dataclasses.replace(snap, accounts=[*kept, new_acct])
    return IraApplyOutcome(
        snapshot=new_snap,
        replaced=removed,
        field_key=_field_key(rec, owner),
        total=_owner_total(new_snap, owner, roth=roth),
        detail=detail,
    )
