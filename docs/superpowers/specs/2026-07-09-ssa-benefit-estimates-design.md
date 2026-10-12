# SSA Benefit-Estimate Sync — Design

**Status**: Approved (brainstorming), pending spec review
**Date**: 2026-07-09
**Author**: Claude (roth_planner session), with mementorc

## Context

FinExtract (the local scraper server roth_planner already syncs with for
brokerage holdings, tax-return PDFs, and YTD income) now exposes a new
schema: `ssa-retirement-benefit-estimates-v1` under domain `social_security`,
reachable at `GET /query/social_security?data_type=benefit_estimates`.

Fields per row: `retirement_age`, `claim_date`, `benefit_type`,
`monthly_amount` — one row per claiming age the SSA statement lists (e.g.
62 / FRA / 70).

FinExtract's own schema doc (`FinancialScrapper/docs/SCHEMA_REFERENCE.md`)
marks roth_planner's consumption status for this schema as `pending`, and
notes it supersedes roth_planner's old Phase C item **C4** ("SS earnings
record / PIA estimate"), which had assumed an SSA SOAP-scrape or
PDF-extraction approach before this schema existed.

Today, roth_planner collects Social Security inputs entirely by manual entry
in `views/setup/parameters.py`: `your_ss_fra` / `spouse_ss_fra` (monthly $ at
FRA) and `your_ss_start_age` / `spouse_ss_start_age` (claim age 62–70) are
plain `st.number_input` widgets. The engine (`engine/ira.py:ss_benefit_at_age`)
already derives the benefit at any claim age from the FRA value using the
standard SSA early/delayed-claiming reduction and delayed-retirement-credit
schedule — so the FRA monthly benefit is the one number that actually needs
to come from an external source; claim age is a planning input the user
chooses, not something to sync.

## Goal

Let the user sync `your_ss_fra` / `spouse_ss_fra` from their FinExtract SSA
statement data instead of hand-copying it, following the exact pattern
already established for IRA/Roth balances (holdings), tax-return fields, and
YTD income.

## Non-goals

- Syncing or storing claim-age-specific benefit amounts (62/70 estimates)
  as separate fields — the engine already derives these from FRA benefit.
- Auto-selecting a claim age based on SSA data.
- Building a new "sync hub" UI; this reuses the existing per-file Me/Spouse
  toggle and inline sync-button conventions.

## Design

### 1. Engine layer — `engine/portfolio_sync/social_security.py` (new file)

Mirrors the existing per-domain modules (`tax_return.py`, `ytd.py`) in the
`engine/portfolio_sync` package:

```python
def fetch_ssa_benefit_estimates() -> list[dict]:
    """GET /query/social_security?data_type=benefit_estimates, flattened."""
```

- Uses the package's existing `_get()` helper from `client.py` (bearer-token
  auth via `_headers()`/`_load_token()`, `BASE_URL` from
  `FINEXTRACT_URL` env var, `allow_redirects=False`), `timeout=5`,
  `resp.raise_for_status()`, `resp.json()`, then
  `_flatten_query_rows(data)` to normalize single- vs multi-institution
  response shapes (this endpoint is single-institution — SSA — but reusing
  the shared helper keeps behavior consistent with every other domain).

Dataclasses:

```python
@dataclass
class SSABenefitEstimate:
    retirement_age: int
    claim_date: str
    benefit_type: str
    monthly_amount: float

@dataclass
class SSASnapshot:
    estimates: list[SSABenefitEstimate]
```

Cache, matching the `tax_return.py` / `ytd.py` pattern exactly:

```python
_SSA_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / ".ssa_cache.json"

def save_ssa_snapshot(snap: SSASnapshot) -> None: ...
def load_ssa_snapshot() -> SSASnapshot | None: ...
```

Both use `engine.secure_io.read_pii_json` / `write_pii_json` (the same
PII-aware read/write helpers `portfolio.py` uses), since SSA benefit amounts
are financial PII like everything else already cached this way.

### 2. UI layer — `views/setup/parameters.py`

Next to each of `your_ss_fra` and `spouse_ss_fra`:

- A "Sync from FinExtract" button, gated by the same Me/Spouse toggle
  pattern used elsewhere for per-file uploads (`engine/upload_merge.py`'s
  `as_spouse` convention).
- On click: call `fetch_ssa_benefit_estimates()`, find the estimate whose
  `retirement_age == your_fra_age` (or `spouse_fra_age`); if there's no
  exact match, fall back to the estimate with the smallest
  `abs(retirement_age - fra_age)`. Write `monthly_amount` into
  `st.session_state["your_ss_fra"]` (or `"spouse_ss_fra"`), call
  `save_ssa_snapshot()`, and set a session flag
  (`_ssa_synced_you` / `_ssa_synced_spouse`).
- When the flag is set, the field renders with the existing `" (synced)"`
  label suffix and `disabled=True`, exactly like the current treatment of
  `your_ira` / `your_roth` / `spouse_ira` / `spouse_roth` when
  `st.session_state.get("portfolio_snapshot")` is truthy.
- Claim age (`your_ss_start_age` / `spouse_ss_start_age`) is unaffected —
  stays a manual planning input.

### 3. Error handling

Best-effort and non-blocking, consistent with every other FinExtract
consumer in this codebase:

- Network/HTTP errors from `fetch_ssa_benefit_estimates()` are caught at the
  UI call site and surfaced via `st.warning(...)`; they never raise into
  page rendering.
- If FinExtract is unreachable, the manual `your_ss_fra` / `spouse_ss_fra`
  entry fields and their current values are untouched — the sync button
  simply fails gracefully and the field stays editable.
- If the fetched estimate list has no row matching (or close to)
  `your_fra_age` / `spouse_fra_age`, warn and skip auto-fill rather than
  writing a guessed value.

### 4. Testing

New tests (file TBD at plan time — likely `tests/test_portfolio_sync_ssa.py`
to match the one-file-per-domain split implied by the existing
`portfolio_sync` package, or a new test class in `tests/test_engine.py` if
the existing suite doesn't split that way) covering:

- `fetch_ssa_benefit_estimates()` against a mocked HTTP response (reuse
  whatever mocking pattern the existing `tax_return.py`/`ytd.py` tests use).
- Cache round-trip: `save_ssa_snapshot()` → `load_ssa_snapshot()`.
- Retirement-age matching logic: exact match, nearest-age fallback, and
  no-match-warns-and-skips.

## Open questions for the implementation plan

- Exact test file location/split (see Testing section) — confirm by reading
  the existing `tests/` layout for `portfolio_sync` domain tests before
  writing the plan.
- Confirm the precise mechanism `parameters.py` currently uses to compute
  `_synced` for IRA/Roth (session flag name(s), where it's cleared) so the
  new `_ssa_synced_you`/`_ssa_synced_spouse` flags follow identical
  lifecycle semantics (e.g. cleared alongside `_suppress_snapshot_autoload`
  on `_clear_personal_session_state()` in `views/setup/_state.py`).
