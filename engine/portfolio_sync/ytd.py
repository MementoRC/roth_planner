"""YTD income snapshot fetch/parse/cache."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import TYPE_CHECKING

import requests  # type: ignore[import-untyped]

from engine.brokerage_statement_pdf import BrokerageStatementRecord, aggregate_to_ytd_fields
from engine.secure_io import read_pii_json, write_pii_json
from models.ytd_income import (
    OWNED_YTD_FIELDS,
    YTD_OWNERS,
    IncomeEvent,
    ManualMark,
    RealizedGainEvent,
    YTDSnapshot,
    manual_mark_from_dict,
    split_manual_key,
)

if TYPE_CHECKING:
    pass

from .client import _get
from .shapes import known_fields


def fetch_ytd_snapshot() -> YTDSnapshot:
    """Fetch year-to-date income data from FinExtract.

    Only checks server availability (/status) to report sync freshness in the
    UI. Returns an empty snapshot if FinExtract is unavailable (the UI then
    falls back to manual entry).

    Investment income (interest, dividends, tax-exempt split, realized gains)
    is sourced exclusively from brokerage statement PDFs now, via
    apply_brokerage_statement_records — NOT from FinExtract. FinExtract's
    investment_income/realized_gains endpoints were retired from this function
    because they structurally cannot distinguish tax-exempt income from
    taxable income, or separate taxable accounts from Roth/IRA accounts
    (confirmed against the live FinExtract server 2026-07-10) — see
    docs/superpowers/plans/2026-07-10-brokerage-statement-pdf-ytd.md.

    wages_ytd, nec_income_ytd, qualified_dividends_ytd, ira_conversions_ytd,
    spouse_ira_conversions_ytd, and ira_distributions_ytd remain
    manual-entry-only (see models.ytd_income.IncomeEvent / views/ytd_income.py).
    """
    ytd = YTDSnapshot()

    try:
        resp = _get("/status", timeout=3)
        resp.raise_for_status()
        ytd.manually_entered = False
        # Only stamp snapshot_date when the ping actually succeeded (audit-0805
        # C32) -- calling this unconditionally made every caller's
        # `if ytd.snapshot_date:` "was this sync actually reachable?" check
        # always true, even when FinExtract was down.
        ytd.with_snapshot_date()
    except requests.RequestException:
        pass

    return ytd


def apply_brokerage_totals(ytd: YTDSnapshot, totals: dict[str, float]) -> YTDSnapshot:
    """Land derived brokerage totals onto *ytd*. THE one place that mapping lives.

    ``totals["ordinary_dividends_ytd"]`` is the statement's FULL taxable
    dividend total -- Form 1040 line 3b in substance. No brokerage statement
    splits qualified from ordinary (see engine.brokerage_statement_pdf module
    docstring: verified absent for Schwab, Vanguard, IBKR and UBS).

    ``YTDSnapshot.ordinary_dividends_ytd`` means something different: the
    NON-qualified remainder. ``dividends_ytd`` returns qualified + ordinary,
    and both ``magi_ytd`` and ``total_investment_income`` consume that sum.
    Assigning the full statement total straight across therefore counted the
    user's separately-entered qualified dividends twice, inflating MAGI
    (IRMAA/ACA thresholds), the NIIT base and ordinary brackets at once
    (audit-0823 C1).

    Subtracting here -- in ONE place every caller routes through -- is what
    keeps that from being re-introduced at the next call site. Floored at zero
    so a stale qualified entry larger than the scanned total cannot drive
    ordinary dividends negative. Idempotent: it reads qualified_dividends_ytd
    and *totals*, never the previous ordinary value, so re-deriving is safe.

    NOTE: editing qualified_dividends_ytd afterwards does NOT retroactively
    re-split an already-applied total; re-scan or re-derive to refresh it.
    """
    ytd.interest_ytd = totals["interest_ytd"]
    ytd.tax_exempt_interest_ytd = totals["tax_exempt_interest_ytd"]
    ytd.ordinary_dividends_ytd = max(
        0.0, totals["ordinary_dividends_ytd"] - ytd.qualified_dividends_ytd
    )
    ytd.stcg_ytd = totals["stcg_ytd"]
    ytd.ltcg_ytd = totals["ltcg_ytd"]
    return ytd


def apply_brokerage_statement_records(
    ytd: YTDSnapshot, taxable_by_account: dict[str, BrokerageStatementRecord]
) -> YTDSnapshot:
    """Overlay statement-derived interest/dividend/gain YTD figures onto *ytd*.

    *taxable_by_account* MUST already be filtered to account_type == "taxable"
    (via engine.brokerage_statement_pdf.partition_by_account_type) — this
    function does not re-check, so Roth/IRA/unknown accounts must never reach
    here. Fields not covered by brokerage statements (wages, NEC income, IRA
    conversions/distributions, qualified dividends) are left untouched,
    mirroring apply_option_exercises' overlay pattern.

    The qualified-dividend subtraction (statement total -> non-qualified
    remainder) happens inside apply_brokerage_totals, not here.
    """
    return apply_brokerage_totals(ytd, aggregate_to_ytd_fields(taxable_by_account))


_YTD_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / ".ytd_cache.json"


def ytd_to_dict(ytd: YTDSnapshot) -> dict:
    """Serialize a YTDSnapshot to its explicit JSON-able dict form.

    Extracted from save_ytd_snapshot (audit-0823: "YTD in the data-bridge
    bundle") so both the on-disk cache writer AND the data-bridge bundle
    builder share one serialization -- otherwise the bundle would silently
    drift from the cache format the first time either one gained a field.
    """
    data = {
        "tax_year": ytd.tax_year,
        "snapshot_date": ytd.snapshot_date,
        "wages_ytd": ytd.wages_ytd,
        "nec_income_ytd": ytd.nec_income_ytd,
        "ira_conversions_ytd": ytd.ira_conversions_ytd,
        "spouse_ira_conversions_ytd": ytd.spouse_ira_conversions_ytd,
        "ira_distributions_ytd": ytd.ira_distributions_ytd,
        "ltcg_ytd": ytd.ltcg_ytd,
        "stcg_ytd": ytd.stcg_ytd,
        "qualified_dividends_ytd": ytd.qualified_dividends_ytd,
        "ordinary_dividends_ytd": ytd.ordinary_dividends_ytd,
        "interest_ytd": ytd.interest_ytd,
        "tax_exempt_interest_ytd": ytd.tax_exempt_interest_ytd,
        "nqo_exercise_ytd": ytd.nqo_exercise_ytd,
        "federal_withholding_ytd": ytd.federal_withholding_ytd,
        "estimated_payments_ytd": ytd.estimated_payments_ytd,
        "hsa_contribution_ytd": ytd.hsa_contribution_ytd,
        "deductible_ira_contribution_ytd": ytd.deductible_ira_contribution_ytd,
        "crypto_stcg_ytd": ytd.crypto_stcg_ytd,
        "crypto_ltcg_ytd": ytd.crypto_ltcg_ytd,
        "crypto_income_ytd": ytd.crypto_income_ytd,
        "crypto_interest_ytd": ytd.crypto_interest_ytd,
        "gain_events": [asdict(e) for e in ytd.gain_events],
        "income_events": [asdict(e) for e in ytd.income_events],
        "manually_entered": ytd.manually_entered,
    }
    # Issue #496: emitted only when attributed, so an unattributed cache
    # stays byte-identical to the pre-#496 format.
    if ytd.by_owner:
        data["by_owner"] = {owner: dict(slot) for owner, slot in ytd.by_owner.items()}
    # Redesign PR 1b: emitted only when present, so a mark-free cache stays
    # byte-identical to the pre-1b format.
    if ytd.manual_marks:
        data["manual_marks"] = {key: asdict(mark) for key, mark in sorted(ytd.manual_marks.items())}
    # Redesign PR 1b: the UBS withholding component, emitted only when present.
    if ytd.ubs_withholding_by_owner:
        data["ubs_withholding_by_owner"] = dict(sorted(ytd.ubs_withholding_by_owner.items()))
    return data


def _sanitize_by_owner(raw: object) -> dict[str, dict[str, float]]:
    """Keep only well-formed per-owner YTD entries; drop the rest silently.

    Same forward-compat rule as the unknown-field filter in ytd_from_dict:
    one malformed entry from a newer/buggy peer must not take the whole YTD
    section down with it.
    """
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, dict[str, float]] = {}
    for owner, slot in raw.items():
        if owner not in YTD_OWNERS or not isinstance(slot, dict):
            continue
        entry: dict[str, float] = {}
        for name, value in slot.items():
            if name not in OWNED_YTD_FIELDS or isinstance(value, bool):
                continue
            try:
                entry[name] = float(value)
            except (TypeError, ValueError):
                continue
        if entry:
            clean[owner] = entry
    return clean


def _sanitize_manual_marks(raw: object) -> dict[str, ManualMark]:
    """Keep only well-formed provenance entries; drop the rest silently."""
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, ManualMark] = {}
    for key, entry in raw.items():
        mark = manual_mark_from_dict(entry)
        if mark is None or not isinstance(key, str):
            continue
        try:
            split_manual_key(key)
        except ValueError:
            continue
        clean[key] = mark
    return clean


def _sanitize_ubs_withholding(raw: object) -> dict[str, float]:
    """Keep only {owner: number} entries for known owners; drop the rest silently."""
    if not isinstance(raw, dict):
        return {}
    return {
        owner: float(value)
        for owner, value in raw.items()
        if owner in YTD_OWNERS and isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def ytd_from_dict(data: dict) -> YTDSnapshot:
    """Parse a YTDSnapshot from its JSON dict form, applying legacy migrations.

    Extracted from load_ytd_snapshot (audit-0823) so the data-bridge bundle
    reader can reuse the SAME migration logic -- a bundle built by an older
    peer (pre-nqo_exercise_ytd, pre-dividends_ytd-split, etc.) must still
    load correctly here, exactly as an old on-disk cache file would.
    Pure: operates on a copy, never mutates the caller's dict.
    """
    data = dict(data)
    # Unknown keys inside an event are dropped like unknown top-level keys
    # below, so one new event field from a newer peer cannot lose (or crash)
    # the whole YTD section (audit-1008 F-57).
    events = [
        RealizedGainEvent(**known_fields(RealizedGainEvent, e)) for e in data.pop("gain_events", [])
    ]
    income_events = [
        IncomeEvent(**known_fields(IncomeEvent, e)) for e in data.pop("income_events", [])
    ]
    # Issue #496: absent -> {} (legacy, unattributed; no migration on load).
    by_owner = _sanitize_by_owner(data.pop("by_owner", {}))
    manual_marks = _sanitize_manual_marks(data.pop("manual_marks", {}))
    ubs_withholding = _sanitize_ubs_withholding(data.pop("ubs_withholding_by_owner", {}))
    if not by_owner:
        ubs_withholding = {}  # a component needs an attributed snapshot; never fatal
    # Migrate old cache files that stored a single dividends_ytd key.
    if "dividends_ytd" in data and "ordinary_dividends_ytd" not in data:
        data["ordinary_dividends_ytd"] = data.pop("dividends_ytd")
    else:
        data.pop("dividends_ytd", None)
    # Migrate pre-PR1 caches that lack nqo_exercise_ytd.
    if "nqo_exercise_ytd" not in data:
        data["nqo_exercise_ytd"] = 0.0
    # PU1-M01: migrate old caches that predate federal_withholding_ytd field.
    if "federal_withholding_ytd" not in data:
        data["federal_withholding_ytd"] = 0.0
    # Migrate old caches that predate estimated_payments_ytd (Form 1040-ES).
    if "estimated_payments_ytd" not in data:
        data["estimated_payments_ytd"] = 0.0
    # Migrate old caches that predate the above-the-line HSA/IRA adjustment fields.
    if "hsa_contribution_ytd" not in data:
        data["hsa_contribution_ytd"] = 0.0
    if "deductible_ira_contribution_ytd" not in data:
        data["deductible_ira_contribution_ytd"] = 0.0
    # Migrate old caches that predate the crypto (Koinly-sourced) fields.
    if "crypto_stcg_ytd" not in data:
        data["crypto_stcg_ytd"] = 0.0
    if "crypto_ltcg_ytd" not in data:
        data["crypto_ltcg_ytd"] = 0.0
    if "crypto_income_ytd" not in data:
        data["crypto_income_ytd"] = 0.0
    # audit-1008 F-54: the lending-interest (NII) part of crypto income.
    if "crypto_interest_ytd" not in data:
        data["crypto_interest_ytd"] = 0.0
    # Forward compatibility: a bundle/cache built by a NEWER peer can carry a
    # field this build doesn't know about yet (the mirror image of the
    # backward-compat migrations above, which handle keys going MISSING).
    # YTDSnapshot(**data, ...) below raises TypeError on any unrecognised
    # keyword, and bridge_bundle.read_bundle_ytd's `except (TypeError,
    # ValueError, KeyError): return None` turns that into the ENTIRE ytd
    # section silently vanishing -- wages, LTCG, STCG, dividends,
    # withholding, everything -- rather than just the one field it doesn't
    # understand. Dropping unrecognised keys here means a newer peer's
    # bundle degrades to losing one unknown field, never the whole section.
    # Derived from the dataclass itself, not a hand-maintained literal list,
    # so this can't drift the moment a new field is added -- that drift is
    # exactly the class of bug being fixed. gain_events/income_events are
    # excluded from the allow-set: they're already popped above and passed
    # as separate constructor args, not routed back through **data.
    valid_field_names = {f.name for f in fields(YTDSnapshot)} - {
        "gain_events",
        "income_events",
        "by_owner",
        "manual_marks",
        "ubs_withholding_by_owner",
    }
    data = {k: v for k, v in data.items() if k in valid_field_names}
    return YTDSnapshot(
        **data,
        gain_events=events,
        income_events=income_events,
        by_owner=by_owner,
        manual_marks=manual_marks,
        ubs_withholding_by_owner=ubs_withholding,
    )


def save_ytd_snapshot(ytd: YTDSnapshot) -> None:
    """Save YTD snapshot to disk as JSON."""
    write_pii_json(_YTD_CACHE_PATH, ytd_to_dict(ytd))


def load_ytd_snapshot() -> YTDSnapshot | None:
    """Load cached YTD snapshot from disk, or None if not available."""
    if not _YTD_CACHE_PATH.exists():
        return None
    try:
        data = read_pii_json(_YTD_CACHE_PATH)
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return ytd_from_dict(data)
    except (TypeError, ValueError, AttributeError):
        # A shape this build cannot read (e.g. an event missing a required
        # field) is treated like a corrupt file, never an app-boot crash
        # (audit-1008 F-57).
        return None
