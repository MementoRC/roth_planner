# Design Spec: Consolidated single-`.enc` bridge with full-replace import

- **Date**: 2026-07-13
- **Status**: Approved design (rev 2, post spec-review) — pending final user read, then implementation planning
- **Approach**: A (ledger-canonical bundle)
- **Related**: PR #362 (`1f234cb`, per-owner ledger derive-sum); sibling design `docs/superpowers/specs/2026-07-13-spouse-pdf-owner-attribution-design.md`

## 1. Context & problem

The roth_planner app lets two spouses run separate instances and exchange data via an encrypted (`.enc`) bridge. Sealing is implemented by `engine/data_bridge_crypto.py` (`seal()` at line 37, `unseal()` at line 47, PyNaCl sealed-box). Today the bridge exports **two** artifacts:

- `.user_defaults.json.enc` — a fixed list of setup scalars, built by `_user_defaults_from_session()` (`views/setup/_state.py:42`, scalar_keys list at lines 45-73).
- `.portfolio_cache.json.enc` — the raw bytes of `.portfolio_cache.json`, sealed in `views/setup/data_bridge.py:351-363` (`read_pii_bytes` → `seal`).

Two problems:

1. **Scattered transfer.** The app persists ~7 local JSON caches, but only 2 ride the bridge. The receiving spouse gets no YTD income and no PDF-import ledger, so they must re-upload PDFs locally.
2. **No authoritative reset.** Import merges rather than fully replacing an owner's slot, so stale data can linger.

The reported "doubling of everything" was a self-simulation artifact (loading one's own PDFs, exporting, re-importing the same file as Spouse on the same machine, re-running PDFs), not a live production bug. In real bidirectional use each spouse imports the *other's* `.enc`, so that collision cannot occur. This spec addresses the structural gaps.

## 2. Goals

- **One `.enc` file** replaces the two sealed artifacts.
- **Import = authoritative full-replace** of the chosen owner slot: owner-attributable fields absent from the incoming `.enc` are reset, not left stale.
- **Carry the per-owner PDF ledger** (`.pdf_import_ledger.json`), which the bridge currently omits — this is the canonical owner-tagged income source.

## 3. Non-goals

- No changes to the 7 local `.json` cache schemas, shapes, or writers.
- No transfer of manually-typed (untagged) YTD fields — they stay local.
- **No transfer of equity-grant holdings or `txn_shares`.** `EquityGrant` (`engine/portfolio_sync/shapes.py:120-128`) has no `owner` field; grants are modeled as the primary's in this household. They stay local, and `merge_snapshots`' existing drop-on-import behavior is retained and correct. (Realized option *income* is handled separately — see section 8.)
- No owner-scoping of the global YTD snapshot cache — household totals re-derive from the ledger instead (section 7.2).
- No transfer of raw parse caches (`.koinly_cache.json`, `.statement_account_overrides.json`) or the learned `.pdf_owner_map.json`.

## 4. Locked decisions

1. One file, not two — merge into a single versioned bundle, sealed once.
2. Local `.json` caches untouched — the refactor lives entirely in the `.enc` pack/unpack layer.
3. Import = full replace (clear-then-apply) of the chosen owner slot, for owner-attributable data.
4. Keep the existing Me/Spouse radio at upload — symmetric mirror model; both spouses can hand a `.enc` to the other.
5. Transfer only owner-attributable data: portfolio accounts (owner-filtered) + the per-owner ledger slice + owner-prefixed setup scalars.

## 5. The mirror model

Each app holds `me = self` and `spouse = other`. Each person exports only their own (`you`-owned) data. The other imports it into the target slot, rewriting `you` → target owner (as `merge_snapshots` (`engine/portfolio_sync/holdings.py:19`, owner rewrite at `:50`) already does for account `owner`). The exchange is symmetric: one sealed `.enc` in each direction.

## 6. Bundle format

An in-memory versioned dict (NOT a new cache file), sealed once via `engine/data_bridge_crypto.py`:

```
{
  "format_version": 2,
  "sections": {
    "setup_scalars": { /* owner-prefixed + household keys — today's user_defaults list */ },
    "portfolio": {
      "accounts": [ /* owner-filtered to exporter (you) */ ]
    },
    "ledger": {
      "koinly":    { "you": {…} },            /* exporter's owner slice only */
      "brokerage": { "you": { "<acct>": {…} } }
    }
  }
}
```

The ledger's owner keys are free-form strings resolved via `resolve_owner`/`OWNER_ROLES`; `"you"` here is the exporter's role placeholder. Downloaded as one file named `roth_bridge.enc`. The two old `.json.enc` filenames are retired. Equity grants and `txn_shares` are deliberately absent (section 3).

## 7. Flows

### 7.1 Export (`views/setup/_state.py` + `views/setup/data_bridge.py`)

1. **setup_scalars** — via the existing `_user_defaults_from_session()` key list, unchanged.
2. **portfolio.accounts** — read `.portfolio_cache.json`; owner-filter `accounts` to `you`. Grants/`txn_shares` are not included.
3. **ledger** — read `.pdf_import_ledger.json` (written by `engine/pdf_ledger.py` `save_ledger()` at lines 134-135; shape at lines 31-36); slice out the exporter's owner entries under `koinly.<owner>` and `brokerage.<owner>`. **New helper needed** — no owner-slice read exists in `pdf_ledger.py` today (only `write_*_contribution`, `derive_*_totals`, `save_ledger`, `load_ledger`); a small read/extract helper is added.
4. **Seal once** → single `roth_bridge.enc` download. Replaces the two-file logic in `_handle_personal_exports` (`views/setup/data_bridge.py:295`).

### 7.2 Import — full replace (`views/setup/data_bridge.py`, existing Me/Spouse radio at `:275`)

For the chosen target owner (`Me` → `you`, `Spouse` → `spouse`):

1. **Clear the target owner's slice first**: drop `owner == target` accounts from the portfolio cache; delete the target owner's `koinly` and `brokerage` entries from the ledger. **New helper needed** — a ledger owner-slice delete (e.g. `ledger["koinly"].pop(owner, None)`) is added; it does not exist today.
2. **Apply** the bundle's sections, rewriting incoming `you` → target owner (accounts via the existing `merge_snapshots`/`_apply_portfolio_snapshot` path; ledger via the new helper).
3. **Re-derive** the household YTD snapshot from the updated ledger via the existing PR #362 path (`derive_koinly_totals`/`derive_brokerage_totals` at `engine/pdf_ledger.py:83-124`, consumed in `views/ytd_income.py` at lines 231, 276, 382, 432). **Derive semantics (invariant to preserve):** ledger-derived YTD fields (e.g. crypto/brokerage income) are recomputed as a fresh overwrite from the sum of that field's ledger contributions across owners — NOT an accumulation onto the prior derived value (confirmed at `views/ytd_income.py:231-236, 276-279`, which assign `derive_*_totals` output directly to the snapshot). For any field that combines a manual base with ledger contributions, the derive must recompute `base + Σledger` rather than adding onto the existing value. Because ledger contributions are owner-scoped and untagged-manual entries never transfer, clear-then-apply-then-derive is idempotent and cannot double-count. Implementation must verify this field-by-field against `views/ytd_income.py` (see section 11).
4. **setup_scalars** written to session state, as today.

Because step 1 clears the slot before step 2 applies, any owner-attributable field absent from the bundle is gone after import. This makes "reset missing fields" true for accounts and ledger-derived income.

## 8. Realized option income & the acid test

**Acid test**: the receiver previously assumed the spouse sold/exercised options; the incoming `.enc` reflects none — the stale data must clear.

Per the design decision, "the spouse's stock options" means **realized option income** (from exercise/sale), not outstanding grant holdings. Realized option income that is owner-attributable rides the ledger/owner-prefixed-scalar paths and is therefore subject to the clear-then-apply reset in section 7.2. Outstanding **grant holdings** stay local (section 3) and are not part of the acid test. (Note: any realized-income component that is stored only as an *untagged manual* YTD field would remain local per section 3 and would not reset via the bridge; implementation should confirm the relevant realized-income fields are ledger- or scalar-backed.)

## 9. Scope boundary

| Store | On the bridge? | Why |
|---|---|---|
| `.portfolio_cache` accounts | transfers | Owner-tagged; owner-filtered on export, replaced on import |
| `.pdf_import_ledger.json` | transfers | Per-owner already; canonical PDF-derived income source |
| owner-prefixed setup scalars | transfers | Already transfer today; folded into the one bundle |
| equity-grant holdings + `txn_shares` | stays local | No `owner` field; modeled as the primary's; `merge_snapshots` drop retained |
| manual YTD fields (untagged) | stays local | No owner tag; cannot split without touching the cache |
| `.koinly_cache.json`, `.statement_account_overrides.json` | stays local | Raw parse scratch; results already captured in the ledger |
| `.pdf_owner_map.json` | stays local | Learned per-machine classifier prefs |
| `.tax_return_cache.json` | n/a | Dead code — no longer written |

## 10. Resolved open questions

- **Q1 Filename** → `roth_bridge.enc`.
- **Q2 Legacy `.enc`** → on load, detect a missing `format_version` and show a one-line "please re-export" notice. No reader for the old two-file format.

## 11. Accepted risk

Re-deriving YTD from the ledger (section 7.2 step 3) assumes the derive path recomputes `manual_base + Σledger` rather than accumulating. If any realized-income field is stored only as an untagged manual YTD entry, it stays local (section 3) and will not reset via the bridge. This is an accepted limitation of "keep caches as-is" and must be verified against `views/ytd_income.py` during implementation.

## 12. Edge cases

- **Empty owner slice** (e.g. no ledger entries) → clear-then-apply naturally resets to empty.
- **Importing the same owner twice** → idempotent; clear-then-apply refreshes without stacking.
- **Importing as Spouse must not touch the primary's local grants** → guaranteed because the bundle has no grant section and import never writes grants.
- **`_clear_personal_session_state`** (`views/setup/_state.py:132`, TODO at 178-179) — confirm the clear path covers the caches the new import writes, so a fresh import starts clean.

## 13. Testing (new cases in `tests/test_engine.py`)

- **Round-trip** — export → import → reconstructed owner-attributable state matches source.
- **Full-replace reset** — pre-seed a stale spouse ledger entry, import a bundle without it, assert it is gone after re-derive.
- **Ledger owner isolation** — importing as spouse never touches `you` ledger entries.
- **Grants untouched** — importing as spouse leaves the primary's local grants intact.
- **YTD re-derive** — household totals recompute as `manual_base + Σledger` (no double-count) after import.
- **Legacy detection** — a bundle without `format_version` triggers the re-export notice, not a crash.

## 14. Affected files (anticipated)

- `views/setup/data_bridge.py` — bundle assembly, single-file export, clear-then-apply import, legacy detection.
- `views/setup/_state.py` — scalar gathering (reuse), clear-personal-state coverage.
- `engine/pdf_ledger.py` — **new** owner-slice read + delete helpers.
- `engine/data_bridge_crypto.py` — reuse `seal`/`unseal` (no change expected).
- `tests/test_engine.py` — new cases per section 13.
- (`engine/portfolio_sync/holdings.py` — no change; existing `merge_snapshots` account rewrite + grant-drop behavior is retained as correct.)

## 15. Next steps

1. Spec review loop (independent reviewer) — fix and re-run until clean.
2. User reads this spec.
3. Implementation plan via the writing-plans skill.
4. Build → PR, same flow as the per-owner attribution feature (PR #362).
