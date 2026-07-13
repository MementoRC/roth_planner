# Spouse PDF Owner Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attribute each imported PDF (brokerage statement, Koinly crypto report) to an owner (`you` / `spouse` / `household`) via a per-owner contribution ledger, so scanning a second person's document adds to the YTD snapshot instead of silently overwriting the first.

**Architecture:** Approach A — a new pure-engine ledger module (`engine/pdf_ledger.py`) becomes the source of truth for PDF-derived Koinly and brokerage contributions, keyed by `(doc_type, owner)`; snapshot fields are derived by summing across owners at apply time instead of direct single-valued assignment. Owner identification is hybrid: each parser extracts a best-effort owner key from the PDF text, a persisted learned map (`engine/pdf_owner.py`, `.pdf_owner_map.json`) resolves key → role, and the view lets the user confirm/correct once (written back to the map). The 1040 stays owner-agnostic (`household`, unchanged) and engine tax math (MAGI/brackets/NIIT/ACA) is untouched — the owner dimension is import-time only.

**Tech Stack:** Python, Streamlit, pdfplumber (deferred import, Pyodide-safe), JSON file caches, pytest.

---

## File Structure

| File | Responsibility |
|---|---|
| `engine/pdf_owner.py` | **Create.** `OwnerRole` constants (`you`/`spouse`/`household`), `normalize_owner_key()`, `load_owner_map()`/`save_owner_map()` (`.pdf_owner_map.json`), `resolve_owner()`, `learn_owner()`. Pure + JSON I/O, no Streamlit. |
| `engine/pdf_ledger.py` | **Create.** `PdfLedger` shape, `load_ledger()`/`save_ledger()` (`.pdf_import_ledger.json`), `write_koinly_contribution()`, `write_brokerage_contribution()`, `derive_koinly_totals()`, `derive_brokerage_totals()`. Pure + JSON I/O. |
| `engine/koinly_report_pdf.py` | **Modify.** Add `extract_owner_key()` (name/email extraction) and an `owner_key: str \| None` field on `KoinlyReport`. |
| `engine/brokerage_statement_pdf.py` | **Modify.** Add `extract_owner_key()` (account-holder name / SSN-last-4) and an `owner_key: str \| None` field on `BrokerageStatementRecord`. |
| `engine/pdf_import.py` | **Modify.** `PdfImportResult.koinly_report: KoinlyReport \| None` -> `koinly_reports: list[KoinlyReport]`; remove the single-newest-by-mtime collapse so multiple owners' reports all survive one scan. |
| `views/ytd_income.py` | **Modify.** Wire owner resolution + confirm/correct UI, write to the ledger, derive snapshot Koinly/brokerage fields as sum-across-owners (replacing the direct-assignment blocks), add a per-owner breakdown expander. |
| `models/ytd_income.py` | **Unchanged.** No new fields — confirmed during research; `crypto_stcg_ytd`/`crypto_ltcg_ytd`/`crypto_income_ytd` and the brokerage-derived fields remain single household-level totals, now populated by ledger-derive instead of direct assignment. |
| `.gitignore` | **Modify.** Add `.pdf_owner_map.json`, `.pdf_import_ledger.json` (see Task 0). |
| `tests/test_pdf_owner.py` | **Create.** Unit tests for the new owner module. |
| `tests/test_pdf_ledger.py` | **Create.** Unit tests for the new ledger module (override-fix proven here). |
| `tests/test_koinly_report_pdf.py` | **Modify.** Add owner-extraction tests. |
| `tests/test_brokerage_statement_pdf.py` | **Modify.** Add owner-extraction tests per broker fixture. |
| `tests/test_pdf_import.py` | **Modify.** Update to the `koinly_reports` list shape; add two-owner-survives-scan test. |
| `tests/test_views_ytd_income.py` | **Modify.** Add override-fixed regression, two-owner additive, idempotent re-scan, no-owner fallback tests. |

---

## Task 0: Housekeeping — add new cache files to `.gitignore`

Add the two new cache files to `.gitignore`. Note: `.koinly_cache.json` is deliberately excluded from this task even though it is currently untracked, because it is being handled by a separate in-flight PR (#361, branch fix/gitignore-koinly-cache); adding it here too would create a duplicate line when both PRs land, causing a merge conflict. This plan adds only the two genuinely new caches.

- [ ] Add `.pdf_owner_map.json`, `.pdf_import_ledger.json` to `.gitignore` under the "Project-specific" section (see Task 1 for the exact edit, done once).

**Files:**
- Modify: `/home/memento/PycharmProjects/roth_planner/.gitignore`

---

## Task 1: `engine/pdf_owner.py` — owner roles, key normalization, learned map

### Step 1.1: Write failing tests for `OwnerRole` + `normalize_owner_key`

Create `tests/test_pdf_owner.py`:

```python
"""Tests for engine.pdf_owner -- owner role vocabulary and learned name->owner map."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.pdf_owner import (
    OWNER_ROLES,
    OwnerRole,
    learn_owner,
    load_owner_map,
    normalize_owner_key,
    resolve_owner,
    save_owner_map,
)


class TestOwnerRoles:
    def test_owner_roles_frozenset(self) -> None:
        assert OWNER_ROLES == frozenset({"you", "spouse", "household"})

    def test_owner_role_constants(self) -> None:
        assert OwnerRole.YOU == "you"
        assert OwnerRole.SPOUSE == "spouse"
        assert OwnerRole.HOUSEHOLD == "household"


class TestNormalizeOwnerKey:
    def test_lowercases_and_strips(self) -> None:
        assert normalize_owner_key("  Claude R Cirba  ") == "claude r cirba"

    def test_collapses_internal_whitespace(self) -> None:
        assert normalize_owner_key("Claude   R\tCirba") == "claude r cirba"

    def test_none_passthrough(self) -> None:
        assert normalize_owner_key(None) is None

    def test_empty_string_becomes_none(self) -> None:
        assert normalize_owner_key("   ") is None


class TestOwnerMapRoundTrip:
    def test_save_load_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_owner as mod

        monkeypatch.setattr(mod, "_OWNER_MAP_PATH", tmp_path / ".pdf_owner_map.json")
        save_owner_map({"claude r cirba": "you"})
        assert load_owner_map() == {"claude r cirba": "you"}

    def test_load_missing_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_owner as mod

        monkeypatch.setattr(mod, "_OWNER_MAP_PATH", tmp_path / "nope.json")
        assert load_owner_map() == {}

    def test_load_corrupt_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_owner as mod

        bad = tmp_path / ".pdf_owner_map.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_OWNER_MAP_PATH", bad)
        assert load_owner_map() == {}


class TestResolveOwner:
    def test_resolve_hit(self) -> None:
        assert resolve_owner("claude r cirba", {"claude r cirba": "you"}) == "you"

    def test_resolve_miss_returns_none(self) -> None:
        assert resolve_owner("jane doe", {"claude r cirba": "you"}) is None

    def test_resolve_none_key_returns_none(self) -> None:
        assert resolve_owner(None, {"claude r cirba": "you"}) is None

    def test_resolve_normalizes_before_lookup(self) -> None:
        assert resolve_owner("  Claude R Cirba  ", {"claude r cirba": "you"}) == "you"


class TestLearnOwner:
    def test_learn_adds_entry(self) -> None:
        existing: dict[str, str] = {}
        updated = learn_owner("Jane R Cirba", "spouse", existing)
        assert updated == {"jane r cirba": "spouse"}
        assert existing == {}  # pure -- does not mutate the input

    def test_learn_overwrites_existing_entry(self) -> None:
        existing = {"claude r cirba": "spouse"}
        updated = learn_owner("Claude R Cirba", "you", existing)
        assert updated == {"claude r cirba": "you"}

    def test_learn_rejects_invalid_role(self) -> None:
        with pytest.raises(ValueError, match="Invalid owner role"):
            learn_owner("Claude R Cirba", "cousin", {})

    def test_learn_none_key_raises(self) -> None:
        with pytest.raises(ValueError, match="owner key"):
            learn_owner(None, "you", {})
```

### Step 1.2: Run test, expect FAIL

```
pixi run -e ci test -- tests/test_pdf_owner.py -v
```
Expected: `ModuleNotFoundError: No module named 'engine.pdf_owner'` (collection error).

### Step 1.3: Implement `engine/pdf_owner.py`

```python
"""Owner role vocabulary and learned name->owner map for PDF import attribution.

Three roles, reusing the portfolio flow's terms (views/setup/portfolio.py's
"you"/"spouse" owner vocabulary) plus a joint category for documents/accounts
that are inherently shared (the 1040, jointly-held brokerage accounts):

- "you"
- "spouse"
- "household" -- joint documents and jointly-held accounts/crypto.

Import-time only -- see docs/superpowers/specs/2026-07-13-spouse-pdf-owner-
attribution-design.md. Pure functions + a small JSON cache, no Streamlit
import (engine/ purity rule).
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path

from engine.secure_io import read_pii_json, write_pii_json


class OwnerRole(StrEnum):
    """The three owner roles a PDF-derived contribution can be attributed to."""

    YOU = "you"
    SPOUSE = "spouse"
    HOUSEHOLD = "household"


OWNER_ROLES: frozenset[str] = frozenset({r.value for r in OwnerRole})

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_owner_key(raw: str | None) -> str | None:
    """Normalize a raw extracted owner key (name/email) for stable map lookup.

    Lowercases, strips leading/trailing whitespace, and collapses internal
    whitespace runs to a single space. Returns None for None or blank input
    so callers can treat "no key extracted" uniformly.
    """
    if raw is None:
        return None
    collapsed = _WHITESPACE_RE.sub(" ", raw.strip())
    return collapsed.lower() if collapsed else None


def resolve_owner(key: str | None, owner_map: dict[str, str]) -> str | None:
    """Look up *key* in the learned owner_map. Returns None if key is None or unknown.

    *key* is normalized before lookup so callers may pass the raw extracted
    string directly.
    """
    normalized = normalize_owner_key(key)
    if normalized is None:
        return None
    return owner_map.get(normalized)


def learn_owner(key: str | None, role: str, owner_map: dict[str, str]) -> dict[str, str]:
    """Return a NEW owner_map with *key* -> *role* written in (pure, no mutation).

    Raises ValueError if *role* is not one of OWNER_ROLES or *key* normalizes
    to None (nothing to learn).
    """
    if role not in OWNER_ROLES:
        raise ValueError(f"Invalid owner role {role!r}, must be one of {sorted(OWNER_ROLES)}")
    normalized = normalize_owner_key(key)
    if normalized is None:
        raise ValueError("Cannot learn an owner mapping for an empty/None owner key")
    updated = dict(owner_map)
    updated[normalized] = role
    return updated


# ---------------------------------------------------------------------------
# JSON cache -- learned name/email -> owner role map
# ---------------------------------------------------------------------------

_OWNER_MAP_PATH = Path(__file__).resolve().parent.parent / ".pdf_owner_map.json"


def save_owner_map(owner_map: dict[str, str]) -> None:
    write_pii_json(_OWNER_MAP_PATH, owner_map)


def load_owner_map() -> dict[str, str]:
    if not _OWNER_MAP_PATH.exists():
        return {}
    try:
        raw = read_pii_json(_OWNER_MAP_PATH)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}
```

### Step 1.4: Run test, expect PASS

```
pixi run -e ci test -- tests/test_pdf_owner.py -v
```
Expected: all tests pass.

### Step 1.5: Lint + type-check this file in isolation

```
pixi run -e ci lint
pixi run -e ci type-check
```
Fix any violations before proceeding (CC/cognitive complexity are trivially low here — no action expected).

### Step 1.6: Commit

```
git add engine/pdf_owner.py tests/test_pdf_owner.py .gitignore
git commit -m "feat(ytd): add pdf_owner module -- owner roles + learned name map"
```
(Add the `.gitignore` edit from Task 0 to this same commit: `.pdf_owner_map.json`, `.pdf_import_ledger.json` under "Project-specific".)

**Files:**
- Create: `/home/memento/PycharmProjects/roth_planner/engine/pdf_owner.py`
- Create: `/home/memento/PycharmProjects/roth_planner/tests/test_pdf_owner.py`
- Modify: `/home/memento/PycharmProjects/roth_planner/.gitignore`

---

## Task 2: `engine/pdf_ledger.py` — per-owner contribution ledger (the override-fix)

This is where the override bug is proven fixed at the unit level: derive-sum reproduces today's single-owner behavior and adds correctly for two owners.

### Step 2.1: Write failing tests

Create `tests/test_pdf_ledger.py`:

```python
"""Tests for engine.pdf_ledger -- per-owner PDF contribution ledger.

Proves the override-fix design goal: derive-on-apply reproduces today's exact
single-owner behavior and SUMS across owners for a two-owner scan, replacing
the old single-valued direct-assignment bug (crypto_stcg_ytd = report.stcg).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.koinly_report_pdf import KoinlyReport
from engine.pdf_ledger import (
    derive_brokerage_totals,
    derive_koinly_totals,
    load_ledger,
    save_ledger,
    write_brokerage_contribution,
    write_koinly_contribution,
)


def _koinly(stcg: float, ltcg: float, income: float) -> KoinlyReport:
    return KoinlyReport(
        tax_year=2026,
        crypto_stcg=stcg,
        crypto_ltcg=ltcg,
        crypto_income=income,
        captured_at="2026-07-13T00:00:00+00:00",
    )


def _brokerage(account_number: str, interest: float = 0.0) -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number=account_number,
        broker="schwab",
        account_type="taxable",
        statement_period_end="2026-06-30",
        interest_taxable_ytd=interest,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=0.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-07-13T00:00:00+00:00",
    )


class TestKoinlySingleOwnerMatchesToday:
    """One owner scanned once -> identical to the pre-ledger direct-assignment
    behavior (the non-regression half of the fix)."""

    def test_single_owner_totals_equal_report(self) -> None:
        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", _koinly(100.0, 200.0, 50.0))
        totals = derive_koinly_totals(ledger)
        assert totals == {"stcg": 100.0, "ltcg": 200.0, "income": 50.0}


class TestKoinlyOverrideFixed:
    """The specific bug: scanning spouse's report after yours must ADD, not
    overwrite."""

    def test_two_owner_additive(self) -> None:
        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", _koinly(100.0, 200.0, 50.0))
        ledger = write_koinly_contribution(ledger, "spouse", _koinly(10.0, 20.0, 5.0))
        totals = derive_koinly_totals(ledger)
        assert totals == {"stcg": 110.0, "ltcg": 220.0, "income": 55.0}

    def test_idempotent_rescan_same_owner(self) -> None:
        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", _koinly(100.0, 200.0, 50.0))
        ledger = write_koinly_contribution(ledger, "spouse", _koinly(10.0, 20.0, 5.0))
        # Re-scan "you" with an updated report -- must REPLACE "you"'s slot only.
        ledger = write_koinly_contribution(ledger, "you", _koinly(150.0, 200.0, 50.0))
        totals = derive_koinly_totals(ledger)
        assert totals == {"stcg": 160.0, "ltcg": 220.0, "income": 55.0}

    def test_household_role_is_a_distinct_slot(self) -> None:
        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", _koinly(100.0, 0.0, 0.0))
        ledger = write_koinly_contribution(ledger, "household", _koinly(5.0, 0.0, 0.0))
        totals = derive_koinly_totals(ledger)
        assert totals["stcg"] == pytest.approx(105.0)


class TestKoinlyEmptyLedger:
    def test_empty_ledger_returns_zeros(self) -> None:
        assert derive_koinly_totals({}) == {"stcg": 0.0, "ltcg": 0.0, "income": 0.0}


class TestBrokerageAdditive:
    def test_single_owner_single_account(self) -> None:
        ledger: dict = {}
        ledger = write_brokerage_contribution(ledger, "you", _brokerage("111", interest=10.0))
        totals = derive_brokerage_totals(ledger)
        assert totals["interest_ytd"] == pytest.approx(10.0)

    def test_two_owners_different_accounts_additive(self) -> None:
        ledger: dict = {}
        ledger = write_brokerage_contribution(ledger, "you", _brokerage("111", interest=10.0))
        ledger = write_brokerage_contribution(ledger, "spouse", _brokerage("222", interest=20.0))
        totals = derive_brokerage_totals(ledger)
        assert totals["interest_ytd"] == pytest.approx(30.0)

    def test_rescan_same_owner_same_account_replaces(self) -> None:
        ledger: dict = {}
        ledger = write_brokerage_contribution(ledger, "you", _brokerage("111", interest=10.0))
        ledger = write_brokerage_contribution(ledger, "you", _brokerage("111", interest=15.0))
        totals = derive_brokerage_totals(ledger)
        assert totals["interest_ytd"] == pytest.approx(15.0)


class TestLedgerCache:
    def test_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_ledger as mod

        monkeypatch.setattr(mod, "_LEDGER_PATH", tmp_path / ".pdf_import_ledger.json")
        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", _koinly(100.0, 0.0, 0.0))
        save_ledger(ledger)
        loaded = load_ledger()
        assert derive_koinly_totals(loaded) == {"stcg": 100.0, "ltcg": 0.0, "income": 0.0}

    def test_load_missing_returns_empty_ledger(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_ledger as mod

        monkeypatch.setattr(mod, "_LEDGER_PATH", tmp_path / "nope.json")
        assert load_ledger() == {"koinly": {}, "brokerage": {}}

    def test_load_corrupt_returns_empty_ledger(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_ledger as mod

        bad = tmp_path / ".pdf_import_ledger.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_LEDGER_PATH", bad)
        assert load_ledger() == {"koinly": {}, "brokerage": {}}

    def test_load_predates_ledger_defaults_missing_doc_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hand-crafted / partially-migrated ledger missing the 'brokerage'
        key must default it to {} rather than KeyError."""
        import engine.pdf_ledger as mod

        partial = tmp_path / ".pdf_import_ledger.json"
        partial.write_text('{"koinly": {}}')
        monkeypatch.setattr(mod, "_LEDGER_PATH", partial)
        loaded = load_ledger()
        assert loaded == {"koinly": {}, "brokerage": {}}
```

### Step 2.2: Run test, expect FAIL

```
pixi run -e ci test -- tests/test_pdf_ledger.py -v
```
Expected: `ModuleNotFoundError: No module named 'engine.pdf_ledger'`.

### Step 2.3: Implement `engine/pdf_ledger.py`

```python
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

import json
from pathlib import Path
from typing import Any

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.koinly_report_pdf import KoinlyReport
from engine.secure_io import read_pii_json, write_pii_json

# Ledger shape:
# {
#   "koinly": {"<owner>": {"stcg": float, "ltcg": float, "income": float,
#                            "captured_at": str, "source": str}},
#   "brokerage": {"<owner>": {"<account_number>": {...record.to_dict()...}}},
# }
PdfLedger = dict[str, dict[str, Any]]

_EMPTY_LEDGER: PdfLedger = {"koinly": {}, "brokerage": {}}


def write_koinly_contribution(ledger: PdfLedger, owner: str, report: KoinlyReport) -> PdfLedger:
    """Return a NEW ledger with *owner*'s Koinly slot set to *report*'s figures.

    Re-writing the same owner replaces that owner's prior contribution
    (idempotent re-scan); a different owner's slot is untouched (additive
    across owners).
    """
    updated: PdfLedger = {
        "koinly": dict(ledger.get("koinly", {})),
        "brokerage": dict(ledger.get("brokerage", {})),
    }
    updated["koinly"][owner] = {
        "stcg": float(report.crypto_stcg),
        "ltcg": float(report.crypto_ltcg),
        "income": float(report.crypto_income),
        "captured_at": report.captured_at,
        "source": report.source,
    }
    return updated


def write_brokerage_contribution(
    ledger: PdfLedger, owner: str, record: BrokerageStatementRecord
) -> PdfLedger:
    """Return a NEW ledger with *record* written into *owner*'s brokerage
    slot, keyed by account_number.

    Re-writing the same (owner, account_number) pair replaces that slot
    (idempotent re-scan, mirrors pick_latest_per_account); a different owner
    or a different account_number is a separate, additive slot.
    """
    updated: PdfLedger = {
        "koinly": dict(ledger.get("koinly", {})),
        "brokerage": {k: dict(v) for k, v in ledger.get("brokerage", {}).items()},
    }
    owner_accounts = dict(updated["brokerage"].get(owner, {}))
    owner_accounts[record.account_number] = record.to_dict()
    updated["brokerage"][owner] = owner_accounts
    return updated


def derive_koinly_totals(ledger: PdfLedger) -> dict[str, float]:
    """Sum Koinly stcg/ltcg/income across every owner slot in the ledger.

    Empty ledger -> all zeros. Single owner -> identical to that owner's raw
    report values (non-regression). Multiple owners -> summed (the fix).
    """
    by_owner = ledger.get("koinly", {})
    return {
        "stcg": sum(float(v.get("stcg", 0.0)) for v in by_owner.values()),
        "ltcg": sum(float(v.get("ltcg", 0.0)) for v in by_owner.values()),
        "income": sum(float(v.get("income", 0.0)) for v in by_owner.values()),
    }


def derive_brokerage_totals(ledger: PdfLedger) -> dict[str, float]:
    """Sum brokerage-derived YTD fields across every (owner, account_number)
    slot in the ledger, using the same field mapping as
    engine.brokerage_statement_pdf.aggregate_to_ytd_fields.

    Deliberately re-implemented here (not delegating to
    aggregate_to_ytd_fields) because that function takes a flat
    dict[account_number, record]; the ledger is owner-scoped
    dict[owner, dict[account_number, record_dict]], so the flattening step
    (across owners) belongs in this module.
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
            totals["interest_ytd"] += float(rec_dict.get("interest_taxable_ytd", 0.0))
            totals["tax_exempt_interest_ytd"] += float(
                rec_dict.get("interest_tax_exempt_ytd", 0.0)
            ) + float(rec_dict.get("dividends_tax_exempt_ytd", 0.0))
            totals["ordinary_dividends_ytd"] += float(rec_dict.get("dividends_taxable_ytd", 0.0))
            totals["stcg_ytd"] += float(rec_dict.get("stcg_net_ytd", 0.0))
            totals["ltcg_ytd"] += float(rec_dict.get("ltcg_net_ytd", 0.0))
    return totals


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
```

### Step 2.4: Run test, expect PASS

```
pixi run -e ci test -- tests/test_pdf_ledger.py -v
```
Expected: all tests pass, including `test_two_owner_additive` and `test_idempotent_rescan_same_owner` (the override-fix proof).

### Step 2.5: Lint + type-check

```
pixi run -e ci lint
pixi run -e ci type-check
```

### Step 2.6: Commit

```
git add engine/pdf_ledger.py tests/test_pdf_ledger.py
git commit -m "feat(ytd): add pdf_ledger -- per-owner contribution store, proves override-fix"
```

**Files:**
- Create: `/home/memento/PycharmProjects/roth_planner/engine/pdf_ledger.py`
- Create: `/home/memento/PycharmProjects/roth_planner/tests/test_pdf_ledger.py`

---

## Task 3: `engine/koinly_report_pdf.py` — owner-key extraction

TODO(verify): the design spec says Koinly prints "the account name/email... on the report" but the two real fixture pages read (`_CG_PAGE`, `_INCOME_PAGE` in `tests/test_koinly_report_pdf.py`) do not show a name/email anywhere in the captured text, and the real sample PDF at `PDF-Statements/koinly_2026_complete_tax_report_July.pdf` was not inspected page-by-page for this plan (only the two summary pages were previously anchored). **The executor MUST inspect additional pages of the real sample PDF (e.g. page 1 / cover page) via `pdfplumber` before finalizing the extraction regex** — the pattern below is a reasonable placeholder (common report cover-page phrasing) but is UNVERIFIED against the real document. If no such line exists in the real report, `extract_owner_key` correctly returns `None` and the fallback (manual role pick) is the intended path per the design's Non-Goals/Fallback section — this is an acceptable outcome, not a blocker.

### Step 3.1: Write failing tests

Add to `tests/test_koinly_report_pdf.py`:

```python
from engine.koinly_report_pdf import extract_owner_key


class TestExtractOwnerKey:
    def test_extracts_name_from_cover_page(self) -> None:
        cover = "Complete Tax Report\nPrepared for Claude R Cirba\nTAX YEAR 2026\n"
        assert extract_owner_key([cover, _CG_PAGE, _INCOME_PAGE]) == "Claude R Cirba"

    def test_extracts_email_when_no_name_line(self) -> None:
        cover = "Complete Tax Report\nclaude.cirba@example.com\nTAX YEAR 2026\n"
        assert extract_owner_key([cover, _CG_PAGE, _INCOME_PAGE]) == "claude.cirba@example.com"

    def test_returns_none_when_absent(self) -> None:
        assert extract_owner_key([_CG_PAGE, _INCOME_PAGE]) is None
```

### Step 3.2: Run test, expect FAIL

```
pixi run -e ci test -- tests/test_koinly_report_pdf.py::TestExtractOwnerKey -v
```
Expected: `ImportError: cannot import name 'extract_owner_key'`.

### Step 3.3: Implement

Add to `engine/koinly_report_pdf.py` (near `parse_koinly_text`), plus extend `KoinlyReport`:

```python
_OWNER_NAME_RE = re.compile(r"Prepared for\s+(.+)", re.IGNORECASE)
_OWNER_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def extract_owner_key(pages: list[str]) -> str | None:
    """Best-effort extraction of an owner-identifying string (name or email)
    from a Koinly report's cover/header text.

    TODO(verify): the "Prepared for <name>" anchor is UNVERIFIED against a
    real Koinly report cover page -- confirm against
    PDF-Statements/koinly_2026_complete_tax_report_July.pdf and adjust the
    regex before relying on this in production. Returns None (never guesses)
    when no name or email pattern is found, matching the design's documented
    fallback to manual owner selection in the UI.
    """
    full_text = "\n".join(pages)
    name_m = _OWNER_NAME_RE.search(full_text)
    if name_m:
        return name_m.group(1).strip().splitlines()[0].strip()
    email_m = _OWNER_EMAIL_RE.search(full_text)
    if email_m:
        return email_m.group(0)
    return None
```

Update `KoinlyReport` dataclass — add field `owner_key: str | None = None` (after `provenance`), and thread it through `to_dict`/`from_dict`:

```python
    owner_key: str | None = None
```
```python
    def to_dict(self) -> dict[str, Any]:
        return {
            ...,
            "provenance": self.provenance,
            "owner_key": self.owner_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KoinlyReport:
        return cls(
            ...,
            provenance=dict(data.get("provenance", {})),
            owner_key=data.get("owner_key"),
        )
```

Wire into `parse_koinly_text` (set `owner_key=extract_owner_key(pages)` in the returned `KoinlyReport(...)` call).

### Step 3.4: Run test, expect PASS

```
pixi run -e ci test -- tests/test_koinly_report_pdf.py -v
```
Expected: all tests pass (existing + 3 new). Verify `test_round_trip` in `TestKoinlyCache` still passes unmodified (owner_key defaults to `None`, backward compatible).

### Step 3.5: Lint + type-check; run full Koinly test file once more

```
pixi run -e ci lint
pixi run -e ci type-check
pixi run -e ci test -- tests/test_koinly_report_pdf.py -v
```

### Step 3.6: Commit

```
git add engine/koinly_report_pdf.py tests/test_koinly_report_pdf.py
git commit -m "feat(ytd): extract owner key from Koinly report cover page"
```

**Files:**
- Modify: `/home/memento/PycharmProjects/roth_planner/engine/koinly_report_pdf.py`
- Modify: `/home/memento/PycharmProjects/roth_planner/tests/test_koinly_report_pdf.py`

---

## Task 4: `engine/brokerage_statement_pdf.py` — owner-key extraction per broker

### Step 4.1: Write failing tests

Add to `tests/test_brokerage_statement_pdf.py` (uses the real fixture constants already in the file: `SCHWAB_PAGE_TEXT`, `VANGUARD_TAXABLE_OVERVIEW_TEXT`):

```python
from engine.brokerage_statement_pdf import extract_owner_key


class TestExtractOwnerKeySchwab:
    def test_extracts_account_holder_name(self) -> None:
        # Schwab's extract_text() strips spaces from labels but NOT from the
        # holder's own name line ("CLAUDECIRBA" in the real dump has no space
        # because Schwab renders it as one run -- confirmed in SCHWAB_PAGE_TEXT).
        assert extract_owner_key(SCHWAB_PAGE_TEXT) == "CLAUDECIRBA"


class TestExtractOwnerKeyVanguard:
    def test_extracts_account_holder_name(self) -> None:
        assert extract_owner_key(VANGUARD_TAXABLE_OVERVIEW_TEXT) == "Claude R Cirba"


class TestExtractOwnerKeyAbsent:
    def test_returns_none_when_no_name_found(self) -> None:
        assert extract_owner_key("Some Broker Statement\nNo holder name here\n") is None
```

TODO(verify): the Schwab holder-name anchor (`CLAUDECIRBA` directly after the `AccountNumber StatementPeriod` header line, per `SCHWAB_PAGE_TEXT` line 26) is inferred from the one captured fixture in this repo — confirm it generalizes (e.g. a joint account's "AND" concatenation, or a longer/hyphenated name) against additional real Schwab dumps before trusting it broadly. If it does not generalize, `extract_owner_key` returning `None` is an acceptable degraded outcome (manual fallback), not a hard requirement.

### Step 4.2: Run test, expect FAIL

```
pixi run -e ci test -- tests/test_brokerage_statement_pdf.py::TestExtractOwnerKeySchwab -v
```
Expected: `ImportError: cannot import name 'extract_owner_key'`.

### Step 4.3: Implement

Add to `engine/brokerage_statement_pdf.py`, near `detect_broker`:

```python
# --- Owner-key extraction (account-holder name) -----------------------------
#
# Best-effort per-broker holder-name extraction for the owner-attribution
# ledger (see engine/pdf_owner.py, engine/pdf_ledger.py). Returns None (never
# guesses) when no recognizable holder-name line is found -- the caller falls
# back to manual owner selection in the UI, same safety rule as account_type
# "unknown".

_SCHWAB_HOLDER_NAME_RE = re.compile(
    r"AccountNumber StatementPeriod\s*\n([A-Z]+)\n", re.MULTILINE
)
_VANGUARD_HOLDER_NAME_RE = re.compile(
    r"account—XXXX\d+ Vanguard Personal Investor\s*\n(.+?)\s+\d{3}-\d{3}-\d{4}"
)


def extract_owner_key(full_text: str) -> str | None:
    """Best-effort extraction of the account-holder's name from statement
    text, for owner attribution. Dispatches by detected broker; returns None
    (never guesses) if no recognized broker or no holder-name line matches.

    TODO(verify): only Schwab's and Vanguard's single-holder patterns are
    covered against the real fixtures captured in this repo. IBKR and
    Fidelity's per-account "Account Information" sections were not inspected
    for a holder-name line as part of this plan -- extend
    _detect_ibkr_account / _detect_fidelity_account-style anchors if needed;
    until then, IBKR/Fidelity records fall back to owner_key=None (manual
    confirmation in the UI), which is an acceptable degraded outcome.
    """
    broker = detect_broker(full_text)
    if broker == "schwab":
        m = _SCHWAB_HOLDER_NAME_RE.search(full_text)
        return m.group(1) if m else None
    if broker == "vanguard":
        m = _VANGUARD_HOLDER_NAME_RE.search(full_text)
        return m.group(1).strip() if m else None
    return None
```

Update `BrokerageStatementRecord` dataclass — add field `owner_key: str | None = None` (after `provenance`), thread through `to_dict`/`from_dict` (same pattern as Task 3's `KoinlyReport` edit). Wire into `_parse_schwab` and `_parse_vanguard` (pass `owner_key=extract_owner_key(full_text)` into the returned `BrokerageStatementRecord(...)`); IBKR/Fidelity records pass `owner_key=None` explicitly for now (documented gap above).

### Step 4.4: Run test, expect PASS

```
pixi run -e ci test -- tests/test_brokerage_statement_pdf.py -v
```
Expected: all tests pass (existing + 3 new).

### Step 4.5: Lint + type-check

```
pixi run -e ci lint
pixi run -e ci type-check
```

### Step 4.6: Commit

```
git add engine/brokerage_statement_pdf.py tests/test_brokerage_statement_pdf.py
git commit -m "feat(ytd): extract account-holder owner key from Schwab/Vanguard statements"
```

**Files:**
- Modify: `/home/memento/PycharmProjects/roth_planner/engine/brokerage_statement_pdf.py`
- Modify: `/home/memento/PycharmProjects/roth_planner/tests/test_brokerage_statement_pdf.py`

---

## Task 5: `engine/pdf_import.py` — multi-owner Koinly survival in one scan

Change `PdfImportResult.koinly_report: KoinlyReport | None` to `koinly_reports: list[KoinlyReport]` and remove the single-newest-by-mtime collapse in `scan_pdf_folder`.

### Step 5.1: Write failing tests

Update `tests/test_pdf_import.py`:

1. Change `test_scan_routes_each_type` assertion from
   `assert result.koinly_report is not None`
   to
   `assert len(result.koinly_reports) == 1`.
2. Change `test_scan_collects_parse_errors_without_aborting` assertion from
   `assert result.koinly_report is not None`
   to
   `assert len(result.koinly_reports) == 1`.
3. Replace `test_newest_koinly_wins` with a new test proving BOTH survive:

```python
def test_multiple_koinly_reports_all_survive_scan(tmp_path, monkeypatch):
    """Two owners' Koinly PDFs in one folder must BOTH appear in the result --
    this is the pdf_import half of the override-fix (engine/pdf_ledger.py
    proves the derive-sum half). No mtime-based collapse to a single winner."""
    monkeypatch.setattr(
        pdf_import, "extract_pages", lambda data: (data.decode("utf-8").split("\f"), None)
    )
    monkeypatch.setattr(pdf_import, "parse_koinly_text", lambda pages: _FakeKoinly(pages[0]))
    _write(tmp_path, "you.pdf", "Koinly YOU")
    _write(tmp_path, "spouse.pdf", "Koinly SPOUSE")

    result = scan_pdf_folder(tmp_path)

    tags = {r.tag for r in result.koinly_reports}
    assert tags == {"Koinly YOU", "Koinly SPOUSE"}
```

### Step 5.2: Run test, expect FAIL

```
pixi run -e ci test -- tests/test_pdf_import.py -v
```
Expected: `AttributeError: 'PdfImportResult' object has no attribute 'koinly_reports'`.

### Step 5.3: Implement

In `engine/pdf_import.py`:

```python
@dataclass
class PdfImportResult:
    """Aggregated outcome of scanning a shared folder of mixed PDFs."""

    brokerage_records: list[BrokerageStatementRecord] = field(default_factory=list)
    koinly_reports: list[KoinlyReport] = field(default_factory=list)
    form_1040_records: dict[int, Form1040Record] = field(default_factory=dict)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    unrecognized: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
```

In `scan_pdf_folder`, remove the `koinly_candidates` mtime-collapse machinery entirely and append directly:

```python
def scan_pdf_folder(folder: Path) -> PdfImportResult:
    """... (docstring updated: "every Koinly report found is kept -- owner
    attribution and any per-owner dedup happens downstream in
    engine/pdf_ledger.py, not here") ...
    """
    result = PdfImportResult()

    for pdf_path in sorted(folder.glob("*.[pP][dD][fF]")):
        name = pdf_path.name
        try:
            pages, creator = extract_pages(pdf_path.read_bytes())
        except Exception as exc:  # noqa: BLE001
            result.errors.append((name, f"could not read PDF: {exc}"))
            continue

        kind = classify_pdf_text(pages)
        try:
            if kind is DocKind.BROKERAGE:
                result.brokerage_records.extend(parse_statement_text(pages))
            elif kind is DocKind.KOINLY:
                result.koinly_reports.append(parse_koinly_text(pages))
            elif kind is DocKind.FORM_1040:
                rec = parse_form_1040_text(pages, pdf_creator=creator)
                result.form_1040_records[rec.tax_year] = rec
            elif kind is DocKind.EXTENSION:
                result.skipped.append((name, "Form 4868 extension — no importable data"))
            else:
                result.unrecognized.append(name)
        except Exception as exc:  # noqa: BLE001
            result.errors.append((name, str(exc)))

    return result
```

Remove the now-unused `koinly_candidates` list and its sort block.

### Step 5.4: Run test, expect PASS

```
pixi run -e ci test -- tests/test_pdf_import.py -v
```
Expected: all tests pass.

### Step 5.5: Check downstream break — `views/ytd_income.py` still references `result.koinly_report` (singular)

This WILL break `views/ytd_income.py` (Task 6 fixes it) and `tests/test_views_ytd_income.py` if any test constructs `PdfImportResult(koinly_report=...)`. Grep to confirm scope before moving on:

```
grep -rn "koinly_report\b" views/ tests/test_views_ytd_income.py
```

Do NOT fix `views/ytd_income.py` in this task — that is Task 6's job specifically, so this task's commit stays isolated to the engine-layer shape change. It is acceptable for the view to be broken between this commit and Task 6's commit ONLY if they land in the same PR before merge to `development`; if executing task-by-task with intermediate pushes, note this ordering dependency.

### Step 5.6: Lint + type-check

```
pixi run -e ci lint
pixi run -e ci type-check
```
mypy will likely flag `views/ytd_income.py`'s `result.koinly_report` access as an attribute error on `PdfImportResult` — expected until Task 6. If the type-check gate is run standalone here, note the known-broken view file rather than attempting a premature fix.

### Step 5.7: Commit

```
git add engine/pdf_import.py tests/test_pdf_import.py
git commit -m "feat(ytd): PdfImportResult.koinly_reports (list) -- no more single-newest collapse"
```

**Files:**
- Modify: `/home/memento/PycharmProjects/roth_planner/engine/pdf_import.py`
- Modify: `/home/memento/PycharmProjects/roth_planner/tests/test_pdf_import.py`

---

## Task 6: `views/ytd_income.py` — wire owner resolution, ledger writes, derive-sum

This is the view-layer fix locus identified in the design spec (§Problem): the direct-assignment blocks (`_snap.crypto_stcg_ytd = float(result.koinly_report.crypto_stcg)` at line 191-193, and the `koinly_report` "Apply to crypto fields below" button at line 320-322) become ledger-writes + derive-sum reads.

`tests/test_views_ytd_income.py` mocks `st` wholesale (see `_make_mock_st` helper) and patches `ytd_income_mod.st`; it does not use a traditional pytest fixture seam for the PDF-scan button flow specifically (that flow is gated behind `is_pyodide()` returning False and a `st.button` click, neither of which the existing NQO-focused tests exercise). Follow that same `_make_mock_st` + `patch.object(ytd_income_mod, "st", mock_st)` pattern; additionally patch `is_pyodide` to return `False` and drive `mock_st.button` / `mock_st.text_input` return values to simulate a "Scan folder" click, and patch `engine.pdf_import.scan_pdf_folder` to return a canned `PdfImportResult`.

### Step 6.1: Write failing tests

Add to `tests/test_views_ytd_income.py`:

```python
from unittest.mock import patch

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.koinly_report_pdf import KoinlyReport
from engine.pdf_import import PdfImportResult


def _koinly_report(owner_key: str | None, stcg: float, ltcg: float, income: float) -> KoinlyReport:
    return KoinlyReport(
        tax_year=2026,
        crypto_stcg=stcg,
        crypto_ltcg=ltcg,
        crypto_income=income,
        captured_at="2026-07-13T00:00:00+00:00",
        owner_key=owner_key,
    )


class TestOwnerAttributionScanFlow:
    """Regression coverage for the Koinly override bug (docs/superpowers/specs/
    2026-07-13-spouse-pdf-owner-attribution-design.md): scanning a second
    owner's Koinly report must ADD to crypto_stcg_ytd/crypto_ltcg_ytd/
    crypto_income_ytd, not overwrite them."""

    def _run_scan(self, hh, mock_st, canned_result, ledger_path, owner_map_path, tmp_path):
        import engine.pdf_ledger as ledger_mod
        import engine.pdf_owner as owner_mod

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch.object(ytd_income_mod, "is_pyodide", return_value=False),
            patch("engine.pdf_import.scan_pdf_folder", return_value=canned_result),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
            patch.object(ledger_mod, "_LEDGER_PATH", ledger_path),
            patch.object(owner_mod, "_OWNER_MAP_PATH", owner_map_path),
        ):
            ytd_income_mod.render(hh)

    def test_two_owner_koinly_scan_sums_not_overrides(self, tmp_path):
        """Core regression: scan 'you' Koinly, then scan 'spouse' Koinly in a
        SEPARATE render call -- final crypto_*_ytd must be the SUM, matching
        the design's derive-sum contract, not the second report's raw value."""
        hh = _stub_hh()

        # First render: "you" scans a Koinly report.
        ytd1 = YTDSnapshot()
        mock_st1 = _make_mock_st(ytd1)
        mock_st1.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st1.text_input.return_value = str(tmp_path)
        mock_st1.selectbox.return_value = "you"
        result1 = PdfImportResult(koinly_reports=[_koinly_report("claude r cirba", 100.0, 200.0, 50.0)])
        self._run_scan(
            hh, mock_st1, result1,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json", tmp_path,
        )

        # Second render: "spouse" scans a separate Koinly report. Ledger/owner
        # map persist on disk between renders (same tmp_path), same as two
        # separate Streamlit sessions on the same machine.
        ytd2 = YTDSnapshot()
        mock_st2 = _make_mock_st(ytd2)
        mock_st2.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st2.text_input.return_value = str(tmp_path)
        mock_st2.selectbox.return_value = "spouse"
        result2 = PdfImportResult(koinly_reports=[_koinly_report("jane r cirba", 10.0, 20.0, 5.0)])
        self._run_scan(
            hh, mock_st2, result2,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json", tmp_path,
        )

        # Read back via direct attribute access to match test file's established pattern
        final_snap = mock_st2.session_state.ytd_snapshot
        assert final_snap.crypto_stcg_ytd == pytest.approx(110.0)
        assert final_snap.crypto_ltcg_ytd == pytest.approx(220.0)
        assert final_snap.crypto_income_ytd == pytest.approx(55.0)

    def test_idempotent_rescan_same_owner_unchanged_total(self, tmp_path):
        hh = _stub_hh()
        ytd1 = YTDSnapshot()
        mock_st1 = _make_mock_st(ytd1)
        mock_st1.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st1.text_input.return_value = str(tmp_path)
        mock_st1.selectbox.return_value = "you"
        result = PdfImportResult(koinly_reports=[_koinly_report("claude r cirba", 100.0, 200.0, 50.0)])
        self._run_scan(
            hh, mock_st1, result,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json", tmp_path,
        )
        ytd2 = YTDSnapshot()
        mock_st2 = _make_mock_st(ytd2)
        mock_st2.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st2.text_input.return_value = str(tmp_path)
        mock_st2.selectbox.return_value = "you"
        self._run_scan(
            hh, mock_st2, result,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json", tmp_path,
        )
        # Read back via direct attribute access to match test file's established pattern
        final_snap = mock_st2.session_state.ytd_snapshot
        assert final_snap.crypto_stcg_ytd == pytest.approx(100.0)

    def test_no_owner_key_falls_back_to_manual_selectbox(self, tmp_path):
        """A Koinly report with owner_key=None must not silently apply --
        the UI's manual role selectbox must be consulted."""
        hh = _stub_hh()
        ytd1 = YTDSnapshot()
        mock_st1 = _make_mock_st(ytd1)
        mock_st1.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st1.text_input.return_value = str(tmp_path)
        mock_st1.selectbox.return_value = "household"
        result = PdfImportResult(koinly_reports=[_koinly_report(None, 100.0, 200.0, 50.0)])
        self._run_scan(
            hh, mock_st1, result,
            tmp_path / ".pdf_import_ledger.json", tmp_path / ".pdf_owner_map.json", tmp_path,
        )
        # Manual role selectbox must have been invoked with the owner options.
        selectbox_calls = mock_st1.selectbox.call_args_list
        assert any(
            "you" in (c.args[1] if len(c.args) > 1 else c.kwargs.get("options", []))
            for c in selectbox_calls
        )
```

TODO(verify): the exact `st.selectbox` call signature/ordering the executor writes in Step 6.3 must line up with what these tests assert (`mock_st.selectbox.return_value = "you"` assumes a single relevant selectbox call per render in the happy-path tests — if the implementation adds multiple selectboxes per scan, e.g. one per unresolved document, tighten the mock with `side_effect` keyed by call order or by inspecting `call.kwargs["key"]`). Adjust test mocking to match the real widget layout chosen in Step 6.3; the assertions on final `crypto_*_ytd` sums are the load-bearing part of this test, not the exact selectbox plumbing.

### Step 6.2: Run test, expect FAIL

```
pixi run -e ci test -- tests/test_views_ytd_income.py::TestOwnerAttributionScanFlow -v
```
Expected: `AttributeError: 'PdfImportResult' object has no attribute 'koinly_report'` (view still reads the old singular field) or a ledger-derive assertion failure, since the view has not been wired yet.

### Step 6.3: Implement

In `views/ytd_income.py`, replace the Koinly-application block (lines ~186-194) and the "Apply to crypto fields below" button block (lines ~316-326). Add imports at the top of the scan-button branch:

```python
from engine.pdf_ledger import (
    derive_koinly_totals,
    load_ledger,
    save_ledger,
    write_koinly_contribution,
)
from engine.pdf_owner import (
    OWNER_ROLES,
    learn_owner,
    load_owner_map,
    resolve_owner,
    save_owner_map,
)
```

Replace the Koinly block inside the "Scan folder" branch:

```python
                owner_map = load_owner_map()
                ledger = load_ledger()

                for report in result.koinly_reports:
                    resolved = resolve_owner(report.owner_key, owner_map)
                    if resolved is None:
                        st.warning(
                            f"Koinly report {report.tax_year} has no recognized owner "
                            f"({report.owner_key!r}) — confirm whose it is:"
                        )
                        resolved = st.selectbox(
                            f"Owner for Koinly report ({report.owner_key or 'unknown'})",
                            sorted(OWNER_ROLES),
                            key=f"koinly_owner_confirm_{report.captured_at}",
                        )
                    elif report.owner_key is not None:
                        # Auto-resolved -- still show a correction control.
                        corrected = st.selectbox(
                            f"Owner (auto-resolved: {resolved})",
                            sorted(OWNER_ROLES),
                            index=sorted(OWNER_ROLES).index(resolved),
                            key=f"koinly_owner_correct_{report.captured_at}",
                        )
                        if corrected != resolved:
                            owner_map = learn_owner(report.owner_key, corrected, owner_map)
                            resolved = corrected
                    ledger = write_koinly_contribution(ledger, resolved, report)

                save_ledger(ledger)
                save_owner_map(owner_map)

                koinly_totals = derive_koinly_totals(ledger)
                if result.koinly_reports:
                    _snap.crypto_stcg_ytd = koinly_totals["stcg"]
                    _snap.crypto_ltcg_ytd = koinly_totals["ltcg"]
                    _snap.crypto_income_ytd = koinly_totals["income"]
                    applied_bits.append(
                        f"Koinly crypto ({len(result.koinly_reports)} report(s), "
                        f"{len(ledger['koinly'])} owner(s))"
                    )
```

Remove the old block:
```python
                if result.koinly_report is not None:
                    from engine.koinly_report_pdf import save_koinly_report

                    st.session_state["koinly_report"] = result.koinly_report
                    save_koinly_report(result.koinly_report)
                    _snap.crypto_stcg_ytd = float(result.koinly_report.crypto_stcg)
                    _snap.crypto_ltcg_ytd = float(result.koinly_report.crypto_ltcg)
                    _snap.crypto_income_ytd = float(result.koinly_report.crypto_income)
                    applied_bits.append(f"Koinly {result.koinly_report.tax_year} crypto")
```

Update the "parsed_bits" summary reference from `result.koinly_report is not None` to `result.koinly_reports` (truthy list check), and its label from `f"Koinly {result.koinly_report.tax_year}"` to a report-count summary, e.g. `f"Koinly ({len(result.koinly_reports)} report(s))"`.

Update the standalone "Apply to crypto fields below" button block (the cached single-`koinly_report` display/apply section) similarly — TODO(verify): this cached-session-state single-report display (`st.session_state.get("koinly_report")`, loaded via `load_koinly_report()`) predates the ledger and shows only the LAST scanned report; decide whether to (a) keep it as a "last scanned" convenience display only (no apply button, since apply now always goes through the ledger during scan), or (b) remove it entirely in favor of the new per-owner breakdown expander below. Recommended: **(a)**, converting the "Apply to crypto fields below" button into a read-only display, since the design spec's ledger derive-sum makes a separate manual "apply" step redundant and a source of double-count risk if a stale cached report gets re-applied after ledger totals have already summed newer scans.

Add a per-owner breakdown expander (near the existing Koinly display block, ~line 306-312):

```python
        if ledger.get("koinly"):
            with st.expander("Per-owner crypto breakdown"):
                for owner, figures in sorted(ledger["koinly"].items()):
                    st.caption(
                        f"{owner.title()}: STCG {fmt_dollars(figures['stcg'])}, "
                        f"LTCG {fmt_dollars(figures['ltcg'])}, "
                        f"Income {fmt_dollars(figures['income'])}"
                    )
```

TODO(verify): `ledger` must be loaded once near the top of the non-Pyodide branch (via `load_ledger()`) so it is in scope for both the scan-button block and this display block outside the `if st.button(...)` conditional — the exact variable threading depends on final control-flow placement the executor chooses; ensure `ledger` reflects the on-disk state even on renders where "Scan folder" was NOT clicked this run (i.e. call `load_ledger()` unconditionally once per render, not only inside the button branch).

### Step 6.4: Run test, expect PASS

```
pixi run -e ci test -- tests/test_views_ytd_income.py -v
```
Expected: all tests pass, including the 3 new `TestOwnerAttributionScanFlow` cases and all pre-existing NQO smoke tests (no regression).

### Step 6.5: Full test suite + lint + type-check

```
pixi run -e ci test
pixi run -e ci lint
pixi run -e ci type-check
```
Fix any remaining fallout (e.g. other tests that constructed `PdfImportResult(koinly_report=...)` outside the files already touched — grep first: `grep -rn "koinly_report=" tests/`).

### Step 6.6: Commit

```
git add views/ytd_income.py tests/test_views_ytd_income.py
git commit -m "feat(ytd): wire owner attribution into YTD scan flow; fix Koinly override bug"
```

**Files:**
- Modify: `/home/memento/PycharmProjects/roth_planner/views/ytd_income.py`
- Modify: `/home/memento/PycharmProjects/roth_planner/tests/test_views_ytd_income.py`

---

## Task 7: Final verification pass

- [ ] Full suite green: `pixi run -e ci test`
- [ ] Lint clean: `pixi run -e ci lint`
- [ ] Type-check clean: `pixi run -e ci type-check`
- [ ] Confirm `git status` shows no new untracked cache files (`.pdf_owner_map.json`, `.pdf_import_ledger.json` must be gitignored, not staged) — re-check after any manual local testing that exercises the real scan flow.
- [ ] Re-read the design spec's Non-Goals section once more: confirm no engine tax-math file (`engine/headroom.py`, `engine/tax.py`, `engine/irmaa.py`, `engine/niit.py`) was touched.
- [ ] Update `docs/superpowers/plans/` cross-reference in project MEMORY.md if the user's workflow expects it (check existing pattern from PR #357-#360 plans first — do not invent a new logging convention).

**Files:** none (verification only).

---

## Summary of TODO(verify) markers left in this plan

1. **Task 3** — Koinly `extract_owner_key` "Prepared for" regex is UNVERIFIED against the real sample PDF; executor must inspect `PDF-Statements/koinly_2026_complete_tax_report_July.pdf` cover page before trusting it.
2. **Task 4** — Schwab/Vanguard `extract_owner_key` anchors are inferred from the single captured fixture per broker; IBKR/Fidelity owner-key extraction is explicitly out of scope (falls back to `None` -> manual UI confirmation).
3. **Task 6** — exact `st.selectbox` call layout/ordering for owner confirm/correct controls must be finalized during implementation and the test mocks in Step 6.1 adjusted to match; the cached single-report "Apply to crypto fields below" block's fate (read-only display vs. removal) is a judgment call flagged for the executor; `ledger` variable scoping (loaded once per render vs. only inside the button branch) needs confirmation during implementation.
