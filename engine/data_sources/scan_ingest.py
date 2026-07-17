"""Unified folder-scan + 1040-candidate-record + cache-persist helper (W2 Part A).

Pure engine module: stdlib + engine.pdf_import / engine.tax_return_pdf /
engine.data_sources only. No streamlit import (verified by
tests/test_scan_ingest.py) — this is the single writer that structurally kills
the ``_pdf_1040_scanned`` session dual-writer (audit defect #3): before this
module existed, ``views/ytd_income.py`` and ``views/setup/parameters.py`` each
ran their own merge-cache + record-candidate + session-write sequence for the
same scanned folder, and could silently diverge.

Scope: this helper owns the Form-1040 side of a folder scan (the part that
used to differ between the two view handlers) — merging parsed
``Form1040Record``s into the on-disk pdf-tax cache and recording one
``prior_year_magi.<year>`` candidate per year. Brokerage/Koinly record
*application* (owner attribution via interactive confirmation, ledger writes,
YTD snapshot updates) was never routed through the candidate store in either
handler and stays a view-only concern — this helper only reports brokerage/
Koinly *counts* and returns the raw ``PdfImportResult`` so the caller can still
drive that interactive flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from engine.data_sources.paths import CANDIDATE_STORE_PATH
from engine.data_sources.record import record_magi_candidates
from engine.pdf_import import PdfImportResult
from engine.tax_return_pdf import Form1040Record, load_pdf_tax_records, save_pdf_tax_records
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
            + self.skipped_count
            + self.unrecognized_count
            + len(self.errors)
        )


def _merge_and_persist_pdf_cache(
    new_records: dict[int, Form1040Record], pdf_cache_path: str | Path | None
) -> dict[int, Form1040Record]:
    """Merge *new_records* into the persisted pdf-tax cache and save it.

    When *pdf_cache_path* is given, temporarily redirects
    ``engine.tax_return_pdf``'s module-level cache path for the duration of
    this call (test isolation) rather than changing that module's public
    ``load_pdf_tax_records``/``save_pdf_tax_records`` signatures, which have
    other callers.
    """
    if pdf_cache_path is None:
        merged = load_pdf_tax_records()
        merged.update(new_records)
        save_pdf_tax_records(merged)
        return merged

    import engine.tax_return_pdf as _tax_return_pdf_mod

    original_path = _tax_return_pdf_mod._PDF_TAX_CACHE_PATH  # noqa: SLF001 -- test-only path swap
    _tax_return_pdf_mod._PDF_TAX_CACHE_PATH = Path(pdf_cache_path)  # noqa: SLF001
    try:
        merged = load_pdf_tax_records()
        merged.update(new_records)
        save_pdf_tax_records(merged)
        return merged
    finally:
        _tax_return_pdf_mod._PDF_TAX_CACHE_PATH = original_path  # noqa: SLF001


def scan_and_record(
    folder: Path,
    *,
    store_path: str | Path = CANDIDATE_STORE_PATH,
    pdf_cache_path: str | Path | None = None,
    recorded_at: datetime | None = None,
) -> ScanIngestResult:
    """Scan *folder*, record 1040 MAGI candidates, persist the merged pdf cache.

    Reproduces exactly what ``views/ytd_income.py``'s and
    ``views/setup/parameters.py``'s scan handlers used to do inline for Form
    1040 records: merge ``result.form_1040_records`` into the on-disk pdf-tax
    cache and record one ``prior_year_magi.<year>`` candidate (``Source.PDF``,
    detail "Form 1040 PDF") per year via ``record_magi_candidates`` — only
    when the scan found at least one Form 1040 this pass.
    """
    from engine.pdf_import import scan_pdf_folder

    result = scan_pdf_folder(Path(folder))
    when = recorded_at or datetime.now()

    pdf_cache: dict[int, Form1040Record] = {}
    magi_candidates_recorded = 0
    if result.form_1040_records:
        pdf_cache = _merge_and_persist_pdf_cache(result.form_1040_records, pdf_cache_path)
        magi_candidates_recorded = record_magi_candidates(
            {yr: rec.magi for yr, rec in result.form_1040_records.items()},
            Source.PDF,
            "Form 1040 PDF",
            when,
            store_path=store_path,
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
    )
