# Spouse PDF Owner Attribution — Design

**Date**: 2026-07-13
**Status**: Approved (design), pending implementation plan
**Author**: household + Claude
**Related**: #357–#360 (YTD crypto fields, Koinly importer, content-based PDF router, two-column read + auto-apply)

## Problem

The YTD PDF import pipeline (brokerage statements, Koinly crypto report, TurboTax 1040) merges every parsed document into a single household-level `YTDSnapshot` with **no owner attribution**. When a second person's document is imported at a separate time, it silently **overrides** the first rather than adding to it.

The sharpest failure is Koinly: `crypto_stcg_ytd` / `crypto_ltcg_ytd` / `crypto_income_ytd` are single-valued fields set by direct assignment (`_snap.crypto_stcg_ytd = report.stcg`). Scanning your Koinly report sets them; scanning your spouse's report **overwrites** them. This assignment lives in the view's apply blocks (`views/ytd_income.py`), not in the Koinly parser module — the fix locus is the view/apply layer. Brokerage records key by `account_number` (distinct accounts sum), but separate-time re-scan idempotency can wipe a prior import. The 1040 is a joint MFJ return — one document covering both filers — and is not affected.

## Goals

- Import each spouse's PDFs **separately and at separate times** without one overriding the other.
- Attribute each PDF to an **owner** so re-scanning the same owner's document **replaces** its contribution (idempotent) while a different owner's document **adds** a separate contribution.
- Keep a **per-owner breakdown** internally — primarily for debugging and correct dedup, not for engine-level tax math.
- Preserve today's behavior exactly for the single-owner case.

## Non-Goals

- **No per-person engine split.** All YTD tax math (MAGI, brackets, NIIT, ACA/IRMAA headroom) stays computed on the household total. The owner dimension is **import-time only** (MFJ).
- No new `your_/spouse_` prefixed fields in `YTDSnapshot` beyond the derivation described below.
- 1040 owner-splitting is out of scope. It is categorized as `household` because it is a single return covering the filing unit — a scope decision, not an assertion that the parser detects or enforces MFJ (`Form1040Record.filing_status` is an optional string, and the app supports single-filer households). Either way, the 1040 is never owner-split.

## Owner Vocabulary

Three roles, reusing the portfolio flow's terms plus a joint category:

- `you`
- `spouse`
- `household` — joint documents (the 1040) and jointly-held accounts/crypto.

The derive-sum runs across all three (`you + spouse + household`) to produce the single MFJ household total. There is no override risk between the three slots because they are distinct ledger keys.

## Approach A — Per-Owner Contribution Ledger (chosen)

### 1. Owner identification (hybrid auto-extract + confirm)

Each parser gains an owner-extraction helper that pulls a stable **owner key** from the PDF it already reads:

- **Brokerage** — account-holder name from the statement header (and SSN-last-4 when present).
- **Koinly** — the account name/email Koinly prints on the report; may be absent.
- **1040** — no extraction; auto-categorized as `household`. Continues to feed `prior_year_magi` unchanged.

A persisted **learned name→owner map** (`.pdf_owner_map.json`) maps a normalized owner key → `you` | `spouse` | `household`. The scan summary shows each document's resolved owner; the user confirms or corrects once, and the correction is written back to the map so the same statement auto-resolves next time.

Fallback: when no owner key can be extracted, `owner = None` and the UI requires a manual role pick (you / spouse / household) before that document can apply.

### 2. Per-owner ledger (the store)

A new cache file `.pdf_import_ledger.json` becomes the source of truth for PDF-derived contributions:

```json
{
  "koinly": {
    "you":    {"stcg": 0.0, "ltcg": 0.0, "income": 0.0, "captured_at": "...", "source": "..."},
    "spouse": {"stcg": 0.0, "ltcg": 0.0, "income": 0.0, "captured_at": "...", "source": "..."}
  },
  "brokerage": {
    "you":    {"<account_number>": {"...record fields..."}},
    "spouse": {"<account_number>": {"...record fields..."}}
  }
}
```

Keys are owners in `{you, spouse, household}`. The 1040 MAGI is not part of this ledger (it feeds `prior_year_magi` separately and is unchanged).

**Router change required**: `scan_pdf_folder` (`engine/pdf_import.py`) currently keeps only the single newest Koinly report by mtime across a folder scan, and `PdfImportResult.koinly_report` is a lone `KoinlyReport | None`. Because two owners' Koinly PDFs may be present in one scan, this must change to a per-owner-compatible shape (a list or per-owner dict) so the router does not silently drop one owner's report before ledger logic runs. Ledger writes happen inside the router's per-file loop, before any collapse.

### 3. Data flow

```
scan folder
  -> per PDF: extract_pages -> classify -> parse
       -> extract owner key -> resolve via learned map -> (confirm/correct UI)
       -> write ledger[doc_type][owner][key]
  -> derive snapshot PDF-derived fields = sum across owners
  -> save snapshot + ledger + learned map
```

Idempotency: re-scanning the same owner's document overwrites its ledger slot -> identical total. A new owner's document writes a separate slot -> added to the total.

### 4. Dedup / override semantics (the fix)

Governing rule: **derive-on-apply reproduces today's exact behavior for a single owner and sums for multiple owners.**

- **Koinly** — `snapshot.crypto_stcg_ytd = Σ_owner ledger["koinly"][owner].stcg` (same for ltcg/income). Today's single-valued assignment becomes a sum keyed by owner; the override bug vanishes by construction. One owner -> identical to now; two owners -> added.
- **Brokerage** — sum over `(owner, account_number)`. Re-scan replaces that account's slot (as today); a different owner's accounts add.
- **Manual-only fields** (wages, NEC, IRA conversions, qualified dividends, HSA, deductible IRA) are never touched by ledger derivation — same guarantee as the current apply path.

**Confirmed current behavior**: `apply_brokerage_statement_records` (`engine/portfolio_sync/ytd.py`) recomputes `totals` fresh from `taxable_by_account` each call and **directly assigns** (`ytd.interest_ytd = totals[...]`), i.e. it replaces per-call rather than summing against a manual base. The ledger derive step must preserve this replace-for-single-owner behavior while summing across owners, and the plan must ensure the derived total is `manual_base + Σ ledger` with **no double-count** for any snapshot field that also has a manual-entry widget.

### 5. UI (views/ytd_income.py)

- Scan summary gains an **owner column** plus a per-document confirm/correct control offering `you` / `spouse` / `household`.
- A **per-owner breakdown** expander for debugging (e.g. "Crypto STCG: you $X + spouse $Y + household $Z = $Total").
- A manual role selector for any undetected-owner document (fallback).

### 6. Testing (extend existing files)

Extend `tests/test_koinly_report_pdf.py`, `tests/test_brokerage_statement_pdf.py`, `tests/test_pdf_import.py`, `tests/test_views_ytd_income.py`:

- Owner extraction per parser (fixtures with holder names / SSN-last-4).
- Learned name→owner map resolve + write-back on correction.
- Ledger derive = sum across owners.
- **Idempotent re-scan** (same owner twice -> unchanged total).
- **Two-owner additive** (you + spouse -> summed total).
- **Koinly override-fixed regression** built from real flattened two-column output (the specific bug).
- No-owner **fallback** path (manual role required).

## Alternatives Considered

- **B — Owner-tagged records, dedup by `(owner, key)`**: add an `owner` field to the record dataclasses and dedup at apply time. Rejected because Koinly's single-valued snapshot fields still need per-owner persistence to avoid double-count on re-scan, which reinvents the ledger with more friction for the exact case that is broken.
- **C — Two full YTDSnapshots (you/spouse) merged at the engine boundary**: rejected as the full per-person split that is an explicit non-goal; it touches every headroom consumer and the persistence layer for zero engine benefit under MFJ.

## Scope

One PR, mirroring the #357–#360 cadence: ledger module + owner-extraction helpers + learned-map store + view wiring + tests. Engine tax math untouched.
