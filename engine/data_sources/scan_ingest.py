"""Unified folder-scan + 1040-candidate-record + cache-persist helper (W2 Part A).

Pure engine module: stdlib + engine.pdf_import / engine.tax_return_pdf /
engine.data_sources / engine.store only. No streamlit import (verified by
tests/test_scan_ingest.py) — this is the single writer that structurally kills
the ``_pdf_1040_scanned`` session dual-writer (audit defect #3): before this
module existed, ``views/ytd_income.py`` and ``views/setup/parameters.py`` each
ran their own merge-cache + record-candidate + session-write sequence for the
same scanned folder, and could silently diverge.

Scope: this helper owns the Form-1040 side of a folder scan (the part that
used to differ between the two view handlers) — merging parsed
``Form1040Record``s into the saved 1040 records and recording one
``prior_year_magi.<year>`` candidate per year. Brokerage/Koinly record
*application* (owner attribution via interactive confirmation, ledger writes,
YTD snapshot updates) was never routed through the candidate store in either
handler and stays a view-only concern — this helper only reports brokerage/
Koinly *counts* and returns the raw ``PdfImportResult`` so the caller can still
drive that interactive flow.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from engine.data_sources.record import record_magi_candidates
from engine.pdf_import import PdfImportResult
from engine.store import get_store
from engine.tax_return_pdf import (
    Form1040Record,
    compute_irmaa_magi,
    load_pdf_tax_records,
    save_pdf_tax_records,
)
from models.sourced import Source


@dataclass(frozen=True)
class ScanIngestResult:
    """Summary of one folder scan + 1040-candidate-record + cache-persist pass."""

    brokerage_count: int
    form_1040_count: int
    koinly_count: int
    skipped_count: int
    unrecognized_count: int
    magi_candidates_recorded: int
    errors: list[tuple[str, str]]
    raw: PdfImportResult
    # The merged pdf-tax cache written this pass — empty when no Form 1040 was
    # found this scan (mirrors the old handlers: the cache/candidate/session
    # write only ever happened when ``form_1040_records`` was non-empty).
    pdf_cache: dict[int, Form1040Record]
    netbenefits_count: int = 0
    ssa_count: int = 0

    @property
    def files_scanned(self) -> int:
        """Approximate total of categorized files.

        Not a strict 1:1 file count — a single brokerage statement PDF can
        yield several per-account records.
        """
        return (
            self.brokerage_count
            + self.form_1040_count
            + self.koinly_count
            + self.netbenefits_count
            + self.ssa_count
            + self.skipped_count
            + self.unrecognized_count
            + len(self.errors)
        )


def _merge_and_persist_pdf_cache(
    new_records: dict[int, Form1040Record],
) -> dict[int, Form1040Record]:
    """Merge *new_records* into the saved 1040 records and save them."""
    merged = load_pdf_tax_records()
    merged.update(new_records)
    save_pdf_tax_records(merged)
    return merged


def _record_scan_result(
    result: PdfImportResult,
    *,
    recorded_at: datetime | None,
) -> ScanIngestResult:
    """Shared core behind :func:`scan_and_record` and
    :func:`scan_documents_and_record`: record 1040 MAGI candidates and persist
    the merged pdf cache for an already-scanned *result*.

    Reproduces exactly what ``views/ytd_income.py``'s and
    ``views/setup/parameters.py``'s scan handlers used to do inline for Form
    1040 records: merge ``result.form_1040_records`` into the saved 1040
    records and record one ``prior_year_magi.<year>`` candidate (``Source.PDF``,
    detail "Form 1040 PDF") per year via ``record_magi_candidates`` — only
    when the scan found at least one Form 1040 this pass.

    The recorded candidate value is ``compute_irmaa_magi(rec.agi,
    rec.tax_exempt_interest, rec.feie)`` — ``prior_year_magi`` is the
    IRMAA-scoped 2-year-lookback slot (engine/scenario.py), and IRMAA MAGI
    adds the foreign earned income exclusion back (42 U.S.C. §1395r(i)(4),
    audit-1008 F-59).

    Kept a single entry point so the folder path (reads a directory) and the
    bytes path (public/Pyodide site, no filesystem) cannot silently diverge in
    what they merge/record for the exact same set of parsed documents.
    """
    when = recorded_at or datetime.now()

    pdf_cache: dict[int, Form1040Record] = {}
    magi_candidates_recorded = 0
    if result.form_1040_records:
        # One transaction (spec §2): the merged 1040 records and their MAGI
        # candidates are saved together or not at all. Parsing already happened.
        with get_store().transaction():
            pdf_cache = _merge_and_persist_pdf_cache(result.form_1040_records)
            magi_candidates_recorded = record_magi_candidates(
                {
                    yr: compute_irmaa_magi(rec.agi, rec.tax_exempt_interest, rec.feie)
                    for yr, rec in result.form_1040_records.items()
                },
                Source.PDF,
                "Form 1040 PDF",
                when,
            )

    return ScanIngestResult(
        brokerage_count=len(result.brokerage_records),
        form_1040_count=len(result.form_1040_records),
        koinly_count=len(result.koinly_reports),
        skipped_count=len(result.skipped),
        unrecognized_count=len(result.unrecognized),
        magi_candidates_recorded=magi_candidates_recorded,
        errors=list(result.errors),
        raw=result,
        pdf_cache=pdf_cache,
        netbenefits_count=len(result.netbenefits_statements),
        ssa_count=len(result.ssa_statements),
    )


def scan_and_record(
    folder: Path,
    *,
    recorded_at: datetime | None = None,
) -> ScanIngestResult:
    """Scan *folder*, record 1040 MAGI candidates, persist the merged pdf cache.

    Local-install entry point: reads a directory from disk. See
    :func:`scan_documents_and_record` for the bytes-based (public/Pyodide
    site) sibling — both delegate to :func:`_record_scan_result` for the
    actual merge/record/persist work.
    """
    from engine.pdf_import import scan_pdf_folder

    result = scan_pdf_folder(Path(folder))
    return _record_scan_result(result, recorded_at=recorded_at)


def scan_documents_and_record(
    documents: Iterable[tuple[str, bytes]],
    *,
    recorded_at: datetime | None = None,
) -> ScanIngestResult:
    """Scan already-read PDF bytes, record 1040 MAGI candidates, persist the
    merged pdf cache.

    Bytes-based sibling of :func:`scan_and_record` for the public (Pyodide)
    site, which has no filesystem and can only hand over uploaded bytes.
    *documents* is an iterable of ``(name, bytes)`` pairs, passed straight
    through to :func:`engine.pdf_import.scan_pdf_documents`. Delegates to the
    same :func:`_record_scan_result` core as the folder path, so the two
    cannot silently diverge in what they merge/record.
    """
    from engine.pdf_import import scan_pdf_documents

    result = scan_pdf_documents(documents)
    return _record_scan_result(result, recorded_at=recorded_at)
