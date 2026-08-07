# SSA Benefit-Estimate Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user auto-fill `your_ss_fra`/`spouse_ss_fra` in the Setup page from FinExtract's new SSA benefit-estimate data instead of hand-typing it, via a "Sync from FinExtract" button per person.

**Architecture:** New `engine/portfolio_sync/social_security.py` module (fetch → parse → cache), following the exact fetch/cache pattern already used by `tax_return.py` and `ytd.py` in the same package. One deviation from those two single-person modules: because SSA benefit data is inherently per-person (you have your own statement, spouse has theirs), the cache file (`.ssa_cache.json`) stores both people keyed by owner (`"you"`/`"spouse"`), and `save_ssa_snapshot`/`load_ssa_snapshot` take an `owner` kwarg — `tax_return.py`/`ytd.py` don't need this because TurboTax/YTD income is household-level, not per-spouse. Re-exported from `engine/portfolio_sync/__init__.py` like every other domain. UI wiring lives in `views/setup/parameters.py`, reusing the existing `" (synced)"` + `disabled=` convention already applied to `your_ira`/`your_roth`/`spouse_ira`/`spouse_roth` — but since that existing `_synced` bool is derived from a single shared `portfolio_snapshot` truthy-check, and SSA sync is per-person, this plan introduces two independent derived booleans (`_ssa_synced_you` / `_ssa_synced_spouse`) from two new session_state keys (`ssa_snapshot_you` / `ssa_snapshot_spouse`), which must be added to `_clear_personal_session_state()`'s `keys_to_clear` list in `views/setup/_state.py`.

**Tech Stack:** Python dataclasses, `requests` (via the package's existing `client.py` helpers), Streamlit, pytest + `monkeypatch`.

---

### Task 1: SSA dataclasses + low-level fetch

**Files:**
- Modify: `engine/portfolio_sync/shapes.py` (add dataclasses near `TaxReturnSnapshot`/`MagiSnapshot`, around line 246)
- Create: `engine/portfolio_sync/social_security.py`
- Test: `tests/test_ssa_benefit_estimates.py` (new file)

- [ ] **Step 1: Write the failing test**

Look at `tests/test_security_bearer_transport.py` first for the exact `monkeypatch.setattr(client_module.requests, "get", fake_get)` pattern used to mock `_get()`'s underlying HTTP call in this codebase, then pattern-match it here:

```python
"""Tests for SSA benefit-estimate fetch, matching, and cache (engine/portfolio_sync/social_security.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.portfolio_sync import client as client_module
from engine.portfolio_sync.social_security import fetch_ssa_benefit_estimates


def _fake_response(json_data, status_code=200):
    return SimpleNamespace(
        status_code=status_code,
        headers={},
        json=lambda: json_data,
        raise_for_status=lambda: None,
    )


class TestFetchSsaBenefitEstimates:
    def test_flattens_single_institution_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {"retirement_age": 62, "claim_date": "2027-01", "benefit_type": "early", "monthly_amount": 1800.0},
            {"retirement_age": 67, "claim_date": "2032-01", "benefit_type": "full", "monthly_amount": 2600.0},
        ]
        monkeypatch.setattr(
            client_module.requests,
            "get",
            lambda *a, **kw: _fake_response({"rows": rows}),
        )
        result = fetch_ssa_benefit_estimates()
        assert result == rows
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e ci pytest tests/test_ssa_benefit_estimates.py::TestFetchSsaBenefitEstimates -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` (`engine.portfolio_sync.social_security` doesn't exist yet)

- [ ] **Step 3: Write minimal implementation**

In `engine/portfolio_sync/shapes.py`, add after `TaxReturnSnapshot` (after line ~244, before `DividendsRollupSnapshot`):

```python
@dataclass
class SSABenefitEstimate:
    """One row from FinExtract's ssa-retirement-benefit-estimates-v1 schema."""

    retirement_age: int
    claim_date: str
    benefit_type: str
    monthly_amount: float


@dataclass
class SSASnapshot:
    """Parsed SSA benefit-estimate data for one person (you or spouse)."""

    estimates: list[SSABenefitEstimate] = field(default_factory=list)
    server_available: bool = False
    error: str | None = None
```

Create `engine/portfolio_sync/social_security.py`:

```python
"""SSA retirement-benefit-estimate fetch/parse/match/cache."""

from __future__ import annotations

from typing import Any

import requests  # type: ignore[import-untyped]

from .client import _flatten_query_rows, _get


def fetch_ssa_benefit_estimates() -> list[dict[str, Any]]:
    """GET /query/social_security?data_type=benefit_estimates, flattened rows."""
    resp = _get("/query/social_security", params={"data_type": "benefit_estimates"}, timeout=5)
    resp.raise_for_status()
    return _flatten_query_rows(resp.json())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e ci pytest tests/test_ssa_benefit_estimates.py::TestFetchSsaBenefitEstimates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/portfolio_sync/shapes.py engine/portfolio_sync/social_security.py tests/test_ssa_benefit_estimates.py
git commit -m "feat(portfolio_sync): add SSA benefit-estimate dataclasses + low-level fetch"
```

---

### Task 2: Parse rows into SSASnapshot with error handling

**Files:**
- Modify: `engine/portfolio_sync/social_security.py`
- Test: `tests/test_ssa_benefit_estimates.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ssa_benefit_estimates.py`:

```python
from engine.portfolio_sync.social_security import fetch_ssa_snapshot
from engine.portfolio_sync.shapes import SSABenefitEstimate, SSASnapshot


class TestFetchSsaSnapshot:
    def test_parses_rows_into_estimates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.portfolio_sync.social_security as ssa_module

        monkeypatch.setattr(
            ssa_module,
            "fetch_ssa_benefit_estimates",
            lambda: [
                {"retirement_age": 67, "claim_date": "2032-01", "benefit_type": "full", "monthly_amount": 2600.0},
            ],
        )
        snap = fetch_ssa_snapshot()
        assert snap.server_available is True
        assert snap.error is None
        assert snap.estimates == [
            SSABenefitEstimate(retirement_age=67, claim_date="2032-01", benefit_type="full", monthly_amount=2600.0)
        ]

    def test_sets_error_on_request_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.portfolio_sync.social_security as ssa_module

        def _raise():
            raise requests.RequestException("connection refused")

        monkeypatch.setattr(ssa_module, "fetch_ssa_benefit_estimates", _raise)
        snap = fetch_ssa_snapshot()
        assert snap.server_available is False
        assert snap.error == "connection refused"
        assert snap.estimates == []

    def test_skips_malformed_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.portfolio_sync.social_security as ssa_module

        monkeypatch.setattr(
            ssa_module,
            "fetch_ssa_benefit_estimates",
            lambda: [{"retirement_age": "not-a-number", "monthly_amount": 2600.0}],
        )
        snap = fetch_ssa_snapshot()
        assert snap.server_available is True
        assert snap.estimates == []
```

Add `import requests` to the test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e ci pytest tests/test_ssa_benefit_estimates.py::TestFetchSsaSnapshot -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_ssa_snapshot'`

- [ ] **Step 3: Write minimal implementation**

Add to `engine/portfolio_sync/social_security.py`:

```python
from .shapes import SSABenefitEstimate, SSASnapshot


def fetch_ssa_snapshot() -> SSASnapshot:
    """Fetch and parse SSA benefit estimates into an SSASnapshot (best-effort)."""
    snap = SSASnapshot()
    try:
        rows = fetch_ssa_benefit_estimates()
    except requests.RequestException as e:
        snap.error = str(e)
        return snap
    snap.server_available = True
    for row in rows:
        try:
            snap.estimates.append(
                SSABenefitEstimate(
                    retirement_age=int(row["retirement_age"]),
                    claim_date=str(row.get("claim_date", "")),
                    benefit_type=str(row.get("benefit_type", "")),
                    monthly_amount=float(row["monthly_amount"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return snap
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e ci pytest tests/test_ssa_benefit_estimates.py::TestFetchSsaSnapshot -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/portfolio_sync/social_security.py tests/test_ssa_benefit_estimates.py
git commit -m "feat(portfolio_sync): parse SSA rows into SSASnapshot with error handling"
```

---

### Task 3: FRA-age matching helper

**Files:**
- Modify: `engine/portfolio_sync/social_security.py`
- Test: `tests/test_ssa_benefit_estimates.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ssa_benefit_estimates.py`:

```python
from engine.portfolio_sync.social_security import match_fra_estimate


class TestMatchFraEstimate:
    def test_exact_match(self) -> None:
        estimates = [
            SSABenefitEstimate(62, "2027-01", "early", 1800.0),
            SSABenefitEstimate(67, "2032-01", "full", 2600.0),
            SSABenefitEstimate(70, "2035-01", "delayed", 3200.0),
        ]
        assert match_fra_estimate(estimates, 67) == estimates[1]

    def test_nearest_fallback_when_no_exact_match(self) -> None:
        estimates = [
            SSABenefitEstimate(62, "2027-01", "early", 1800.0),
            SSABenefitEstimate(70, "2035-01", "delayed", 3200.0),
        ]
        # fra_age=66 is closer to 62? no -- closer to... |66-62|=4, |66-70|=4 -> tie, min() picks first
        assert match_fra_estimate(estimates, 68) == estimates[1]  # |68-62|=6, |68-70|=2

    def test_empty_list_returns_none(self) -> None:
        assert match_fra_estimate([], 67) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e ci pytest tests/test_ssa_benefit_estimates.py::TestMatchFraEstimate -v`
Expected: FAIL with `ImportError: cannot import name 'match_fra_estimate'`

- [ ] **Step 3: Write minimal implementation**

Add to `engine/portfolio_sync/social_security.py`:

```python
def match_fra_estimate(estimates: list[SSABenefitEstimate], fra_age: int) -> SSABenefitEstimate | None:
    """Find the estimate at fra_age; fall back to the nearest retirement_age.

    Returns None if estimates is empty.
    """
    if not estimates:
        return None
    exact = next((e for e in estimates if e.retirement_age == fra_age), None)
    if exact is not None:
        return exact
    return min(estimates, key=lambda e: abs(e.retirement_age - fra_age))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e ci pytest tests/test_ssa_benefit_estimates.py::TestMatchFraEstimate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/portfolio_sync/social_security.py tests/test_ssa_benefit_estimates.py
git commit -m "feat(portfolio_sync): add FRA-age matching helper for SSA estimates"
```

---

### Task 4: Per-owner cache (save/load)

**Files:**
- Modify: `engine/portfolio_sync/social_security.py`
- Test: `tests/test_ssa_benefit_estimates.py`

- [ ] **Step 1: Write the failing test**

Look at `tests/test_tax_return_engine.py`'s `test_tax_snapshot_save_load_roundtrip` (around line 127-145) for the exact `monkeypatch.setattr(portfolio_sync, "_TAX_CACHE_PATH", tmp_path / "tax.json")` pattern — patch the constant on the **package** (`engine.portfolio_sync`), not the submodule, because `__init__.py`'s `_PortfolioSyncPackage.__setattr__` forwards package-level monkeypatches to the owning submodule. Pattern-match that here. Add to `tests/test_ssa_benefit_estimates.py`:

```python
from pathlib import Path

from engine import portfolio_sync
from engine.portfolio_sync.social_security import load_ssa_snapshot, save_ssa_snapshot


class TestSsaCache:
    def test_round_trips_per_owner(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(portfolio_sync, "_SSA_CACHE_PATH", tmp_path / "ssa.json")
        you_snap = SSASnapshot(
            estimates=[SSABenefitEstimate(67, "2032-01", "full", 2600.0)],
            server_available=True,
        )
        spouse_snap = SSASnapshot(
            estimates=[SSABenefitEstimate(67, "2033-01", "full", 1900.0)],
            server_available=True,
        )
        save_ssa_snapshot(you_snap, owner="you")
        save_ssa_snapshot(spouse_snap, owner="spouse")

        loaded_you = load_ssa_snapshot(owner="you")
        loaded_spouse = load_ssa_snapshot(owner="spouse")
        assert loaded_you == you_snap
        assert loaded_spouse == spouse_snap

    def test_load_missing_file_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(portfolio_sync, "_SSA_CACHE_PATH", tmp_path / "missing.json")
        assert load_ssa_snapshot(owner="you") is None

    def test_load_missing_owner_key_returns_none_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(portfolio_sync, "_SSA_CACHE_PATH", tmp_path / "ssa.json")
        save_ssa_snapshot(SSASnapshot(server_available=True), owner="you")
        assert load_ssa_snapshot(owner="spouse") is None

    def test_load_corrupt_file_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "ssa.json"
        path.write_text("{not valid json")
        monkeypatch.setattr(portfolio_sync, "_SSA_CACHE_PATH", path)
        assert load_ssa_snapshot(owner="you") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e ci pytest tests/test_ssa_benefit_estimates.py::TestSsaCache -v`
Expected: FAIL with `ImportError` (`_SSA_CACHE_PATH`/`save_ssa_snapshot`/`load_ssa_snapshot` don't exist yet)

- [ ] **Step 3: Write minimal implementation**

Add to `engine/portfolio_sync/social_security.py`:

```python
import json
from dataclasses import asdict
from pathlib import Path

from engine.secure_io import read_pii_json, write_pii_json

_SSA_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / ".ssa_cache.json"


def save_ssa_snapshot(snap: SSASnapshot, *, owner: str) -> None:
    """Save *snap* under *owner* ('you' or 'spouse') in the shared SSA cache file."""
    existing: dict[str, Any] = {}
    if _SSA_CACHE_PATH.exists():
        try:
            existing = read_pii_json(_SSA_CACHE_PATH)
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing[owner] = asdict(snap)
    write_pii_json(_SSA_CACHE_PATH, existing)


def load_ssa_snapshot(*, owner: str) -> SSASnapshot | None:
    """Load the cached SSA snapshot for *owner*, or None if unavailable."""
    if not _SSA_CACHE_PATH.exists():
        return None
    try:
        data = read_pii_json(_SSA_CACHE_PATH)
    except (json.JSONDecodeError, OSError):
        return None
    owner_data = data.get(owner)
    if owner_data is None:
        return None
    estimates = [SSABenefitEstimate(**e) for e in owner_data.get("estimates", [])]
    return SSASnapshot(
        estimates=estimates,
        server_available=owner_data.get("server_available", False),
        error=owner_data.get("error"),
    )
```

Move the `from typing import Any` import (already present) to cover `dict[str, Any]` usage; keep imports at top of file, consolidated (don't leave the `import json`/`from dataclasses import asdict`/`from pathlib import Path`/`from engine.secure_io import ...` lines inline mid-file — move them up to the module's top-level import block alongside the existing `import requests`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e ci pytest tests/test_ssa_benefit_estimates.py::TestSsaCache -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/portfolio_sync/social_security.py tests/test_ssa_benefit_estimates.py
git commit -m "feat(portfolio_sync): add per-owner SSA benefit-estimate cache"
```

---

### Task 5: Re-export from the package facade

**Files:**
- Modify: `engine/portfolio_sync/__init__.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ssa_benefit_estimates.py`:

```python
class TestPackageReexport:
    def test_ssa_symbols_are_importable_from_package(self) -> None:
        from engine.portfolio_sync import (
            SSABenefitEstimate as _E,
            SSASnapshot as _S,
            fetch_ssa_benefit_estimates as _f1,
            fetch_ssa_snapshot as _f2,
            match_fra_estimate as _f3,
            save_ssa_snapshot as _f4,
            load_ssa_snapshot as _f5,
        )
        assert all([_E, _S, _f1, _f2, _f3, _f4, _f5])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -e ci pytest tests/test_ssa_benefit_estimates.py::TestPackageReexport -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Write minimal implementation**

In `engine/portfolio_sync/__init__.py`:

1. Add `SSABenefitEstimate` and `SSASnapshot` to the existing `from .shapes import (...)` block (alphabetical, matching existing style).
2. Add a new import block after the `from .tax_return import (...)` block:
```python
from .social_security import (
    _SSA_CACHE_PATH,
    fetch_ssa_benefit_estimates,
    fetch_ssa_snapshot,
    load_ssa_snapshot,
    match_fra_estimate,
    save_ssa_snapshot,
)
```
3. Add all seven new names to `__all__` (alphabetical, matching existing style): `"SSABenefitEstimate"`, `"SSASnapshot"`, `"_SSA_CACHE_PATH"`, `"fetch_ssa_benefit_estimates"`, `"fetch_ssa_snapshot"`, `"load_ssa_snapshot"`, `"match_fra_estimate"`, `"save_ssa_snapshot"`.
4. Add `from . import social_security as _social_security` to the "Test-monkeypatch propagation hook" import block (alongside `_tax_return`, `_ytd`, etc.).
5. Add entries to `_REEXPORT_OWNERS`: `"SSABenefitEstimate": _shapes`, `"SSASnapshot": _shapes`, `"_SSA_CACHE_PATH": _social_security`, `"fetch_ssa_benefit_estimates": _social_security`, `"fetch_ssa_snapshot": _social_security`, `"load_ssa_snapshot": _social_security`, `"match_fra_estimate": _social_security`, `"save_ssa_snapshot": _social_security`.

This last step (`_REEXPORT_OWNERS`) is why Task 4's cache test monkeypatches `portfolio_sync._SSA_CACHE_PATH` and expects it to reach the submodule — without this entry, that test would fail even though it passed in isolation with a direct submodule import. Re-run Task 4's tests after this step to confirm they still pass through the package-level patch path.

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -e ci pytest tests/test_ssa_benefit_estimates.py -v`
Expected: All PASS (full file, including Task 4's tests re-verified against the package-level monkeypatch path)

- [ ] **Step 5: Commit**

```bash
git add engine/portfolio_sync/__init__.py
git commit -m "feat(portfolio_sync): re-export SSA sync symbols from package facade"
```

---

### Task 6: UI wiring — sync button for "you"

**Files:**
- Modify: `views/setup/parameters.py`

- [ ] **Step 1: Add the import**

Near the top of `views/setup/parameters.py`, in the existing import block (after `from config.loader import save_user_defaults`), add:

```python
from engine.portfolio_sync import fetch_ssa_snapshot, match_fra_estimate, save_ssa_snapshot
```

- [ ] **Step 2: Add a private sync helper**

Add this function above `render_parameters_tab` (near the other module-level helpers in this file):

```python
def _sync_ssa_for(owner: str, fra_age: int) -> str | None:
    """Fetch, match, and apply the FRA SSA benefit for *owner* ('you' or 'spouse').

    Writes the matched monthly benefit into session_state and caches the raw
    snapshot. Returns a warning message on failure/no-match, or None on success.
    """
    snap = fetch_ssa_snapshot()
    if snap.error:
        return f"SSA sync failed: {snap.error}"
    match = match_fra_estimate(snap.estimates, fra_age)
    if match is None:
        return "No SSA benefit estimate found near the configured FRA age; sync skipped."
    session_key = "your_ss_fra" if owner == "you" else "spouse_ss_fra"
    st.session_state[session_key] = match.monthly_amount
    st.session_state[f"ssa_snapshot_{owner}"] = snap
    save_ssa_snapshot(snap, owner=owner)
    return None
```

- [ ] **Step 3: Wire the button into the "Me" tab**

In `render_parameters_tab`, inside `with me_sub:`, immediately before the existing `your_ss_fra` block (the code starting `your_fra_age = st.session_state.get("your_fra_age", 67)` at line ~440), add:

```python
        _ssa_synced_you = bool(st.session_state.get("ssa_snapshot_you"))
```

Then change the `your_ss_fra` number_input block from:

```python
        your_fra_age = st.session_state.get("your_fra_age", 67)
        st.session_state.your_ss_fra = st.number_input(
            f"Your SS at FRA {your_fra_age} ($/mo)",
            min_value=0,  # UU2-UI-06
            value=st.session_state.your_ss_fra,
            step=100,
            format="%d",
        )
```

to:

```python
        your_fra_age = st.session_state.get("your_fra_age", 67)
        st.session_state.your_ss_fra = st.number_input(
            f"Your SS at FRA {your_fra_age} ($/mo)" + (" (synced)" if _ssa_synced_you else ""),
            min_value=0,  # UU2-UI-06
            value=st.session_state.your_ss_fra,
            step=100,
            format="%d",
            disabled=_ssa_synced_you,
            help="Auto-synced from FinExtract (SSA benefit estimate)" if _ssa_synced_you else None,
        )
        if st.button("Sync SS from FinExtract", key="_sync_ssa_you_btn"):
            _warning = _sync_ssa_for("you", your_fra_age)
            if _warning:
                st.warning(_warning)
            else:
                st.rerun()
```

- [ ] **Step 4: Manual smoke check**

Run: `pixi run -e ci streamlit run app.py` (or the project's existing dev-run task if one exists — check `pixi.toml` for a `run`/`dev` task first), open Setup → Parameters → Me tab, confirm the "Sync SS from FinExtract" button renders next to the SS-at-FRA field without raising an exception (FinExtract doesn't need to be running for this check — clicking it with the server down should produce a `st.warning`, not a crash).

- [ ] **Step 5: Commit**

```bash
git add views/setup/parameters.py
git commit -m "feat(setup): add SSA FinExtract sync button for 'you'"
```

---

### Task 7: UI wiring — sync button for "spouse"

**Files:**
- Modify: `views/setup/parameters.py`

- [ ] **Step 1: Wire the button into the "Spouse" tab**

Inside `with spouse_sub:`, immediately before the existing `spouse_ss_fra` block (the code starting `spouse_fra_age = st.session_state.get("spouse_fra_age", 67)` at line ~522), add:

```python
        _ssa_synced_spouse = bool(st.session_state.get("ssa_snapshot_spouse"))
```

Then change the `spouse_ss_fra` number_input block from:

```python
        spouse_fra_age = st.session_state.get("spouse_fra_age", 67)
        st.session_state.spouse_ss_fra = st.number_input(
            f"Spouse SS at FRA {spouse_fra_age} ($/mo)",
            min_value=0,  # UU2-UI-06
            value=st.session_state.spouse_ss_fra,
            step=100,
            format="%d",
            disabled=_is_single,
        )
```

to:

```python
        spouse_fra_age = st.session_state.get("spouse_fra_age", 67)
        st.session_state.spouse_ss_fra = st.number_input(
            f"Spouse SS at FRA {spouse_fra_age} ($/mo)" + (" (synced)" if _ssa_synced_spouse else ""),
            min_value=0,  # UU2-UI-06
            value=st.session_state.spouse_ss_fra,
            step=100,
            format="%d",
            disabled=_is_single or _ssa_synced_spouse,
            help="Auto-synced from FinExtract (SSA benefit estimate)" if _ssa_synced_spouse else None,
        )
        if st.button("Sync SS from FinExtract", key="_sync_ssa_spouse_btn", disabled=_is_single):
            _warning = _sync_ssa_for("spouse", spouse_fra_age)
            if _warning:
                st.warning(_warning)
            else:
                st.rerun()
```

- [ ] **Step 2: Manual smoke check**

Same as Task 6 Step 4, but the Spouse tab — also verify the button is disabled when Filing status is "Single".

- [ ] **Step 3: Commit**

```bash
git add views/setup/parameters.py
git commit -m "feat(setup): add SSA FinExtract sync button for 'spouse'"
```

---

### Task 8: Clear SSA session state on personal-mode reset

**Files:**
- Modify: `views/setup/_state.py`

- [ ] **Step 1: Add the new keys to the clear list**

In `_clear_personal_session_state()`, in the `keys_to_clear` list, add `"ssa_snapshot_you"` and `"ssa_snapshot_spouse"` next to the existing `"tax_return_snapshot"`, `"ytd_snapshot"` entries (around line 166-169):

```python
        "tax_return_snapshot",
        "ytd_snapshot",
        "ssa_snapshot_you",
        "ssa_snapshot_spouse",
        "apply_ytd_to_projection",
```

- [ ] **Step 2: Manual verification**

This file has no existing dedicated unit test (views/setup helpers are exercised via the Streamlit UI per this project's testing conventions — see `tests/` for confirmation there's no `test__state.py`). Verify manually: Setup page → sync SSA for "you" → Setup page → "Reset to demo data" (or whatever UI action calls `_clear_personal_session_state()` — find it via the button/menu that currently clears IRA/Roth on reset) → confirm the SS-at-FRA field goes back to editable (not showing "(synced)") and the demo default value.

- [ ] **Step 3: Commit**

```bash
git add views/setup/_state.py
git commit -m "fix(setup): clear SSA sync state on personal-mode reset"
```

---

### Task 9: Full suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pixi run -e ci test`
Expected: All tests pass, including the new `tests/test_ssa_benefit_estimates.py` file and the existing suite (no regressions).

- [ ] **Step 2: Run lint and type-check**

Run: `pixi run -e ci lint` and `pixi run -e ci type-check`
Expected: Both clean (per this project's mypy-not-just-ruff convention — mypy runs on `engine/` and `models/`, which includes the new `engine/portfolio_sync/social_security.py` and `shapes.py` changes).

- [ ] **Step 3: Manual end-to-end check with a live FinExtract server (if available)**

If a local FinExtract server is running with SSA data available, click both sync buttons on the Setup page and confirm the FRA benefit fields populate with the matched value and lock to "(synced)". If FinExtract isn't running/available in this environment, note that this step was skipped and why, rather than skipping silently.
