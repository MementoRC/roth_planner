"""Per-owner PDF contribution ledger -- the source of truth for Koinly and
brokerage-statement-derived YTD figures, keyed by owner.

Fixes the override bug (docs/superpowers/specs/2026-07-13-spouse-pdf-owner-
attribution-design.md): today, scanning a Koinly report direct-assigns
crypto_stcg_ytd/crypto_ltcg_ytd/crypto_income_ytd on the snapshot, so a second
owner's report silently overwrites the first. This ledger stores one slot per
(doc_type, owner) and the snapshot value becomes SUM(slot) across owners --
one owner behaves identically to today; two owners add.

Brokerage contributions already dedup by account_number (see
engine.brokerage_statement_pdf.pick_latest_per_account); this ledger adds the
owner dimension on top so two owners' distinct accounts both survive a
re-scan, while re-scanning the SAME owner's SAME account still replaces (not
duplicates) that slot -- identical to today's pick_latest_per_account
semantics, just owner-scoped.

Pure functions + a small JSON cache. No Streamlit import (engine/ purity rule).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.koinly_report_pdf import KoinlyReport
from engine.secure_io import read_pii_json, write_pii_json
from models.ytd_income import YTDSnapshot, iso_day

# Ledger shape:
# {
#   "koinly": {"<owner>": {"stcg": float, "ltcg": float, "income": float,
#                            "interest": float,  # lending-interest part of income
#                            "captured_at": str, "source": str,
#                            "parser_version": str, "provenance": dict,
#                            "owner_key": str | None}},
#   "brokerage": {"<owner>": {"<account_number>": {...record.to_dict()...}}},
# }
# A second broker's account reusing a number already held by the same owner is
# keyed "<broker>:<account_number>" (see _slot_key_for); readers should take
# the account number from the record dict, not the key.
PdfLedger = dict[str, dict[str, Any]]

_EMPTY_LEDGER: PdfLedger = {"koinly": {}, "brokerage": {}}


def write_koinly_contribution(ledger: PdfLedger, owner: str, report: KoinlyReport) -> PdfLedger:
    """Return a NEW ledger with *owner*'s Koinly slot set to *report*'s
    figures, pruning that SAME report from any OTHER owner slot it was
    previously (mis)filed under.

    Re-writing the same owner with the SAME or a NEWER tax_year replaces that
    owner's prior contribution (idempotent re-scan); a report from an OLDER
    tax_year than what's already stored is skipped (C15 audit-0721) -- a
    multi-year folder scan must keep the latest tax_year's figures rather
    than silently collapsing to whichever Koinly PDF happens to be processed
    last. A different owner's DISTINCT report is untouched (additive across
    owners, the module's core purpose).

    Koinly reports carry no account number (unlike brokerage statements), so
    cross-owner report identity is inferred from content: tax_year, source,
    parser_version, the parsed stcg/ltcg/income figures, provenance, and
    owner_key -- everything a re-scan of the IDENTICAL PDF reproduces
    byte-for-byte. captured_at is deliberately excluded from the identity
    check: it is the one field guaranteed to differ between scans of the same
    file, so comparing it would make every re-scan look like a "different"
    report and defeat the prune entirely. Two owners' genuinely distinct
    reports would have to collide on stcg, ltcg, income, AND provenance
    (which includes a page count) to be misidentified as the same report --
    a coincidence real transaction data does not produce.

    Without this, an owner-resolution change (e.g. a scan run before
    instance identity was set lands under the "household" fallback, then a
    later re-scan after identity is set writes the identical report under
    "you") leaves BOTH slots behind, and derive_koinly_totals sums every
    owner bucket -- doubling crypto_stcg_ytd/crypto_ltcg_ytd/
    crypto_income_ytd. That OVERSTATES income, which UNDERSTATES remaining
    Roth conversion headroom -- a silent cost, not an obviously wrong number.
    """
    updated: PdfLedger = {
        "koinly": dict(ledger.get("koinly", {})),
        "brokerage": dict(ledger.get("brokerage", {})),
    }
    existing = updated["koinly"].get(owner)
    if existing is not None and int(existing.get("tax_year", report.tax_year)) > report.tax_year:
        return updated
    new_slot = {
        "tax_year": report.tax_year,
        "stcg": float(report.crypto_stcg),
        "ltcg": float(report.crypto_ltcg),
        "income": float(report.crypto_income),
        "interest": _lending_interest(report.provenance),
        "captured_at": report.captured_at,
        "source": report.source,
        "parser_version": report.parser_version,
        "provenance": report.provenance,
        "owner_key": report.owner_key,
    }

    def _same_report(slot: dict[str, Any]) -> bool:
        return (
            slot.get("tax_year") == new_slot["tax_year"]
            and slot.get("source") == new_slot["source"]
            and slot.get("parser_version") == new_slot["parser_version"]
            and slot.get("stcg") == new_slot["stcg"]
            and slot.get("ltcg") == new_slot["ltcg"]
            and slot.get("income") == new_slot["income"]
            and slot.get("provenance") == new_slot["provenance"]
            and slot.get("owner_key") == new_slot["owner_key"]
        )

    # Prune the same report from every OTHER owner so a stale owner
    # assignment (e.g. the "household" fallback later corrected to a named
    # owner) MOVES the slot rather than leaving a duplicate that
    # derive_koinly_totals would sum a second time.
    for other_owner, slot in list(updated["koinly"].items()):
        if other_owner != owner and _same_report(slot):
            del updated["koinly"][other_owner]

    updated["koinly"][owner] = new_slot
    return updated


def write_brokerage_contribution(
    ledger: PdfLedger, owner: str, record: BrokerageStatementRecord
) -> PdfLedger:
    """Return a NEW ledger with *record* written into *owner*'s brokerage
    slot, keyed by account_number (broker-qualified on a number collision).

    Account identity is (broker, account_number), not account_number alone.
    Re-writing the same account under the SAME owner replaces that slot only
    when *record*'s statement_period_end is the same or newer than the
    stored slot's (mirrors pick_latest_per_account's comparison, C14
    audit-0721) -- an out-of-order scan (e.g. Dec statement processed after
    Jan) leaves the newer stored record untouched.

    The same account arriving under a DIFFERENT owner MOVES the slot instead
    of duplicating it: resolve_account_owner falls back to instance_owner,
    which the user can change at Setup > Command Center, so a re-scan can
    legitimately land the same account under a new owner (audit-0823 C2).
    The staleness comparison above is therefore evaluated cross-owner (every
    owner's copy of this account, not just the target owner's), so an older
    statement arriving under a new owner cannot clobber a newer one already
    stored under the old owner. A genuinely different account_number (or the
    same number at a different broker) remains a separate, additive slot.
    """
    updated: PdfLedger = {
        "koinly": dict(ledger.get("koinly", {})),
        "brokerage": {k: dict(v) for k, v in ledger.get("brokerage", {}).items()},
    }

    # Staleness guard, evaluated across EVERY owner rather than just *owner*:
    # resolve_account_owner falls back to instance_owner, which the user can
    # change at Setup > Command Center, so the same account can arrive under a
    # different owner on a later scan (audit-0823 C2).
    for accounts in updated["brokerage"].values():
        key = _slot_key_for(accounts, record)
        if (
            key is not None
            and str(accounts[key].get("statement_period_end", "")) > record.statement_period_end
        ):
            return updated

    # Prune the same account from every OTHER owner so an owner change MOVES
    # the slot rather than leaving a duplicate that derive_brokerage_totals
    # would sum a second time.
    for other_owner, accounts in updated["brokerage"].items():
        if other_owner == owner:
            continue
        key = _slot_key_for(accounts, record)
        if key is not None:
            del accounts[key]

    owner_accounts = dict(updated["brokerage"].get(owner, {}))
    key = _slot_key_for(owner_accounts, record)
    if key is None:
        # Plain account_number when free; a broker-qualified key only when
        # another broker's account already holds that number (audit-1008 F-55).
        key = record.account_number
        if key in owner_accounts:
            key = f"{record.broker}:{record.account_number}"
    owner_accounts[key] = record.to_dict()
    updated["brokerage"][owner] = owner_accounts
    return updated


def _slot_key_for(accounts: dict[str, Any], record: BrokerageStatementRecord) -> str | None:
    """Key of *record*'s account among one owner's brokerage slots, or None.

    Account identity is (broker, account_number), matching how
    account_overrides is keyed (engine.account_attribution) -- two brokers
    reusing a number string are two distinct accounts (audit-1008 F-55). The
    slot key is normally the bare account_number; a second broker's account
    with the same number is stored under ``"<broker>:<account_number>"``.
    """
    for key, rec in accounts.items():
        if not isinstance(rec, dict):
            continue
        number = str(rec.get("account_number", key))
        if number == record.account_number and str(rec.get("broker", "")) == record.broker:
            return key
    return None


def _record_year(rec: dict[str, Any]) -> int | None:
    """Tax year a brokerage slot reports (its statement_period_end year)."""
    day = iso_day(rec.get("statement_period_end"))
    return int(day[:4]) if day else None


def _in_year(year: int | None, tax_year: int | None) -> bool:
    """True when a slot counts toward *tax_year* (None = no gate). A slot
    whose year cannot be read is kept, as before the gate existed."""
    return tax_year is None or year is None or year == tax_year


def _koinly_year(slot: dict[str, Any]) -> int | None:
    try:
        return int(slot["tax_year"])
    except (KeyError, TypeError, ValueError):
        return None


def statement_in_tax_year(rec: BrokerageStatementRecord, tax_year: int | None) -> bool:
    """True when *rec*'s figures count toward *tax_year* (the same gate the
    year-gated totals use; a statement whose period cannot be read is kept)."""
    return _in_year(_record_year({"statement_period_end": rec.statement_period_end}), tax_year)


def off_year_contributions(
    ledger: PdfLedger,
    tax_year: int,
    *,
    koinly: bool = True,
    brokerage: bool = True,
) -> list[str]:
    """Plain-language notes for ledgered documents NOT counted in *tax_year*.

    Statement YTD columns and Koinly reports cover one tax year. A document
    from another year stays in the ledger (it is still right for its own
    year) but is skipped by the *tax_year*-gated totals (audit-1008 F-10);
    these notes tell the user why it was not applied. *koinly* / *brokerage*
    select which document kinds are described.
    """
    notes: list[str] = []
    for owner, slot in sorted(ledger.get("koinly", {}).items() if koinly else ()):
        if not isinstance(slot, dict):
            continue
        year = _koinly_year(slot)
        if not _in_year(year, tax_year):
            notes.append(
                f"Koinly report for {year} ({owner}) was not counted: "
                f"the YTD snapshot is for {tax_year}."
            )
    for owner, accounts in sorted(ledger.get("brokerage", {}).items() if brokerage else ()):
        for rec in accounts.values():
            if not isinstance(rec, dict):
                continue
            year = _record_year(rec)
            if not _in_year(year, tax_year):
                notes.append(
                    _statement_off_year_text(
                        str(rec.get("broker", "")),
                        str(rec.get("account_number", "?")),
                        owner,
                        rec.get("statement_period_end"),
                        year,
                        tax_year,
                    )
                )
    return notes


def _statement_off_year_text(
    broker: str,
    account_number: str,
    owner: str,
    period_end: object,
    year: int | None,
    tax_year: int,
) -> str:
    return (
        f"{broker.title()} statement for account {account_number} ({owner}) ends "
        f"{period_end}, so its {year} figures were not counted: "
        f"the YTD snapshot is for {tax_year}."
    )


def off_year_statement_note(rec: BrokerageStatementRecord, owner: str, tax_year: int) -> str | None:
    """The off-year note for one parsed statement, or None when it counts.

    Same wording as :func:`off_year_contributions`, for a record that is not
    (yet) in the ledger -- e.g. one held for the user's confirmation.
    """
    if statement_in_tax_year(rec, tax_year):
        return None
    year = _record_year({"statement_period_end": rec.statement_period_end})
    return _statement_off_year_text(
        rec.broker, rec.account_number, owner, rec.statement_period_end, year, tax_year
    )


def derive_koinly_totals(ledger: PdfLedger, tax_year: int | None = None) -> dict[str, float]:
    """Sum Koinly stcg/ltcg/income across every owner slot in the ledger.

    Empty ledger -> all zeros. Single owner -> identical to that owner's raw
    report values (non-regression). Multiple owners -> summed (the fix).
    With *tax_year*, a report for any other year is skipped (audit-1008 F-10).
    """
    by_owner = {
        k: v
        for k, v in ledger.get("koinly", {}).items()
        if isinstance(v, dict) and _in_year(_koinly_year(v), tax_year)
    }
    return {
        "stcg": sum(float(v.get("stcg", 0.0)) for v in by_owner.values()),
        "ltcg": sum(float(v.get("ltcg", 0.0)) for v in by_owner.values()),
        "income": sum(float(v.get("income", 0.0)) for v in by_owner.values()),
    }


def _lending_interest(provenance: Any) -> float:
    """The Koinly "Lending interest" income row, from a report's provenance."""
    if not isinstance(provenance, dict):
        return 0.0
    by_category = provenance.get("income_by_category")
    if not isinstance(by_category, dict):
        return 0.0
    try:
        return float(by_category.get("Lending interest", 0.0))
    except (TypeError, ValueError):
        return 0.0


def derive_koinly_interest(ledger: PdfLedger, tax_year: int | None = None) -> float:
    """Sum the Koinly "Lending interest" part of crypto income across owners.

    Lending interest is interest, so it is net investment income under IRC
    §1411(c)(1)(A)(i), unlike staking rewards and airdrops (audit-1008 F-54).
    It is already inside each slot's ``income``; this is only the NII part.
    Slots written before the ``interest`` key existed fall back to the
    report's stored per-category provenance. Same *tax_year* gate as
    derive_koinly_totals.
    """
    total = 0.0
    for slot in ledger.get("koinly", {}).values():
        if not isinstance(slot, dict) or not _in_year(_koinly_year(slot), tax_year):
            continue
        if "interest" in slot:
            total += float(slot.get("interest") or 0.0)
        else:
            total += _lending_interest(slot.get("provenance"))
    return total


def latest_brokerage_period_end(ledger: PdfLedger, tax_year: int | None = None) -> str | None:
    """Newest ``statement_period_end`` across every ledgered brokerage record.

    The as-of of brokerage-derived YTD totals for the manual-entry supersede
    rule (redesign PR 1b). The newest, not the oldest, statement is used so
    one stale account cannot pin hand entries in place forever. Unparsable
    dates are ignored. With *tax_year*, only that year's statements count --
    the same ones derive_brokerage_totals sums for it.
    """
    dates = [
        iso_day(rec.get("statement_period_end"))
        for accounts in ledger.get("brokerage", {}).values()
        for rec in accounts.values()
        if isinstance(rec, dict) and _in_year(_record_year(rec), tax_year)
    ]
    return max((d for d in dates if d), default=None)


def latest_koinly_capture(ledger: PdfLedger, tax_year: int | None = None) -> str | None:
    """Newest Koinly report capture date: the best as-of a Koinly PDF offers.
    With *tax_year*, only reports for that year count."""
    dates = [
        iso_day(v.get("captured_at"))
        for v in ledger.get("koinly", {}).values()
        if isinstance(v, dict) and _in_year(_koinly_year(v), tax_year)
    ]
    return max((d for d in dates if d), default=None)


def apply_koinly_totals(ytd: YTDSnapshot, ledger: PdfLedger) -> tuple[str, ...]:
    """Land Koinly crypto totals on *ytd* IN PLACE, then settle manual marks.

    The one place the Koinly -> YTD mapping lives (it was duplicated in
    views/ytd_income/_partials/_sync_scan.py and views/setup/data_bridge.py).
    Only reports for ``ytd.tax_year`` count (audit-1008 F-10).
    Returns the hand-entered keys kept because they are newer than the reports.
    """
    totals = derive_koinly_totals(ledger, tax_year=ytd.tax_year)
    ytd.crypto_stcg_ytd = totals["stcg"]
    ytd.crypto_ltcg_ytd = totals["ltcg"]
    ytd.crypto_income_ytd = totals["income"]
    ytd.crypto_interest_ytd = derive_koinly_interest(ledger, tax_year=ytd.tax_year)
    return ytd.settle_manual_marks(as_of=latest_koinly_capture(ledger, tax_year=ytd.tax_year))


def derive_brokerage_totals(ledger: PdfLedger, tax_year: int | None = None) -> dict[str, float]:
    """Sum brokerage-derived YTD fields across every (owner, account_number)
    slot in the ledger, using the same field mapping as
    engine.brokerage_statement_pdf.aggregate_to_ytd_fields.

    Deliberately re-implemented here (not delegating to
    aggregate_to_ytd_fields) because that function takes a flat
    dict[account_number, record]; the ledger is owner-scoped
    dict[owner, dict[account_number, record_dict]], so the flattening step
    (across owners) belongs in this module.

    WARNING: the returned "ordinary_dividends_ytd" key holds the FULL taxable
    dividend total (Form 1040 line 3b in substance), NOT the non-qualified
    remainder that YTDSnapshot.ordinary_dividends_ytd expects. Callers must
    land this dict via engine.portfolio_sync.ytd.apply_brokerage_totals,
    which subtracts qualified_dividends_ytd -- assigning it directly
    double-counts qualified dividends (audit-0823 C1).

    With *tax_year*, a statement whose period ends in another year is skipped:
    its YTD columns are that year's figures, not this one's (audit-1008 F-10).
    Pass the YTD snapshot's tax_year at every call site that lands totals.
    """
    totals = {
        "interest_ytd": 0.0,
        "tax_exempt_interest_ytd": 0.0,
        "ordinary_dividends_ytd": 0.0,
        "stcg_ytd": 0.0,
        "ltcg_ytd": 0.0,
    }
    for owner_accounts in ledger.get("brokerage", {}).values():
        for rec_dict in owner_accounts.values():
            if not _in_year(_record_year(rec_dict), tax_year):
                continue
            totals["interest_ytd"] += float(rec_dict.get("interest_taxable_ytd", 0.0))
            totals["tax_exempt_interest_ytd"] += float(
                rec_dict.get("interest_tax_exempt_ytd", 0.0)
            ) + float(rec_dict.get("dividends_tax_exempt_ytd", 0.0))
            totals["ordinary_dividends_ytd"] += float(rec_dict.get("dividends_taxable_ytd", 0.0))
            totals["stcg_ytd"] += float(rec_dict.get("stcg_net_ytd", 0.0))
            totals["ltcg_ytd"] += float(rec_dict.get("ltcg_net_ytd", 0.0))
    return totals


def extract_owner(ledger: PdfLedger, owner: str) -> dict:
    """Return the exporter's owner-agnostic ledger slice: the inner values under `owner`."""
    koinly = copy.deepcopy(ledger.get("koinly", {}).get(owner, {}))
    brokerage = copy.deepcopy(ledger.get("brokerage", {}).get(owner, {}))
    return {"koinly": koinly, "brokerage": brokerage}


def replace_owner(ledger: PdfLedger, owner: str, slice_: dict) -> PdfLedger:
    """Return a new ledger with `owner`'s koinly/brokerage entries replaced by `slice_`.
    An empty section in `slice_` removes that owner from that section (full reset)."""
    out = copy.deepcopy(ledger)
    out.setdefault("koinly", {})
    out.setdefault("brokerage", {})
    for section in ("koinly", "brokerage"):
        payload = slice_.get(section) or {}
        if payload:
            out[section][owner] = copy.deepcopy(payload)
        else:
            out[section].pop(owner, None)
    return out


# ---------------------------------------------------------------------------
# JSON cache
# ---------------------------------------------------------------------------

_LEDGER_PATH = Path(__file__).resolve().parent.parent / ".pdf_import_ledger.json"


def save_ledger(ledger: PdfLedger) -> None:
    write_pii_json(_LEDGER_PATH, ledger)


def load_ledger() -> PdfLedger:
    """Load the ledger, defaulting missing doc-type keys to {} for forward
    compatibility with partially-migrated or hand-edited cache files (mirrors
    the field-default pattern in engine.portfolio_sync.ytd.load_ytd_snapshot
    and engine.tax_return_pdf.load_pdf_tax_records)."""
    if not _LEDGER_PATH.exists():
        return dict(_EMPTY_LEDGER)
    try:
        raw = read_pii_json(_LEDGER_PATH)
    except (json.JSONDecodeError, OSError):
        return dict(_EMPTY_LEDGER)
    if not isinstance(raw, dict):
        return dict(_EMPTY_LEDGER)
    return {
        "koinly": dict(raw.get("koinly", {})),
        "brokerage": {k: dict(v) for k, v in raw.get("brokerage", {}).items()},
    }
