# Consolidated single-`.enc` Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the two sealed `.enc` exports into one versioned bundle and make importing it an authoritative full-replace of the chosen owner slot (accounts + per-owner ledger), so stale data resets and re-imports never double.

**Architecture:** A new pure engine module `engine/bridge_bundle.py` owns bundle build/parse/apply (no Streamlit). New owner-slice helpers in `engine/pdf_ledger.py` extract/replace one owner's ledger entries. The Streamlit view `views/setup/data_bridge.py` becomes thin wiring: gather → seal → one download on export; unseal → apply → persist → re-derive on import. Local `.json` caches keep their current shapes. Grants stay local (not transferred).

**Tech Stack:** Python 3.12, Streamlit, dataclasses, PyNaCl sealed-box crypto (`engine/data_bridge_crypto.py`), pytest (per-module test files, `monkeypatch` + `tmp_path` for cache path constants), pixi (`pixi run -e ci test`). Git via the repo's MCP git workflow (GPG-signed commits; branch `feat/enc-bridge-consolidation`; PR to `development`).

**Spec:** `docs/superpowers/specs/2026-07-13-enc-bridge-consolidation-design.md`

---

## File Structure

- `engine/pdf_ledger.py` (modify) — add `extract_owner(ledger, owner)` and `replace_owner(ledger, owner, slice)`; pure, return new dicts.
- `engine/bridge_bundle.py` (create) — `BUNDLE_FORMAT_VERSION`, `build_bundle(...)`, `read_format_version(raw)`, `apply_bundle(...)`. Pure; no Streamlit imports.
- `views/setup/data_bridge.py` (modify) — single-file export in `_handle_personal_exports`; full-replace import in the upload handler; legacy-payload notice; YTD re-derive after import.
- `views/setup/_state.py` (modify, minimal) — ensure `_clear_personal_session_state` behavior is compatible; reuse `_user_defaults_from_session` / `_apply_user_defaults_to_session`.
- `tests/test_pdf_ledger.py` (modify) — tests for the two new helpers.
- `tests/test_bridge_bundle.py` (create) — tests for build/parse/apply.
- `tests/test_data_bridge.py` (modify) — a view-level smoke/round-trip test if feasible without a live Streamlit session.

**Owner literals:** account `owner` and ledger owner keys are the strings `"you"` and `"spouse"` (see `engine/portfolio_sync/shapes.py:84`). Import target: `Me` → `"you"`, `Spouse` → `"spouse"`.

**Ledger shape** (`engine/pdf_ledger.py:31-36`):
```
{ "koinly": { "<owner>": {stcg, ltcg, income, captured_at, source} },
  "brokerage": { "<owner>": { "<account_number>": {...record...} } } }
```

**Note on bundle ledger sub-shape:** the bundle stores the exporter's ledger slice as an *owner-agnostic* inner dict `{"koinly": {...}, "brokerage": {...}}` (the values under the exporter's owner key), so import can re-key it under the target owner. This refines the illustrative `ledger: {koinly: {you: {…}}}` example in spec §6.

---

## Task 1: Ledger owner-slice helpers

**Files:**
- Modify: `engine/pdf_ledger.py` (add two functions near `save_ledger`/`load_ledger`)
- Test: `tests/test_pdf_ledger.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pdf_ledger.py  (add a new class)
import copy
import engine.pdf_ledger as mod
from engine.pdf_ledger import extract_owner, replace_owner


class TestOwnerSlice:
    def _ledger(self):
        return {
            "koinly": {
                "you": {"stcg": 10.0, "ltcg": 5.0, "income": 1.0, "captured_at": "t", "source": "k"},
                "spouse": {"stcg": 99.0, "ltcg": 0.0, "income": 0.0, "captured_at": "t", "source": "k"},
            },
            "brokerage": {
                "you": {"A1": {"interest": 3.0}},
                "spouse": {"B2": {"interest": 7.0}},
            },
        }

    def test_extract_owner_returns_only_that_owner_inner_values(self):
        slice_ = extract_owner(self._ledger(), "you")
        assert slice_ == {
            "koinly": {"stcg": 10.0, "ltcg": 5.0, "income": 1.0, "captured_at": "t", "source": "k"},
            "brokerage": {"A1": {"interest": 3.0}},
        }

    def test_extract_owner_missing_owner_yields_empty_sections(self):
        assert extract_owner({"koinly": {}, "brokerage": {}}, "you") == {"koinly": {}, "brokerage": {}}

    def test_extract_owner_does_not_mutate_its_input(self):
        led = self._ledger()
        snapshot = copy.deepcopy(led)
        extract_owner(led, "you")
        assert led == snapshot

    def test_replace_owner_drops_old_and_inserts_new_under_target(self):
        led = self._ledger()
        new_slice = {"koinly": {"stcg": 1.0, "ltcg": 2.0, "income": 0.0, "captured_at": "t2", "source": "k"},
                     "brokerage": {"Z9": {"interest": 4.0}}}
        out = replace_owner(led, "spouse", new_slice)
        assert out["koinly"]["spouse"] == new_slice["koinly"]
        assert out["brokerage"]["spouse"] == {"Z9": {"interest": 4.0}}
        # you untouched
        assert out["koinly"]["you"] == led["koinly"]["you"]
        assert out["brokerage"]["you"] == led["brokerage"]["you"]

    def test_replace_owner_with_empty_slice_clears_that_owner(self):
        led = self._ledger()
        out = replace_owner(led, "spouse", {"koinly": {}, "brokerage": {}})
        assert "spouse" not in out["koinly"]
        assert "spouse" not in out["brokerage"]
        assert "you" in out["koinly"]

    def test_replace_owner_does_not_mutate_its_input(self):
        led = self._ledger()
        snapshot = copy.deepcopy(led)
        replace_owner(led, "spouse", {"koinly": {}, "brokerage": {}})
        assert led == snapshot
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run -e ci test tests/test_pdf_ledger.py -k OwnerSlice`
Expected: FAIL with `ImportError: cannot import name 'extract_owner'` (or `replace_owner`).

- [ ] **Step 3: Implement the two helpers**

```python
# engine/pdf_ledger.py
import copy

def extract_owner(ledger: "PdfLedger", owner: str) -> dict:
    """Return the exporter's owner-agnostic ledger slice: the inner values under `owner`."""
    koinly = copy.deepcopy(ledger.get("koinly", {}).get(owner, {}))
    brokerage = copy.deepcopy(ledger.get("brokerage", {}).get(owner, {}))
    return {"koinly": koinly, "brokerage": brokerage}

def replace_owner(ledger: "PdfLedger", owner: str, slice_: dict) -> "PdfLedger":
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run -e ci test tests/test_pdf_ledger.py -k OwnerSlice`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

Commit message: `feat(bridge): ledger owner-slice extract/replace helpers`

---

## Task 2: Bundle build

**Files:**
- Create: `engine/bridge_bundle.py`
- Test: `tests/test_bridge_bundle.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bridge_bundle.py
from dataclasses import dataclass, field

from engine.bridge_bundle import BUNDLE_FORMAT_VERSION, build_bundle


@dataclass
class _Acct:
    owner: str
    account_name: str
    total_value: float = 0.0


@dataclass
class _Snap:
    accounts: list = field(default_factory=list)
    equity_grants: list = field(default_factory=list)


def _ledger():
    return {
        "koinly": {"you": {"stcg": 10.0}, "spouse": {"stcg": 99.0}},
        "brokerage": {"you": {"A1": {"interest": 3.0}}, "spouse": {"B2": {"interest": 7.0}}},
    }


class TestBuildBundle:
    def test_version_and_sections_present(self):
        snap = _Snap(accounts=[_Acct("you", "IRA")])
        b = build_bundle({"age_self": 61}, snap, _ledger(), owner="you")
        assert b["format_version"] == BUNDLE_FORMAT_VERSION
        assert set(b["sections"]) == {"setup_scalars", "portfolio", "ledger"}

    def test_accounts_are_owner_filtered(self):
        snap = _Snap(accounts=[_Acct("you", "MyIRA"), _Acct("spouse", "TheirIRA")])
        b = build_bundle({}, snap, _ledger(), owner="you")
        names = [a["account_name"] for a in b["sections"]["portfolio"]["accounts"]]
        assert names == ["MyIRA"]

    def test_ledger_is_only_exporter_slice(self):
        b = build_bundle({}, _Snap(), _ledger(), owner="you")
        assert b["sections"]["ledger"] == {"koinly": {"stcg": 10.0}, "brokerage": {"A1": {"interest": 3.0}}}

    def test_no_grants_in_bundle(self):
        snap = _Snap(accounts=[_Acct("you", "IRA")], equity_grants=[{"grant_id": "g1"}])
        b = build_bundle({}, snap, _ledger(), owner="you")
        assert "equity_grants" not in b["sections"]["portfolio"]
        assert "txn_shares" not in b["sections"]["portfolio"]

    def test_setup_scalars_passthrough(self):
        b = build_bundle({"filing_status": "mfj"}, _Snap(), _ledger(), owner="you")
        assert b["sections"]["setup_scalars"] == {"filing_status": "mfj"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run -e ci test tests/test_bridge_bundle.py -k BuildBundle`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.bridge_bundle'`.

- [ ] **Step 3: Implement `build_bundle`**

```python
# engine/bridge_bundle.py
"""Pure build/parse/apply for the consolidated .enc data bridge. No Streamlit imports."""
from dataclasses import asdict, is_dataclass

from engine.pdf_ledger import extract_owner, replace_owner

BUNDLE_FORMAT_VERSION = 2


def _account_to_dict(acct) -> dict:
    return asdict(acct) if is_dataclass(acct) else dict(acct)


def build_bundle(setup_scalars: dict, snapshot, ledger, *, owner: str = "you") -> dict:
    """Assemble the versioned, JSON-able bundle for one owner (default the exporter, 'you')."""
    accounts = []
    if snapshot is not None:
        for acct in getattr(snapshot, "accounts", []) or []:
            if getattr(acct, "owner", None) == owner or (isinstance(acct, dict) and acct.get("owner") == owner):
                accounts.append(_account_to_dict(acct))
    return {
        "format_version": BUNDLE_FORMAT_VERSION,
        "sections": {
            "setup_scalars": dict(setup_scalars or {}),
            "portfolio": {"accounts": accounts},
            "ledger": extract_owner(ledger or {}, owner),
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run -e ci test tests/test_bridge_bundle.py -k BuildBundle`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

Commit message: `feat(bridge): pure build_bundle assembling one owner's slice`

---

## Task 3: Bundle format detection (legacy guard)

**Files:**
- Modify: `engine/bridge_bundle.py`
- Test: `tests/test_bridge_bundle.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bridge_bundle.py  (add)
from engine.bridge_bundle import read_format_version


class TestFormatDetection:
    def test_current_bundle_version(self):
        assert read_format_version({"format_version": 2, "sections": {}}) == 2

    def test_legacy_payload_has_no_version(self):
        # old two-file payloads were a bare user_defaults dict or raw cache bytes
        assert read_format_version({"age_self": 61, "filing_status": "mfj"}) is None

    def test_non_dict_returns_none(self):
        assert read_format_version([1, 2, 3]) is None
```

- [ ] **Step 2: Run to verify fail**

Run: `pixi run -e ci test tests/test_bridge_bundle.py -k FormatDetection`
Expected: FAIL (`cannot import name 'read_format_version'`).

- [ ] **Step 3: Implement**

```python
# engine/bridge_bundle.py  (add)
def read_format_version(raw) -> int | None:
    """Return the bundle format version, or None for legacy/foreign payloads."""
    if isinstance(raw, dict):
        v = raw.get("format_version")
        if isinstance(v, int):
            return v
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `pixi run -e ci test tests/test_bridge_bundle.py -k FormatDetection`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

Commit message: `feat(bridge): read_format_version legacy-payload guard`

---

## Task 4: Apply bundle (full-replace, pure)

**Files:**
- Modify: `engine/bridge_bundle.py`
- Test: `tests/test_bridge_bundle.py`

This is the core reset logic. `apply_bundle` computes the new snapshot + ledger for the view to persist. It clears the target owner's accounts and ledger entries, then applies the incoming slice re-keyed to the target owner. Existing grants are preserved untouched.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bridge_bundle.py  (add)
from engine.bridge_bundle import apply_bundle


class _Snap2:
    def __init__(self, accounts, equity_grants=None):
        self.accounts = accounts
        self.equity_grants = equity_grants or []


class _A:
    def __init__(self, owner, name):
        self.owner = owner
        self.account_name = name


def _existing_ledger():
    return {
        "koinly": {"you": {"stcg": 10.0}, "spouse": {"stcg": 99.0}},
        "brokerage": {"you": {"A1": {"interest": 3.0}}, "spouse": {"OLD": {"interest": 500.0}}},
    }


class TestApplyBundle:
    def _incoming(self, owner="you"):
        # a bundle exported by the OTHER person (their data under 'you')
        return {
            "format_version": 2,
            "sections": {
                "setup_scalars": {},
                "portfolio": {"accounts": [{"owner": "you", "account_name": "SpouseIRA"}]},
                "ledger": {"koinly": {"stcg": 42.0}, "brokerage": {"NEW": {"interest": 1.0}}},
            },
        }

    def test_import_as_spouse_replaces_spouse_ledger_and_clears_stale(self):
        existing_snap = _Snap2([_A("you", "MyIRA")], equity_grants=[{"grant_id": "g1"}])
        new_snap, new_led = apply_bundle(
            "spouse", self._incoming(),
            existing_snapshot=existing_snap, existing_ledger=_existing_ledger(),
        )
        # spouse ledger fully replaced (stale OLD brokerage gone)
        assert new_led["koinly"]["spouse"] == {"stcg": 42.0}
        assert new_led["brokerage"]["spouse"] == {"NEW": {"interest": 1.0}}
        assert "OLD" not in new_led["brokerage"]["spouse"]
        # my (you) ledger untouched
        assert new_led["koinly"]["you"] == {"stcg": 10.0}

    def test_import_as_spouse_leaves_my_grants_untouched(self):
        existing_snap = _Snap2([_A("you", "MyIRA")], equity_grants=[{"grant_id": "g1"}])
        new_snap, _ = apply_bundle(
            "spouse", self._incoming(),
            existing_snapshot=existing_snap, existing_ledger=_existing_ledger(),
        )
        assert [g["grant_id"] for g in new_snap.equity_grants] == ["g1"]

    def test_empty_incoming_ledger_resets_target_owner(self):
        incoming = self._incoming()
        incoming["sections"]["ledger"] = {"koinly": {}, "brokerage": {}}
        _, new_led = apply_bundle(
            "spouse", incoming,
            existing_snapshot=_Snap2([_A("you", "MyIRA")]), existing_ledger=_existing_ledger(),
        )
        assert "spouse" not in new_led["koinly"]
        assert "spouse" not in new_led["brokerage"]
```

Note: `apply_bundle` handles accounts directly — it prunes the target owner's existing accounts and appends the incoming ones (deserialized to `AccountSummary` by the view in Task 6 via `_portfolio_snapshot_from_dict`), rewriting each incoming account's `owner` to the import target. It does NOT call `merge_snapshots`; that function is left unchanged for its other callers (spec §14 keeps `holdings.py` untouched). Existing `equity_grants` on the snapshot are preserved untouched (grants are local). The stand-in `_A`/`_Snap2` objects in the tests mirror exactly the `.owner` / `.accounts` / `.equity_grants` attributes `apply_bundle` reads and writes.

- [ ] **Step 2: Run to verify fail**

Run: `pixi run -e ci test tests/test_bridge_bundle.py -k ApplyBundle`
Expected: FAIL (`cannot import name 'apply_bundle'`).

- [ ] **Step 3: Implement `apply_bundle`**

```python
# engine/bridge_bundle.py  (add)
def apply_bundle(target_owner, bundle, *, existing_snapshot, existing_ledger):
    """Full-replace the target owner's slot. Returns (new_snapshot, new_ledger).
    Grants on the existing snapshot are preserved untouched (grants are local)."""
    sections = bundle.get("sections", {})

    # --- ledger: drop target owner, then insert incoming slice re-keyed to target ---
    incoming_ledger_slice = sections.get("ledger", {"koinly": {}, "brokerage": {}})
    new_ledger = replace_owner(existing_ledger or {}, target_owner, incoming_ledger_slice)

    # --- accounts: remove target-owner accounts, add incoming (rewritten to target) ---
    kept = [a for a in getattr(existing_snapshot, "accounts", [])
            if getattr(a, "owner", None) != target_owner]
    for acct in sections.get("portfolio", {}).get("accounts", []):
        # incoming accounts arrive here already reconstructed by the caller (view);
        # rewrite owner to the import target
        try:
            acct.owner = target_owner
        except AttributeError:
            acct["owner"] = target_owner
        kept.append(acct)
    existing_snapshot.accounts = kept
    # grants deliberately preserved (existing_snapshot.equity_grants unchanged)
    return existing_snapshot, new_ledger
```

- [ ] **Step 4: Run to verify pass**

Run: `pixi run -e ci test tests/test_bridge_bundle.py -k ApplyBundle`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the whole bundle + ledger suites**

Run: `pixi run -e ci test tests/test_bridge_bundle.py tests/test_pdf_ledger.py`
Expected: PASS.

- [ ] **Step 6: Commit**

Commit message: `feat(bridge): apply_bundle full-replace of owner accounts + ledger`

---

## Task 5: Single-file export in the view

**Files:**
- Modify: `views/setup/data_bridge.py` (`_handle_personal_exports`, ~lines 295-363)

Replace the two `st.download_button` calls (one for `.user_defaults.json.enc`, one for `.portfolio_cache.json.enc`) with a single bundle download named `roth_bridge.enc`.

- [ ] **Step 1: Rewrite the export block**

Gather the three inputs and seal once:

```python
# views/setup/data_bridge.py  (inside _handle_personal_exports)
import json
from engine.bridge_bundle import build_bundle
from engine.pdf_ledger import load_ledger
from engine.portfolio_sync.portfolio import load_snapshot  # confirm loader name/path

pubkey = _resolved_pubkey()                       # existing resolver in data_bridge.py:31-51 (confirm exact name)
scalars = _user_defaults_from_session()          # existing (views/setup/_state.py:42)
snapshot = load_snapshot()                        # current portfolio cache -> PortfolioSnapshot | None
ledger = load_ledger()                            # engine/pdf_ledger.py

bundle = build_bundle(scalars, snapshot, ledger, owner="you")
payload = json.dumps(bundle).encode("utf-8")
st.download_button(
    "Download my encrypted data (.enc)",
    data=seal(payload, pubkey),
    file_name="roth_bridge.enc",
    mime="application/octet-stream",
    key="export_bundle",
)
```

Remove the old two-button logic and the `read_pii_bytes(cache_path)` portfolio-only seal. Confirm the actual portfolio-cache loader name in `engine/portfolio_sync/portfolio.py` (there is `save_snapshot`; use the matching load function — grep for `def load` in that module).

- [ ] **Step 2: Verify import-time / smoke**

Run: `pixi run -e ci test tests/test_data_bridge.py`
Expected: existing tests still PASS (fix any that asserted the two old file_names / buttons — update them to the single `roth_bridge.enc`).

- [ ] **Step 3: Manual check (Streamlit)**

Launch the app (see the project's run skill / `pixi run` streamlit task), open the setup/data-bridge page, confirm exactly one download button producing `roth_bridge.enc`.

- [ ] **Step 4: Commit**

Commit message: `feat(bridge): single roth_bridge.enc export replacing two artifacts`

---

## Task 6: Full-replace import in the view

**Files:**
- Modify: `views/setup/data_bridge.py` (upload handler ~lines 240-276)

Keep the existing Me/Spouse radio (`key="pc_role"`, options `["Me","Spouse"]`). On upload: unseal, detect legacy, apply, persist, re-derive YTD, apply scalars.

- [ ] **Step 1: Rewrite the upload dispatch**

```python
# views/setup/data_bridge.py  (upload handler)
import json
from engine.bridge_bundle import read_format_version, apply_bundle
from engine.pdf_ledger import load_ledger, save_ledger
from engine.portfolio_sync.portfolio import load_snapshot, save_snapshot

privkey = _resolve_privkey_bytes()               # existing resolver in data_bridge.py:54-66 (confirm exact name)
raw = uploaded_file.read()
plaintext = open_uploaded_payload(raw, privkey)   # engine/data_bridge_crypto.py:77 — dispatches sealed vs plaintext
data = json.loads(plaintext)

if read_format_version(data) is None:
    st.warning("This looks like an older export. Please re-export from the sender using the current version and upload the new roth_bridge.enc.")
    return

target_owner = "spouse" if st.session_state.get("pc_role") == "Spouse" else "you"

# reconstruct incoming accounts into concrete types, then full-replace
incoming_snap = _portfolio_snapshot_from_dict({"accounts": data["sections"]["portfolio"]["accounts"]})  # existing deserializer, views/setup/_state.py:98
data["sections"]["portfolio"]["accounts"] = incoming_snap.accounts  # list[AccountSummary]

existing_snapshot = load_snapshot() or _empty_snapshot()
new_snapshot, new_ledger = apply_bundle(
    target_owner, data,
    existing_snapshot=existing_snapshot,
    existing_ledger=load_ledger(),
)
save_snapshot(new_snapshot)
save_ledger(new_ledger)

_apply_user_defaults_to_session(data["sections"]["setup_scalars"], as_spouse=(target_owner == "spouse"))
_rederive_ytd_from_ledger(new_ledger)   # Task 7
```

Reuse the existing deserialization helper (`_portfolio_snapshot_from_dict`) to turn account dicts into `AccountSummary`/`PortfolioSnapshot`; extract the account list from it. If cleaner, have the view build a `PortfolioSnapshot` from the incoming accounts and call `merge_snapshots(existing_wo_target, incoming, as_spouse=...)` directly instead of the account branch of `apply_bundle` — pick one and keep `apply_bundle`'s ledger logic authoritative.

- [ ] **Step 2: Verify**

Run: `pixi run -e ci test tests/test_data_bridge.py`
Expected: PASS (update any import-path assertions).

- [ ] **Step 3: Manual round-trip check**

Export `roth_bridge.enc` as one persona, upload as Spouse in a second run/keypair, confirm: spouse accounts + ledger appear, your own accounts/grants/ledger untouched, YTD totals refresh, and re-uploading the same file does not double anything.

- [ ] **Step 4: Commit**

Commit message: `feat(bridge): full-replace import of one roth_bridge.enc into the chosen slot`

---

## Task 7: YTD re-derive after import

**Files:**
- Modify: `views/setup/data_bridge.py` (helper `_rederive_ytd_from_ledger`)

After the ledger changes, recompute the ledger-derived YTD fields so household totals refresh. Mirror the assignment pattern at `views/ytd_income.py:231-236, 276-279` (fresh overwrite from `derive_*_totals`, NOT accumulation).

- [ ] **Step 1: Implement the helper**

```python
# views/setup/data_bridge.py
from engine.pdf_ledger import derive_koinly_totals, derive_brokerage_totals

def _rederive_ytd_from_ledger(ledger) -> None:
    snap = st.session_state.get("ytd_snapshot")
    if snap is None:
        return
    k = derive_koinly_totals(ledger)
    b = derive_brokerage_totals(ledger)
    # assign fresh (overwrite) exactly as views/ytd_income.py does — do NOT add onto prior values
    _assign_ytd_fields(snap, k, b)   # follow the exact field mapping used in ytd_income.py
```

Open `views/ytd_income.py:200-290` and copy the precise field-name mapping used when consuming `derive_koinly_totals` / `derive_brokerage_totals` so the same fields are set identically. Do not invent field names.

- [ ] **Step 2: Verify (accepted risk)**

Per spec §11: confirm each ledger-derived field is a fresh overwrite and that no untagged-manual YTD field is clobbered. If any realized-income field is only manual, it stays local by design.

Run: `pixi run -e ci test`
Expected: full suite PASS.

- [ ] **Step 3: Commit**

Commit message: `feat(bridge): re-derive YTD totals from ledger after import`

---

## Task 8: Cleanup, clear-state coverage, full suite

**Files:**
- Modify: `views/setup/_state.py` (`_clear_personal_session_state`)
- Modify: any tests asserting the retired two-file names

- [ ] **Step 1:** Ensure `_clear_personal_session_state` leaves the caches consistent with the new import (it pops session keys; confirm a fresh import after a clear starts clean). Address the TODO at `_state.py:178-179` only if it blocks a clean import; otherwise leave it.

- [ ] **Step 2:** Grep for `user_defaults.json.enc` and `portfolio_cache.json.enc` string literals and remove/replace any remaining references to the retired export names.

Run: `pixi run -e ci test`
Expected: full suite PASS, `pixi run -e ci lint` clean, `pixi run -e ci type-check` clean.

- [ ] **Step 3: Commit**

Commit message: `chore(bridge): retire two-file export names; verify clear-state + full suite`

---

## Definition of Done

- One `roth_bridge.enc` exports; one upload full-replaces the chosen owner slot.
- Stale ledger/account data for the target owner is reset when absent from the bundle.
- The importer's own grants and own-owner data are never touched.
- Re-importing the same file is idempotent (no doubling).
- Legacy payloads show a re-export notice, not a crash.
- `pixi run -e ci test` / `lint` / `type-check` all green.
- PR opened against `development` (per repo PR-only workflow).
