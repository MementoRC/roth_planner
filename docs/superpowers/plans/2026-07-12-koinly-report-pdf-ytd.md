# Plan: Koinly crypto tax-report PDF importer + shared statements folder

**Date:** 2026-07-12
**Goal:** Import a Koinly "complete tax report" PDF into the three existing
`YTDSnapshot` crypto fields (`crypto_stcg_ytd`, `crypto_ltcg_ytd`,
`crypto_income_ytd`), and converge all PDF imports (brokerage, Koinly, 1040)
onto a single shared local statements folder.

## Motivation

Crypto YTD is currently manual, integer-only (`views/ytd_income.py:348-379`) —
drops cents and net-loss sign, redone monthly. The 1040 import is a per-upload
`st.file_uploader` (`views/setup/parameters.py:261-`). The brokerage import
already uses a persisted folder + scan. The user will drop the Koinly report and
the 1040 PDFs into the same local statements folder as the brokerage
statements, so all three should scan that one folder.

## Part A — Koinly parser: `engine/koinly_report_pdf.py` (mirrors `engine/tax_return_pdf.py`)

- `KoinlyParseError(Exception)`
- `KoinlyReport` dataclass: `tax_year`, `crypto_stcg`, `crypto_ltcg`,
  `crypto_income`, `captured_at`, `source="koinly_pdf"`, `parser_version`,
  `provenance` (income category breakdown, reported-total cross-check, page
  indices). `to_dict`/`from_dict`.
- `parse_koinly_text(pages: list[str]) -> KoinlyReport` — pure, no I/O:
  1. Tax year via `TAX YEAR (\d{4})`.
  2. Page containing `Capital gains summary`; anchor on the `Net gains` line and
     capture the following `Short term` / `Long term` values -> `crypto_stcg` /
     `crypto_ltcg`. (Those labels repeat under every row, so anchor to the
     `Net gains` block specifically.)
  3. Page containing `Income summary`; SUM the seven fixed Koinly income
     categories (`Airdrop`, `Fork`, `Mining`, `Reward`, `Salary`,
     `Lending interest`, `Other income`) -> `crypto_income`. Also parse the
     reported income `Total` when unambiguous; record a mismatch note in
     `provenance` if it differs from the sum by > $0.01 (guards against Koinly
     adding an unknown category). Summing sidesteps the duplicate `Total`
     (income vs expenses) on the two-column page.
- `parse_koinly_pdf(data: bytes) -> KoinlyReport` — thin wrapper, DEFERRED
  `import pdfplumber` (Pyodide-safe), `extract_text()` per page -> pure parser.
- `scan_koinly_folder(folder: Path) -> tuple[KoinlyReport | None, list[str]]` —
  newest `*koinly*.pdf` (case-insensitive) by mtime; `(report, errors)`.
- `save_koinly_report` / `load_koinly_report` -> `.koinly_cache.json` via
  `engine.secure_io`, tolerant of missing/corrupt file.
- Negative currency `$-12.34` (minus after `$`) handled by the same
  `_parse_currency` logic used in `tax_return_pdf.py`.

## Part B — Shared folder-path validation (DRY)

Extract the hardened validation inlined in the brokerage block
(`views/ytd_income.py:162-183`: reject blank/control-chars/`..`, resolve, must
be under `$HOME`) into `validate_local_folder(raw: str) -> tuple[Path | None,
str | None]` in `engine/brokerage_statement_pdf.py`. Rewire the brokerage block,
the new Koinly block, and the 1040 importer through it — no duplication. The
persisted folder path (`load_statement_folder_path` / `save_statement_folder_path`,
already in `brokerage_statement_pdf.py`) is the single shared setting.

## Part C — 1040 import becomes folder-scan

Add `scan_1040_folder(folder: Path) -> tuple[dict[int, Form1040Record], list[str]]`
to `engine/tax_return_pdf.py`: parse PDFs whose names match `*1040*` or
`*taxreturn*` (case-insensitive), keep those that parse as a 1040, key by year.
Rewire `views/setup/parameters.py` to scan the shared folder (button +
per-year confirmation) instead of / in addition to the uploader. 1040s update
~once a year, so a rescan-on-demand button is sufficient.

## View wiring (`views/ytd_income.py`)

New `##### Sync Crypto from Koinly Report (PDF)` block after the brokerage block:
shared statement folder, "Scan for Koinly report" button -> `scan_koinly_folder`,
cache the `KoinlyReport` in session + `.koinly_cache.json`, show the three parsed
values as metrics for confirmation, pre-fill the three crypto `number_input`s.
Pyodide guard (caption "requires local install"). Crypto fields are already
preserved on TurboTax-sync merge (`views/ytd_income.py:108-110`) — unchanged.

## Tests

`tests/test_koinly_report_pdf.py` (mirrors `test_brokerage_statement_pdf.py`):
- `parse_koinly_text` synthetic strings: happy path, negative LTCG, zero income,
  multi-category income sum, missing capital-gains page (raise), missing tax
  year (raise), income-sum vs reported-Total mismatch note.
- `_parse_currency` negative `$-12.34`.
- Cache round-trip with monkeypatched path.
- Integration test vs a local sample Koinly report PDF (skip if absent):
  parses the tax year and the three crypto fields.

Add `validate_local_folder` unit tests and `scan_1040_folder` tests alongside
the existing brokerage / 1040 test files.

## Non-goals (v1)

Per-disposal detail; futures/margin/gifts sections; fees/expenses
(the "Cost" row, not modeled in `YTDSnapshot`).

## Verification

`pixi run -e ci test`, `pixi run -e ci lint`, `pixi run -e ci type-check`.

---
