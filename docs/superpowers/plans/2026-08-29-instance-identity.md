# Instance Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each planner instance know which person it belongs to, so scanned statements are attributed automatically instead of prompting per account on every scan.

**Architecture:** A durable machine-local setting `instance_owner ∈ {"you","spouse"}` in a dedicated file. Attribution resolves as `account override → instance_owner`, never prompting. The existing name-keyed `owner_map` is narrowed to identity cross-checking only. Bundle export and import both derive owner from the instance instead of hardcoding or asking.

**Tech Stack:** Python 3.11/3.12, Streamlit (Classic `st.tabs`), pytest + `streamlit.testing.v1.AppTest`, pixi for task running.

**Spec:** `docs/superpowers/specs/2026-08-29-instance-identity-design.md`

---

## Verification commands

Used throughout. The `test` pixi task CANNOT be scoped — it runs `pytest tests/` unconditionally and OOMs in one process. Always shard:

```bash
pixi run -e ci lint          # ruff check .
pixi run -e ci type-check    # mypy engine/ models/
# Full suite, sharded — BOTH must pass before any PR:
pixi run -e ci python -m pytest tests/ -q -rE -k "apptest or shell or view or partial or command_center or flow"
pixi run -e ci python -m pytest tests/ -q -rE -k "not (apptest or shell or view or partial or command_center or flow)"
```

---

### Task 1: `engine/instance_identity.py`

**Files:**
- Create: `engine/instance_identity.py`
- Test: `tests/test_instance_identity.py`

- [ ] Write the failing test file `tests/test_instance_identity.py`:

```python
"""Tests for engine.instance_identity -- durable per-instance owner identity."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestInstanceOwnerRoundTrip:
    def test_save_load_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.instance_identity as mod

        monkeypatch.setattr(mod, "INSTANCE_OWNER_PATH", tmp_path / ".instance_owner.json")
        mod.save_instance_owner("you")
        assert mod.load_instance_owner() == "you"

    def test_load_missing_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.instance_identity as mod

        monkeypatch.setattr(mod, "INSTANCE_OWNER_PATH", tmp_path / "nope.json")
        assert mod.load_instance_owner() is None

    def test_load_corrupt_raises_and_is_not_overwritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.instance_identity as mod

        bad = tmp_path / ".instance_owner.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "INSTANCE_OWNER_PATH", bad)
        with pytest.raises(mod.CorruptInstanceOwnerError):
            mod.load_instance_owner()
        assert bad.read_text() == "{not json"

    def test_save_rejects_household(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.instance_identity as mod

        monkeypatch.setattr(mod, "INSTANCE_OWNER_PATH", tmp_path / ".instance_owner.json")
        with pytest.raises(ValueError, match="Invalid instance owner"):
            mod.save_instance_owner("household")

    def test_save_refuses_to_clobber_corrupt_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.instance_identity as mod

        bad = tmp_path / ".instance_owner.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "INSTANCE_OWNER_PATH", bad)
        with pytest.raises(mod.CorruptInstanceOwnerError):
            mod.save_instance_owner("you")
        assert bad.read_text() == "{not json"
```

- [ ] Run it and confirm it fails on import (module does not exist yet):

```bash
pixi run -e ci python -m pytest tests/test_instance_identity.py -q -rE
```

- [ ] Create `engine/instance_identity.py`:

```python
"""Durable machine-local record of which person this planner instance belongs to.

Pure module: stdlib only. No streamlit, no other engine imports beyond
engine.pdf_owner's role vocabulary (mirrors engine/data_sources/paths.py's
purity rule). Sits directly in engine/, not engine/data_sources/, so
_REPO_ROOT climbs one fewer parent than paths.py does.

An "instance" is a single deployment/session of this planner (a dev laptop
install, one browser's session on the public site). This value never changes
automatically and is deliberately narrower than engine.pdf_owner.OwnerRole:
an instance can be "you" or "spouse" but never "household" -- an instance
belongs to a single person, even though a specific ACCOUNT it later observes
may be jointly titled (see engine/account_attribution.py for that distinct,
per-account concept).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from engine.pdf_owner import OwnerRole

_REPO_ROOT = Path(__file__).resolve().parents[1]

INSTANCE_OWNER_PATH = _REPO_ROOT / ".instance_owner.json"

_VALID_INSTANCE_OWNERS = frozenset({OwnerRole.YOU.value, OwnerRole.SPOUSE.value})

__all__ = [
    "INSTANCE_OWNER_PATH",
    "CorruptInstanceOwnerError",
    "load_instance_owner",
    "save_instance_owner",
]


class CorruptInstanceOwnerError(Exception):
    """Raised when INSTANCE_OWNER_PATH exists but its content is not a valid
    instance_owner payload (truncated/malformed JSON, missing key, or an
    invalid value).

    Deliberately NOT the same outcome as a missing file: a missing file means
    "this instance has never been assigned an owner yet" -- safe for a
    caller to treat as a first-run prompt case. A file that exists but fails
    to parse means real data is sitting on disk in an unreadable state, and
    silently treating it as "unset" would let save_instance_owner clobber the
    only copy. Mirrors
    engine.data_sources.committed.CorruptCommittedCacheError's shape exactly.
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"instance owner cache at {path!r} is corrupt: {cause!r}")


def load_instance_owner() -> str | None:
    """Return the persisted instance owner ("you"/"spouse"), or None if unset.

    None means "this instance has no identity yet" -- callers must treat
    that as a first-run prompt case, never silently default it. Raises
    CorruptInstanceOwnerError if the file exists but its content is
    unreadable or invalid -- callers must not silently re-prompt over broken
    data (see CorruptInstanceOwnerError's docstring).
    """
    try:
        raw = INSTANCE_OWNER_PATH.read_text()
    except OSError:
        return None
    try:
        payload = json.loads(raw)
        owner = payload["instance_owner"]
        if owner not in _VALID_INSTANCE_OWNERS:
            raise ValueError(f"invalid instance_owner value {owner!r}")
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise CorruptInstanceOwnerError(INSTANCE_OWNER_PATH, exc) from exc
    return str(owner)


def save_instance_owner(owner: str) -> None:
    """Persist *owner* ("you" or "spouse" only) atomically.

    Rejects "household" and any other value -- an instance belongs to one
    person, never a joint identity (see module docstring). Pre-checks that
    an existing file still parses before writing: if it exists but is
    corrupt, this raises rather than silently clobbering the only copy of
    whatever is on disk (mirrors
    engine.data_sources.committed.save_committed's audit-0809 #11 guard).
    Writes via a tmp file + os.replace() so a crash mid-write cannot
    truncate a previously-good file.
    """
    if owner not in _VALID_INSTANCE_OWNERS:
        raise ValueError(
            f"Invalid instance owner {owner!r}, must be one of {sorted(_VALID_INSTANCE_OWNERS)}"
        )
    if INSTANCE_OWNER_PATH.exists():
        try:
            json.loads(INSTANCE_OWNER_PATH.read_text())
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise CorruptInstanceOwnerError(INSTANCE_OWNER_PATH, exc) from exc
    tmp_path = INSTANCE_OWNER_PATH.with_name(f"{INSTANCE_OWNER_PATH.name}.tmp-{os.getpid()}")
    tmp_path.write_text(json.dumps({"version": 1, "instance_owner": owner}))
    os.replace(tmp_path, INSTANCE_OWNER_PATH)
```

- [ ] Run the test file again and confirm all 5 tests pass:

```bash
pixi run -e ci python -m pytest tests/test_instance_identity.py -q -rE
```

- [ ] Lint and type-check the new module:

```bash
pixi run -e ci lint
pixi run -e ci type-check
```

- [ ] Commit:

```
mcp__git__execute_tool("git_add", {"repo_path": ".", "files": ["engine/instance_identity.py", "tests/test_instance_identity.py"]})
mcp__git__execute_tool("git_commit", {"repo_path": ".", "message": "feat(instance-identity): add engine.instance_identity module"})
```

---

### Task 2: `engine/account_attribution.py`

**Files:**
- Create: `engine/account_attribution.py`
- Test: `tests/test_account_attribution.py`

- [ ] Write the failing test file `tests/test_account_attribution.py`:

```python
"""Tests for engine.account_attribution -- per-account owner overrides."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSaveOverrideRejectsAmbiguousBroker:
    def test_broker_containing_delimiter_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json")
        with pytest.raises(ValueError, match=r"\|"):
            mod.save_account_override("schwab|evil", "****-*123", "spouse")


class TestOverridesRoundTrip:
    def test_save_load_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json")
        mod.save_account_override("schwab", "****-*123", "spouse")
        assert mod.load_account_overrides() == {("schwab", "****-*123"): "spouse"}

    def test_delete_removes_entry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json")
        mod.save_account_override("schwab", "****-*123", "spouse")
        mod.delete_account_override("schwab", "****-*123")
        assert mod.load_account_overrides() == {}

    def test_load_missing_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / "nope.json")
        assert mod.load_account_overrides() == {}

    def test_load_corrupt_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.account_attribution as mod

        bad = tmp_path / ".account_attribution.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", bad)
        assert mod.load_account_overrides() == {}

    def test_save_two_different_keys_both_persist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json")
        mod.save_account_override("schwab", "****-*123", "spouse")
        mod.save_account_override("vanguard", "****-*456", "you")
        assert mod.load_account_overrides() == {
            ("schwab", "****-*123"): "spouse",
            ("vanguard", "****-*456"): "you",
        }

    def test_save_invalid_owner_role_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json")
        with pytest.raises(ValueError, match="Invalid owner role"):
            mod.save_account_override("schwab", "****-*123", "bogus")


class TestRefusesToClobberCorruptStore:
    def test_save_refuses_to_clobber_corrupt_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        bad = tmp_path / ".account_attribution.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", bad)
        with pytest.raises(mod.CorruptAccountAttributionError):
            mod.save_account_override("schwab", "****-*123", "spouse")
        assert bad.read_text() == "{not json"

    def test_delete_refuses_to_clobber_corrupt_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        bad = tmp_path / ".account_attribution.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", bad)
        with pytest.raises(mod.CorruptAccountAttributionError):
            mod.delete_account_override("schwab", "****-*123")
        assert bad.read_text() == "{not json"


class TestResolveAccountOwner:
    def test_resolves_to_instance_owner_when_no_override(self) -> None:
        import engine.account_attribution as mod

        assert mod.resolve_account_owner("schwab", "****-*123", {}, "you") == "you"

    def test_override_wins_over_instance_owner(self) -> None:
        import engine.account_attribution as mod

        overrides = {("schwab", "****-*123"): "spouse"}
        assert mod.resolve_account_owner("schwab", "****-*123", overrides, "you") == "spouse"

    def test_never_returns_none(self) -> None:
        import engine.account_attribution as mod

        assert mod.resolve_account_owner("vanguard", "9999", {}, "spouse") is not None
```

- [ ] Run it and confirm it fails on import:

```bash
pixi run -e ci python -m pytest tests/test_account_attribution.py -q -rE
```

- [ ] Create `engine/account_attribution.py`:

```python
"""Per-account owner-attribution overrides, keyed by (broker, account_number).

Holds account numbers, so persisted via engine.secure_io's PII helpers (0o600
+ O_NOFOLLOW). NOTE: this is filesystem hardening only, NOT encryption -- the
file itself is plaintext JSON, readable by anyone with local access to this
machine/account, same as engine/pdf_owner.py's .pdf_owner_map.json.

Narrower and account-scoped compared to engine/instance_identity.py's
per-instance default: resolve_account_owner() falls back to instance_owner
whenever no per-account override exists.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from engine.pdf_owner import OWNER_ROLES
from engine.secure_io import read_pii_json, write_pii_json

_ACCOUNT_ATTRIBUTION_PATH = Path(__file__).resolve().parent.parent / ".account_attribution.json"

_KEY_DELIMITER = "|"

__all__ = [
    "CorruptAccountAttributionError",
    "delete_account_override",
    "load_account_overrides",
    "resolve_account_owner",
    "save_account_override",
]


class CorruptAccountAttributionError(Exception):
    """Raised by ``save_account_override``/``delete_account_override`` when
    ``_ACCOUNT_ATTRIBUTION_PATH`` exists but its content is not valid JSON
    (truncated/malformed write, e.g. process killed mid-write).

    Mirrors ``CorruptCommittedCacheError`` (engine/data_sources/committed.py,
    audit-0809 #11 / PR #442) and ``CandidateStore.save``'s
    ``CorruptCandidateStoreError`` (engine/data_sources/candidate_store.py,
    audit-0823 / PR #447): ``load_account_overrides`` tolerates a corrupt
    file by degrading to ``{}`` (callers depend on that resilience -- a
    broken cache must not crash the app), but if a write then merged onto
    that degraded empty dict and saved it, every prior override on disk
    would be permanently destroyed and replaced with just the one entry
    being written. Once this plan's account-attribution table retires the
    interactive owner-confirm selectboxes, ``resolve_account_owner`` becomes
    the sole non-interactive authority -- a lost override would silently
    reattribute an account with no human step left to catch it.
    """

    def __init__(self, path: str | Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"account attribution store at {path!r} is corrupt: {cause!r}")


def _encode_key(broker: str, account_number: str) -> str:
    if _KEY_DELIMITER in broker:
        raise ValueError(
            f"broker name {broker!r} may not contain {_KEY_DELIMITER!r} (used as the key delimiter)"
        )
    return f"{broker}{_KEY_DELIMITER}{account_number}"


def _decode_key(raw: str) -> tuple[str, str] | None:
    broker, sep, account_number = raw.partition(_KEY_DELIMITER)
    if not sep:
        return None
    return broker, account_number


def load_account_overrides() -> dict[tuple[str, str], str]:
    """Return {(broker, account_number): owner}.

    Read-path only: any read/parse failure or wrong shape degrades to {}
    rather than raising, so a broken cache file can't crash the app
    (mirrors engine/pdf_owner.py's load_owner_map tolerant shape). This is
    deliberately NOT mirrored on the write path -- see
    CorruptAccountAttributionError's docstring for why a write must raise
    instead of silently compounding a corrupt read into permanent loss.
    """
    if not _ACCOUNT_ATTRIBUTION_PATH.exists():
        return {}
    try:
        raw = read_pii_json(_ACCOUNT_ATTRIBUTION_PATH)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    overrides_raw = raw.get("overrides")
    if not isinstance(overrides_raw, dict):
        return {}
    result: dict[tuple[str, str], str] = {}
    for key, owner in overrides_raw.items():
        decoded = _decode_key(str(key))
        if decoded is not None:
            result[decoded] = str(owner)
    return result


def _refuse_if_corrupt() -> None:
    """Raise CorruptAccountAttributionError if the store exists but its
    current on-disk content fails to parse.

    Called by both write paths before they merge onto whatever
    ``load_account_overrides()`` (tolerant) returns, so a corrupt file
    stops the write instead of being silently replaced by a fresh dict
    holding only the one entry being saved/deleted (see
    CorruptAccountAttributionError's docstring). A missing file is not
    corrupt -- first run, nothing to protect.
    """
    if not _ACCOUNT_ATTRIBUTION_PATH.exists():
        return
    try:
        read_pii_json(_ACCOUNT_ATTRIBUTION_PATH)
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise CorruptAccountAttributionError(_ACCOUNT_ATTRIBUTION_PATH, exc) from exc


def _write(overrides: dict[tuple[str, str], str]) -> None:
    """Write *overrides* atomically via a tmp file + ``os.replace``.

    Callers must call ``_refuse_if_corrupt()`` first -- this function does
    not pre-check, it only writes.
    """
    encoded = {
        _encode_key(broker, account_number): owner
        for (broker, account_number), owner in overrides.items()
    }
    tmp_path = _ACCOUNT_ATTRIBUTION_PATH.with_name(
        f"{_ACCOUNT_ATTRIBUTION_PATH.name}.tmp-{os.getpid()}"
    )
    write_pii_json(tmp_path, {"version": 1, "overrides": encoded})
    os.replace(tmp_path, _ACCOUNT_ATTRIBUTION_PATH)


def save_account_override(broker: str, account_number: str, owner: str) -> None:
    """Persist an override for ``(broker, account_number)``, atomically.

    Raises CorruptAccountAttributionError (without writing) if the file
    already exists but fails to parse -- see that class's docstring for why
    this refuses rather than clobbers.
    """
    if owner not in OWNER_ROLES:
        raise ValueError(f"Invalid owner role {owner!r}, must be one of {sorted(OWNER_ROLES)}")
    _refuse_if_corrupt()
    overrides = load_account_overrides()
    overrides[(broker, account_number)] = owner
    _write(overrides)


def delete_account_override(broker: str, account_number: str) -> None:
    """Remove any override for ``(broker, account_number)``, atomically.

    Raises CorruptAccountAttributionError (without writing) if the file
    already exists but fails to parse -- see that class's docstring for why
    this refuses rather than clobbers.
    """
    _refuse_if_corrupt()
    overrides = load_account_overrides()
    overrides.pop((broker, account_number), None)
    _write(overrides)


def resolve_account_owner(
    broker: str,
    account_number: str,
    account_overrides: dict[tuple[str, str], str],
    instance_owner: str,
) -> str:
    """TOTAL: an override wins, otherwise fall back to instance_owner.

    Never returns None and never prompts -- this is the single replacement
    for the ad-hoc st.selectbox owner-confirm prompts this plan retires (see
    views/ytd_income/_partials/_sync_scan.py, Task 6).
    """
    return account_overrides.get((broker, account_number)) or instance_owner
```

**Write-path hardening (added after initial implementation, before this task shipped):** the first draft of this module had `save_account_override`/`delete_account_override` call the tolerant `load_account_overrides()` directly and write the merged result straight back -- against a corrupted store this would silently destroy every prior valid override. A quality review caught this as the exact incident class `engine/data_sources/candidate_store.py` (audit-0823, PR #447) and `engine/data_sources/committed.py` (PR #442) were hardened against, and it matters more here because this plan retires the interactive owner-confirm selectboxes that would otherwise catch a silent reattribution. `load_account_overrides()` itself keeps its tolerant read semantics unchanged -- only the two write paths gained a pre-write parse check (`_refuse_if_corrupt`) and atomic tmp-file + `os.replace` writes.

- [ ] Run the test file again and confirm all 12 tests pass:

```bash
pixi run -e ci python -m pytest tests/test_account_attribution.py -q -rE
```

- [ ] Lint and type-check:

```bash
pixi run -e ci lint
pixi run -e ci type-check
```

- [ ] Commit:

```
mcp__git__execute_tool("git_add", {"repo_path": ".", "files": ["engine/account_attribution.py", "tests/test_account_attribution.py"]})
mcp__git__execute_tool("git_commit", {"repo_path": ".", "message": "feat(instance-identity): add engine.account_attribution module"})
```

---

### Task 3: conftest registration — DO THIS BEFORE ANY UI WORK

**Why this task comes third, not last:** `_forbid_real_cache_writes` (an autouse fixture in `tests/conftest.py`) fails any test that creates/modifies/deletes a real repo-root cache file that isn't in `_WATCHED_CACHE_PATHS`/redirected by `_redirect_cache_paths_to_tmp`. Tasks 1-2's own tests were safe because each test locally monkeypatched the module's path constant directly (same pattern as `tests/test_pdf_owner.py`). Task 4 onward drives real app code (`app.py`, `views/setup/command_center.py`, `views/ytd_income/_partials/_sync_scan.py`) through `AppTest`, which calls `load_instance_owner()`/`load_account_overrides()` with NO per-test monkeypatch — without global redirection those calls would read/write the developer's REAL `.instance_owner.json` / `.account_attribution.json`, which git cannot restore (same class of defect as audit-0805 C98/C67-C69).

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_instance_identity.py`
- Modify: `tests/test_account_attribution.py`
- Modify: `tests/test_audit_0809_f18_cache_path_binding.py` — widening `_command_center_cache_files()` from 3 to 5 paths breaks its hard-coded 3-path assertion; update the expected count/list to match.

- [ ] Append a redirection-proof failing test to `tests/test_instance_identity.py` (no local monkeypatch — this is the point):

```python
class TestInstanceOwnerPathIsGloballyRedirected:
    def test_no_local_monkeypatch_still_avoids_the_real_repo_file(self) -> None:
        """Proves tests/conftest.py's autouse _redirect_cache_paths_to_tmp
        fixture redirects INSTANCE_OWNER_PATH on its own. Before that
        registration exists, this call touches the real repo-root
        .instance_owner.json and _forbid_real_cache_writes fails the test."""
        from engine.instance_identity import save_instance_owner

        save_instance_owner("you")
```

- [ ] Append the same style proof to `tests/test_account_attribution.py`:

```python
class TestAccountAttributionPathIsGloballyRedirected:
    def test_no_local_monkeypatch_still_avoids_the_real_repo_file(self) -> None:
        from engine.account_attribution import save_account_override

        save_account_override("schwab", "****-*123", "spouse")
```

- [ ] Run both and confirm they FAIL with a `_forbid_real_cache_writes`-style message naming the real `.instance_owner.json` / `.account_attribution.json` paths:

```bash
pixi run -e ci python -m pytest tests/test_instance_identity.py tests/test_account_attribution.py -q -rE -k GloballyRedirected
```

- [ ] Immediately delete whichever real file(s) the failing run just created at the repo root (`.instance_owner.json`, `.account_attribution.json`) before proceeding — this is exactly the destructive write the guard exists to catch, don't leave it on disk.

- [ ] Edit `tests/conftest.py` — add two module imports to the existing `noqa: E402` block (:32-49), alphabetically ordered:

```python
import engine.account_attribution as _account_attribution_mod  # noqa: E402
```
placed immediately after `import config.loader as _config_loader_mod` (before `engine.brokerage_statement_pdf`), and:

```python
import engine.instance_identity as _instance_identity_mod  # noqa: E402
```
placed immediately after `import engine.exercise_schedule_store as _exercise_schedule_store_mod` (before `engine.koinly_report_pdf`).

- [ ] Add both new path constants to `_WATCHED_CACHE_PATHS` (:51-67 list literal), as two new entries before the closing `]`:

```python
    _account_attribution_mod._ACCOUNT_ATTRIBUTION_PATH,
    _instance_identity_mod.INSTANCE_OWNER_PATH,
```

- [ ] Add matching `monkeypatch.setattr` redirect lines inside `_redirect_cache_paths_to_tmp`'s "1. Defining-module attributes." block (:134-150), immediately after the `_config_loader_mod` line:

```python
    monkeypatch.setattr(_account_attribution_mod, "_ACCOUNT_ATTRIBUTION_PATH", _tmp(".account_attribution.json"))
    monkeypatch.setattr(_instance_identity_mod, "INSTANCE_OWNER_PATH", _tmp(".instance_owner.json"))
```

- [ ] Extend `_command_center_cache_files()` (:234-251) to also return the two new paths. This is a FUNCTION resolved at call time (per its own docstring, audit-0809 F18 — a module-level constant built at conftest import time would escape the redirect fixture), so keep it a function and just widen its return list — Command Center now owns both files (the identity gate in Task 5, the attribution table in Task 7):

```python
    return [
        _paths_mod.CANDIDATE_STORE_PATH,
        _paths_mod.TRUST_CHOICES_PATH,
        _paths_mod.COMMITTED_PATH,
        _instance_identity_mod.INSTANCE_OWNER_PATH,
        _account_attribution_mod._ACCOUNT_ATTRIBUTION_PATH,
    ]
```

- [ ] Re-run the two redirection-proof tests and confirm they now PASS:

```bash
pixi run -e ci python -m pytest tests/test_instance_identity.py tests/test_account_attribution.py -q -rE
```

- [ ] Run the cache-write-guard's own unit test plus these two files together to confirm nothing regressed:

```bash
pixi run -e ci python -m pytest tests/test_instance_identity.py tests/test_account_attribution.py tests/test_pdf_owner.py tests/test_cache_write_guard.py tests/test_audit_0809_f18_cache_path_binding.py -q -rE
```

- [ ] Lint:

```bash
pixi run -e ci lint
```

- [ ] Commit:

```
mcp__git__execute_tool("git_add", {"repo_path": ".", "files": ["tests/conftest.py", "tests/test_instance_identity.py", "tests/test_account_attribution.py"]})
mcp__git__execute_tool("git_commit", {"repo_path": ".", "message": "test(instance-identity): register new cache paths in conftest redirect/guard"})
```

---

### Task 4: `app.py` seeding

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app_seed_session_state.py`

**Note:** `app.py` is a Streamlit script with heavy import-time side effects, so `tests/test_app_seed_session_state.py` already extracts and `exec`s just `_seed_session_state`'s source text into an isolated namespace rather than using `AppTest` directly (see that file's module docstring). This task extends that exact established harness rather than introducing a new `AppTest.from_file("app.py")` pattern with no precedent in this repo.

- [ ] Read `tests/test_app_seed_session_state.py`'s `_run_seed_session_state` helper (:66-84) and extend its signature to accept an `instance_owner` parameter, injecting a fake `load_instance_owner`/`CorruptInstanceOwnerError` into the exec namespace:

```python
from engine.instance_identity import CorruptInstanceOwnerError

_CORRUPT = object()


def _run_seed_session_state(defaults: dict, instance_owner: str | None = "you") -> _FakeSessionState:
    """Extract and execute app.py's _seed_session_state against *defaults*.

    ``instance_owner`` stands in for engine.instance_identity.load_instance_owner's
    return value; pass the sentinel ``_CORRUPT`` to simulate
    CorruptInstanceOwnerError being raised instead. Defaults to "you" so
    every pre-existing call site in this file (which doesn't pass the new
    kwarg) keeps behaving as before.

    Returns the resulting fake session_state for assertions.
    """
    text = APP_PATH.read_text()
    start = text.index("def _seed_session_state()")
    end = text.index("\n\n\n# Shared state: household parameters")
    source = text[start:end]
    fake_st = _FakeSt()

    def _fake_load_instance_owner() -> str | None:
        if instance_owner is _CORRUPT:
            raise CorruptInstanceOwnerError(".instance_owner.json", ValueError("bad"))
        return instance_owner

    namespace: dict[str, Any] = {
        "st": fake_st,
        "load_defaults": lambda: defaults,
        "BASE_PART_B": BASE_PART_B,
        "SCALAR_KEYS": SCALAR_KEYS,
        "load_instance_owner": _fake_load_instance_owner,
        "CorruptInstanceOwnerError": CorruptInstanceOwnerError,
    }
    exec(compile(source, "<_seed_session_state>", "exec"), namespace)
    namespace["_seed_session_state"]()
    return fake_st.session_state
```

- [ ] Append the failing test class to the same file:

```python
class TestSeedSessionStateInstanceOwner:
    """2026-08-29: instance identity (engine.instance_identity) must be
    seeded so views/setup/command_center.py's identity gate and the
    account-attribution resolvers can read st.session_state["instance_owner"]
    without re-deriving it themselves."""

    def test_persisted_instance_owner_is_seeded(self) -> None:
        state = _run_seed_session_state({}, instance_owner="spouse")
        assert state.get("instance_owner") == "spouse"

    def test_unset_instance_owner_seeds_none(self) -> None:
        state = _run_seed_session_state({}, instance_owner=None)
        assert state.get("instance_owner") is None

    def test_corrupt_instance_owner_degrades_to_none(self) -> None:
        state = _run_seed_session_state({}, instance_owner=_CORRUPT)
        assert state.get("instance_owner") is None
```

- [ ] Run it and confirm it FAILS — `_seed_session_state` doesn't reference `load_instance_owner` yet, so `state.get("instance_owner")` is `None` in the "spouse" case too (assertion fails, no crash):

```bash
pixi run -e ci python -m pytest tests/test_app_seed_session_state.py -q -rE -k InstanceOwner
```

- [ ] In `app.py`, add the import to the existing `noqa: E402` block (:13-25), between `engine.data_sources.record` and `engine.irmaa` (alphabetical: `instance_identity` < `irmaa`):

```python
from engine.instance_identity import CorruptInstanceOwnerError, load_instance_owner  # noqa: E402
```

- [ ] In `_seed_session_state()` (:28-83), add this immediately before the final `st.session_state.setdefault("_seeded", True)` line:

```python
    # 2026-08-29: instance identity is set once per machine/install (see
    # engine.instance_identity) and is never silently re-derived on a
    # corrupt cache -- a corrupt file degrades to None here (same "unset"
    # treatment as a first-run install) so Command Center's identity gate
    # (views/setup/command_center.py) can re-prompt instead of the whole
    # app crashing on session start.
    try:
        st.session_state.setdefault("instance_owner", load_instance_owner())
    except CorruptInstanceOwnerError:
        st.session_state.setdefault("instance_owner", None)
```

- [ ] Run the test file again and confirm all tests (old + new) pass:

```bash
pixi run -e ci python -m pytest tests/test_app_seed_session_state.py -q -rE
```

- [ ] Lint and type-check:

```bash
pixi run -e ci lint
pixi run -e ci type-check
```

- [ ] Commit:

```
mcp__git__execute_tool("git_add", {"repo_path": ".", "files": ["app.py", "tests/test_app_seed_session_state.py"]})
mcp__git__execute_tool("git_commit", {"repo_path": ".", "message": "feat(instance-identity): seed instance_owner in app.py session state"})
```

---

### Task 5: Command Center identity gate

**Files:**
- Modify: `views/setup/command_center.py`
- Test: `tests/test_command_center_view.py`

- [ ] Append the failing tests to `tests/test_command_center_view.py`, mirroring its existing `AppTest.from_function` pattern:

```python
def test_command_center_identity_unset_shows_gate_and_disables_sync(
    clean_command_center_caches,
) -> None:
    def _render() -> None:
        import streamlit as st

        from models.household import Household
        from views.setup.command_center import render_command_center

        st.session_state["_pending_review"] = set()
        render_command_center(Household())

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    assert len(at.radio) == 1
    sync_button = next(b for b in at.button if b.key == "sync_everything_btn")
    assert sync_button.disabled is True


def test_command_center_identity_set_hides_gate_and_enables_sync(
    clean_command_center_caches,
) -> None:
    def _render() -> None:
        import streamlit as st

        from models.household import Household
        from views.setup.command_center import render_command_center

        st.session_state["_pending_review"] = set()
        st.session_state["instance_owner"] = "you"
        render_command_center(Household())

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    assert len(at.radio) == 0
    sync_button = next(b for b in at.button if b.key == "sync_everything_btn")
    assert sync_button.disabled is False
```

- [ ] Run them and confirm both FAIL (the gate doesn't exist yet — no radio renders, `sync_everything_btn` has no `disabled` kwarg so `disabled is True` fails):

```bash
pixi run -e ci python -m pytest tests/test_command_center_view.py -q -rE -k identity
```

- [ ] In `views/setup/command_center.py`, add the import alongside the existing engine imports (:30-36):

```python
from engine.instance_identity import CorruptInstanceOwnerError, load_instance_owner, save_instance_owner
```

- [ ] Modify `render_command_center` (:60-97): insert the identity gate right after `st.header(...)`, and pass `disabled=` to the sync button. Replace:

```python
    st.header("🎛️ Command Center")

    if st.button("⟳ Sync everything", key="sync_everything_btn"):
```

with:

```python
    st.header("🎛️ Command Center")

    try:
        instance_owner = st.session_state.get("instance_owner") or load_instance_owner()
    except CorruptInstanceOwnerError:
        instance_owner = None
    identity_set = bool(instance_owner)

    if not identity_set:
        st.warning(
            "This planner instance has no owner set yet. Scanning and "
            "syncing are unavailable until you answer below."
        )
        choice = st.radio(
            "Which person's data does this planner instance hold?",
            ["Me", "Spouse"],
            key="instance_owner_gate_choice",
        )
        if st.button("Save", key="instance_owner_gate_save"):
            resolved_owner = "you" if choice == "Me" else "spouse"
            save_instance_owner(resolved_owner)
            st.session_state["instance_owner"] = resolved_owner
            st.rerun()

    # disabled=True (not hidden) while identity is unset -- a hidden control
    # is indistinguishable from a missing feature (see views/planner.py's
    # column_config disabled=True convention for the same "visible but
    # inert" preference over hiding a widget entirely).
    if st.button("⟳ Sync everything", key="sync_everything_btn", disabled=not identity_set):
```

- [ ] Run the two new tests and confirm they PASS:

```bash
pixi run -e ci python -m pytest tests/test_command_center_view.py -q -rE -k identity
```

- [ ] Run the full Command Center test file to confirm no regression in the existing pending-review/sync-summary tests:

```bash
pixi run -e ci python -m pytest tests/test_command_center_view.py -q -rE
```

- [ ] Lint and type-check:

```bash
pixi run -e ci lint
pixi run -e ci type-check
```

- [ ] Commit:

```
mcp__git__execute_tool("git_add", {"repo_path": ".", "files": ["views/setup/command_center.py", "tests/test_command_center_view.py"]})
mcp__git__execute_tool("git_commit", {"repo_path": ".", "message": "feat(instance-identity): gate Command Center sync on instance owner"})
```

---

### Task 6: Replace the three owner-resolution paths in `_sync_scan.py`

**Files:**
- Modify: `views/ytd_income/_partials/_sync_scan.py`
- Test: `tests/test_ytd_shell.py` (or a new `tests/test_sync_scan_owner_resolution.py` — either is fine, reuse the `_run_ytd`/`_render_ytd` harness from `tests/test_ytd_shell.py`)

**CRITICAL WARNING — read before editing:** the selectbox at `_sync_scan.py:300-321` (`key=f"account_type_confirm_{account_number}"`) is the account TAX-STATUS confirm (taxable / traditional_ira / roth_ira), NOT an owner selectbox. It must NOT be touched or deleted. Only the two owner selectboxes go: `brokerage_owner_confirm_{account_number}` / `brokerage_owner_correct_{account_number}` (:150-175) and `koinly_owner_confirm_{report.captured_at}` / `koinly_owner_correct_{report.captured_at}` (:191-219).

**These tests MUST actually click "Scan folder".** `_run_ytd`'s default mocks leave `by_account` empty, so a test that only calls `_run_ytd(...)` never enters the owner-selectbox branch at all and passes identically before AND after this change — a worthless test. Every negative assertion below is therefore paired with a positive control (`"Imported:"` success banner) proving the scan branch really executed.

- [ ] Append these shared fixture builders to `tests/test_ytd_shell.py` (they feed `run_folder_scan`, which `_sync_scan.py` imports at module level from `views._shared`, so it is monkeypatchable on `sync_scan_mod` itself; the `engine.brokerage_statement_pdf` helpers are imported INSIDE `render_sync_scan_partial`, so those must be patched on that engine module instead):

```python
def _canned_scan_result(brokerage_records=(), koinly_reports=()):
    """A ScanIngestResult whose ``.raw`` carries the given parsed records.

    ``run_folder_scan`` returns ScanIngestResult and ``_sync_scan.py`` reads
    ``.raw`` (a PdfImportResult) off it -- see engine/data_sources/scan_ingest.py.
    """
    from engine.data_sources.scan_ingest import ScanIngestResult
    from engine.pdf_import import PdfImportResult

    raw = PdfImportResult(
        brokerage_records=list(brokerage_records),
        koinly_reports=list(koinly_reports),
    )
    return ScanIngestResult(
        brokerage_count=len(raw.brokerage_records),
        form_1040_count=0,
        koinly_count=len(raw.koinly_reports),
        skipped_count=0,
        unrecognized_count=0,
        magi_candidates_recorded=0,
        errors=[],
        raw=raw,
        pdf_cache={},
    )


def _brokerage_record(account_number="****-*123", account_type="taxable", owner_key=None):
    from engine.brokerage_statement_pdf import BrokerageStatementRecord

    return BrokerageStatementRecord(
        account_number=account_number,
        broker="schwab",
        account_type=account_type,
        statement_period_end="2026-06-30",
        interest_taxable_ytd=10.0,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=20.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-06-30T00:00:00",
        owner_key=owner_key,
    )


def _koinly_report(owner_key=None):
    from engine.koinly_report_pdf import KoinlyReport

    return KoinlyReport(
        tax_year=2026,
        crypto_stcg=100.0,
        crypto_ltcg=200.0,
        crypto_income=50.0,
        captured_at="2026-06-30T00:00:00",
        owner_key=owner_key,
    )


def _patch_scan(monkeypatch, tmp_path, *, brokerage_records=(), koinly_reports=()):
    """Make the "Scan folder" button branch runnable and write-free."""
    import engine.brokerage_statement_pdf as brokerage_statement_pdf_mod
    import engine.koinly_report_pdf as koinly_report_pdf_mod
    from views.ytd_income._partials import _sync_scan as sync_scan_mod

    monkeypatch.setattr(
        brokerage_statement_pdf_mod, "validate_local_folder", lambda raw: (tmp_path, None)
    )
    monkeypatch.setattr(brokerage_statement_pdf_mod, "save_statement_folder_path", lambda p: None)
    monkeypatch.setattr(brokerage_statement_pdf_mod, "save_statement_records", lambda d: None)
    monkeypatch.setattr(brokerage_statement_pdf_mod, "load_account_type_overrides", lambda: {})
    monkeypatch.setattr(koinly_report_pdf_mod, "save_koinly_report", lambda r: None)
    monkeypatch.setattr(sync_scan_mod, "save_ledger", lambda ledger: None)
    monkeypatch.setattr(sync_scan_mod, "save_ytd_snapshot", lambda snap: None)
    monkeypatch.setattr(
        sync_scan_mod,
        "run_folder_scan",
        lambda folder_path: _canned_scan_result(
            brokerage_records=brokerage_records, koinly_reports=koinly_reports
        ),
    )


def _scan(at):
    """Click "Scan folder" and rerun. Requires instance_owner already set --
    the button is disabled while identity is unset (see the gating step below).
    """
    at.button(key="scan_pdf_folder_btn").click().run()
    return at
```

- [ ] Append the failing behavior tests to the same file:

```python
def test_no_brokerage_owner_selectbox_renders_after_scan(monkeypatch, tmp_path) -> None:
    _patch_scan(monkeypatch, tmp_path, brokerage_records=[_brokerage_record(owner_key="Jane Doe")])
    at = _run_ytd(monkeypatch, snapshot_date=None, ui_theme="Classic")
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    # Positive control: the scan branch really ran (otherwise the negative
    # assertions below would pass trivially).
    assert any(s.value.startswith("Imported:") for s in at.success)
    assert not any(w.key == "brokerage_owner_confirm_****-*123" for w in at.selectbox)
    assert not any(w.key == "brokerage_owner_correct_****-*123" for w in at.selectbox)


def test_no_koinly_owner_selectbox_renders_after_scan(monkeypatch, tmp_path) -> None:
    _patch_scan(monkeypatch, tmp_path, koinly_reports=[_koinly_report(owner_key="Jane Doe")])
    at = _run_ytd(monkeypatch, snapshot_date=None, ui_theme="Classic")
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    assert any(s.value.startswith("Imported:") for s in at.success)
    assert not any(w.key == "koinly_owner_confirm_2026-06-30T00:00:00" for w in at.selectbox)
    assert not any(w.key == "koinly_owner_correct_2026-06-30T00:00:00" for w in at.selectbox)


def test_account_type_confirm_selectbox_still_renders_for_unknown_tax_status(
    monkeypatch, tmp_path
) -> None:
    """The tax-status confirm selectbox is NOT an owner prompt and must survive.

    ``"unknown"`` IS a valid ``ACCOUNT_TYPES`` member
    (engine/brokerage_statement_pdf.py:80), so the fixture states it directly.
    """
    _patch_scan(
        monkeypatch,
        tmp_path,
        brokerage_records=[_brokerage_record(account_number="****-*999", account_type="unknown")],
    )
    at = _run_ytd(monkeypatch, snapshot_date=None, ui_theme="Classic")
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    assert any(w.key == "account_type_confirm_****-*999" for w in at.selectbox)
```

- [ ] Append the scan-gating tests (spec requirement: scan is unavailable while `instance_owner` is unset, exactly like Command Center's sync button in Task 5):

```python
def test_scan_button_disabled_when_instance_owner_unset(monkeypatch) -> None:
    at = _run_ytd(monkeypatch, snapshot_date=None, ui_theme="Classic")

    assert not at.exception
    assert next(b for b in at.button if b.key == "scan_pdf_folder_btn").disabled is True


def test_scan_button_enabled_when_instance_owner_set(monkeypatch) -> None:
    at = _run_ytd(monkeypatch, snapshot_date=None, ui_theme="Classic")
    at.session_state["instance_owner"] = "you"
    at.run()

    assert not at.exception
    assert next(b for b in at.button if b.key == "scan_pdf_folder_btn").disabled is False
```

- [ ] Run and confirm they currently FAIL — the owner selectboxes still render today, and `scan_pdf_folder_btn` has no `disabled` kwarg so `disabled is True` fails:

```bash
pixi run -e ci python -m pytest tests/test_ytd_shell.py -q -rE -k "owner_selectbox or account_type_confirm or scan_button"
```

- [ ] In `views/ytd_income/_partials/_sync_scan.py`, replace the `engine.pdf_owner` import block (:12-18) with the new resolvers:

```python
from engine.account_attribution import load_account_overrides, resolve_account_owner
from engine.instance_identity import CorruptInstanceOwnerError, load_instance_owner
```

- [ ] At the top of `render_sync_scan_partial` (:26-27), resolve `instance_owner`/`account_overrides` once for the whole render:

```python
def render_sync_scan_partial(hh: Household) -> None:
    # Resolve once per render. "household" is a defensive last-resort only
    # (mirrors the old ad-hoc `or "household"` default this replaces) --
    # Command Center's identity gate (views/setup/command_center.py) is the
    # real prevention for instance_owner being unset by the time a scan runs.
    instance_owner = st.session_state.get("instance_owner")
    if not instance_owner:
        try:
            instance_owner = load_instance_owner()
        except CorruptInstanceOwnerError:
            instance_owner = None
    # identity_set is computed BEFORE the "household" fallback below -- after
    # it, the value is always truthy and the gate would never fire.
    identity_set = bool(instance_owner)
    instance_owner = instance_owner or "household"
    account_overrides = load_account_overrides()

    # --- Section 1: YTD Income Entry ---
    st.markdown("### YTD Income Entry")
```

- [ ] Gate the "Scan folder" button on the same identity, matching Task 5's `disabled=` precedent (visible but inert — never hide the control). Replace `_sync_scan.py:113`:

```python
        if st.button("Scan folder", key="scan_pdf_folder_btn"):
```

with:

```python
        if not identity_set:
            st.caption(
                "Scanning is unavailable until this planner instance has an "
                "owner — set it on **⚙️ Setup ▸ 🎛️ Command Center**."
            )
        # disabled=True (not hidden), same convention as Command Center's
        # "⟳ Sync everything" button in Task 5.
        if st.button("Scan folder", key="scan_pdf_folder_btn", disabled=not identity_set):
```

- [ ] Replace the brokerage owner block (:150-178, from `if stmt_taxable_now:` through `save_owner_map(owner_map)`) with:

```python
                if stmt_taxable_now:
                    for account_number, rec in stmt_taxable_now.items():
                        resolved = resolve_account_owner(
                            rec.broker, account_number, account_overrides, instance_owner
                        )
                        ledger = write_brokerage_contribution(ledger, resolved, rec)

                    save_ledger(ledger)
```

- [ ] Replace the Koinly owner block (**:191-223**, from `if result.koinly_reports:` through and INCLUDING `save_koinly_report(result.koinly_reports[-1])` at :223) with the block below. **Boundary warning:** the replacement text below already ends with its own `save_koinly_report(result.koinly_reports[-1])`. Stopping the replacement at :222 (`save_owner_map(owner_map)`) leaves the original :223 in place and you get that call TWICE — a duplicated Koinly-report write. Delete through :223 inclusive.

```python
                if result.koinly_reports:
                    from engine.koinly_report_pdf import save_koinly_report

                    for report in result.koinly_reports:
                        resolved = resolve_account_owner(
                            "koinly", report.owner_key or "unknown", account_overrides, instance_owner
                        )
                        ledger = write_koinly_contribution(ledger, resolved, report)

                    save_ledger(ledger)
                    save_koinly_report(result.koinly_reports[-1])
```

- [ ] Replace the `owner_map = load_owner_map()` line that preceded the brokerage block (:145) — delete it, it's no longer used in this file until Task 9 reintroduces it read-only.

- [ ] Replace the Apply handler's silent default (:325-330):

```python
                if st.button("Apply to YTD snapshot", key="apply_statements_btn"):
                    owner_map = load_owner_map()
                    for rec in stmt_taxable.values():
                        resolved = resolve_owner(rec.owner_key, owner_map) or "household"
                        ledger = write_brokerage_contribution(ledger, resolved, rec)
                    save_ledger(ledger)
```

with:

```python
                if st.button("Apply to YTD snapshot", key="apply_statements_btn"):
                    for account_number, rec in stmt_taxable.items():
                        resolved = resolve_account_owner(
                            rec.broker, account_number, account_overrides, instance_owner
                        )
                        ledger = write_brokerage_contribution(ledger, resolved, rec)
                    save_ledger(ledger)
```

**Scope note:** this "Apply to YTD snapshot" button is deliberately NOT gated by `identity_set` — like the rest of this file it falls back to `instance_owner or "household"` — because the spec's disable list covers scan/sync/import only, not this button; this is intentional scope, not an oversight.

- [ ] Leave the `if stmt_unknown:` / `key=f"account_type_confirm_{account_number}"` block (:300-321) completely untouched — verify by re-reading it after your edits.

- [ ] Run the five new tests and confirm the negative ones now PASS, the account-type-confirm positive check still passes, and both scan-gating tests pass:

```bash
pixi run -e ci python -m pytest tests/test_ytd_shell.py -q -rE -k "owner_selectbox or account_type_confirm or scan_button"
```

- [ ] Run the full YTD shell + views test files to confirm no regression:

```bash
pixi run -e ci python -m pytest tests/test_ytd_shell.py tests/test_views_ytd_income.py -q -rE
```

- [ ] Lint and type-check (this step will surface any now-unused import, e.g. leftover `OWNER_ROLES`/`resolve_owner`/`learn_owner`/`save_owner_map` references — remove them):

```bash
pixi run -e ci lint
pixi run -e ci type-check
```

- [ ] Commit:

```
mcp__git__execute_tool("git_add", {"repo_path": ".", "files": ["views/ytd_income/_partials/_sync_scan.py", "tests/test_ytd_shell.py"]})
mcp__git__execute_tool("git_commit", {"repo_path": ".", "message": "feat(instance-identity): resolve scan owners from instance and gate the scan button"})
```

---

### Task 7: Attribution table in Command Center

**Files:**
- Modify: `views/setup/command_center.py`
- Test: `tests/test_command_center_view.py`

**Scope note:** FinExtract portfolio accounts are NOT included in this table — `engine/portfolio_sync/shapes.py:80-92`'s `AccountSummary` has no `account_number` field (only `account_type`/`account_name`/`owner`), so it cannot be joined against the statement-PDF ledger's `(broker, account_number)` keys. This table covers statement-derived accounts only.

- [ ] Append a failing test to `tests/test_command_center_view.py`:

```python
def test_attribution_table_lists_statement_accounts_and_allows_owner_edit(
    clean_command_center_caches, monkeypatch
) -> None:
    import views.setup.command_center as command_center_mod

    monkeypatch.setattr(
        command_center_mod,
        "load_statement_records",
        lambda: {
            "****-*123": type(
                "Rec", (), {"broker": "schwab", "account_type": "taxable", "owner_key": None}
            )()
        },
    )

    def _render() -> None:
        import streamlit as st

        from models.household import Household
        from views.setup.command_center import render_command_center

        st.session_state["_pending_review"] = set()
        st.session_state["instance_owner"] = "you"
        render_command_center(Household())

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    assert any("****-*123" in c.value for c in at.caption + at.markdown)
```

- [ ] Run it and confirm it FAILS (no attribution table exists yet):

```bash
pixi run -e ci python -m pytest tests/test_command_center_view.py -q -rE -k attribution_table
```

- [ ] In `views/setup/command_center.py`, add the imports:

```python
from engine.account_attribution import (
    delete_account_override,
    load_account_overrides,
    save_account_override,
)
from engine.brokerage_statement_pdf import load_statement_records
```

**Blocker you must handle first — `render_command_center` has an early `return`.** At `views/setup/command_center.py:82-84` the function does:

```python
    if not pending:
        st.success("All data sources reconciled ✓")
        return
```

and the function body then ends with the card loop at `:95-97`. So code appended "at the end" is unreachable whenever `_pending_review` is empty — which is exactly what this task's test (and Task 5's) sets up. You MUST convert that early return into an `if/else` first, or your new test will fail for a reason that has nothing to do with your code.

- [ ] Add a new render helper and call it from `render_command_center`, after the existing pending-review loop (end of the function, :95-97):

```python
def _render_attribution_table(instance_owner: str) -> None:
    """List statement-derived accounts with their resolved owner and tax
    status, letting the user set/clear a per-account override. FinExtract
    portfolio accounts are NOT included -- see this task's scope note."""
    by_account = load_statement_records()
    if not by_account:
        return
    overrides = load_account_overrides()
    st.subheader("Account attribution")
    for account_number, rec in sorted(by_account.items()):
        resolved = resolve_account_owner(rec.broker, account_number, overrides, instance_owner)
        col_label, col_owner, col_clear = st.columns([3, 2, 1])
        col_label.caption(f"{account_number} ({rec.broker}, {rec.account_type})")
        choice = col_owner.selectbox(
            f"Owner for {account_number}",
            ["you", "spouse", "household"],
            index=["you", "spouse", "household"].index(resolved),
            key=f"attribution_owner_{account_number}",
            label_visibility="collapsed",
        )
        if choice != resolved:
            save_account_override(rec.broker, account_number, choice)
            st.rerun()
        if (rec.broker, account_number) in overrides and col_clear.button(
            "Clear", key=f"attribution_clear_{account_number}"
        ):
            delete_account_override(rec.broker, account_number)
            st.rerun()
```

Add the missing `resolve_account_owner` import alongside the others.

- [ ] Now remove the early `return` so the tail of `render_command_center` is reachable, and call the helper there. Replace the whole block from `if not pending:` (:82) through the end of the function (:97):

```python
    if not pending:
        st.success("All data sources reconciled ✓")
        return

    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    # audit-0809 #11: a corrupt committed cache degrades to {} here (read-time
    # only) — save_committed() is the actual guard against overwriting it.
    try:
        committed_json = load_committed(COMMITTED_PATH) or {}
    except CorruptCommittedCacheError:
        committed_json = {}

    for field_key in sorted(pending):
        with st.container(border=True):
            _render_field_card(field_key, committed_json, store, choices)
```

with:

```python
    if not pending:
        st.success("All data sources reconciled ✓")
    else:
        store = CandidateStore.load(CANDIDATE_STORE_PATH)
        choices = ChoiceMap.load(TRUST_CHOICES_PATH)
        # audit-0809 #11: a corrupt committed cache degrades to {} here (read-time
        # only) — save_committed() is the actual guard against overwriting it.
        try:
            committed_json = load_committed(COMMITTED_PATH) or {}
        except CorruptCommittedCacheError:
            committed_json = {}

        for field_key in sorted(pending):
            with st.container(border=True):
                _render_field_card(field_key, committed_json, store, choices)

    # Reachable in BOTH branches -- this is why the early return above became
    # an else. instance_owner/identity_set come from Task 5's gate at the top
    # of this function.
    if identity_set:
        _render_attribution_table(instance_owner)
```

This is a pure control-flow change: the `if not pending` branch still renders only the success message, and the loop body is byte-identical apart from indentation. The stores are still not loaded when nothing is pending.

END OF REPLACEMENT.

- [ ] Run the new test and confirm it PASSES:

```bash
pixi run -e ci python -m pytest tests/test_command_center_view.py -q -rE -k attribution_table
```

- [ ] Run the full Command Center test file:

```bash
pixi run -e ci python -m pytest tests/test_command_center_view.py -q -rE
```

- [ ] Lint and type-check:

```bash
pixi run -e ci lint
pixi run -e ci type-check
```

- [ ] Commit:

```
mcp__git__execute_tool("git_add", {"repo_path": ".", "files": ["views/setup/command_center.py", "tests/test_command_center_view.py"]})
mcp__git__execute_tool("git_commit", {"repo_path": ".", "message": "feat(instance-identity): add per-account attribution table to Command Center"})
```

---

### Task 8: Bundle export/import symmetry

**Files:**
- Modify: `views/setup/data_bridge.py`
- Test: `tests/test_shells.py`

**There is no `tests/test_data_bridge_view.py` — do not create one.** `tests/test_shells.py` is the only file that exercises `views/setup/data_bridge.py` under `AppTest`, and it does so ONLY via `monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)` so the embedding Setup shell renders — there is no export or Apply harness anywhere in this repo, and nothing clicks "Apply". So this task must BUILD a new `AppTest.from_function` harness in `tests/test_shells.py`, written in the `_render_ytd`/`_run_shell` style (all imports inside the target function; the target's own source is exec'd in a fresh namespace, so no module-level names from the test file are visible inside it). Copy the `load_pubkey` monkeypatch idiom from `_run_shell` (:122-128).

**The import "Apply" path cannot be driven end-to-end under `AppTest`** — it requires `bundle_file is not None` from an `st.file_uploader`, which `AppTest` cannot populate. So the target-owner derivation is extracted into a testable module-level helper (`_import_target_owner`, added below) and the test asserts on the helper's value plus the absence of the `pc_role` radio, rather than on an `apply_bundle(...)` call it cannot trigger. Removing the `pc_role` radio also leaves a second, untested reference to it at `data_bridge.py:348` (the post-Apply success message) — see the dedicated sub-step below for that edit; no test in this file can catch a miss there.

- [ ] Append the failing export/import tests to `tests/test_shells.py`:

```python
def test_export_stamps_owner_from_instance(monkeypatch) -> None:
    """Export from a spouse-set instance stamps owner="spouse" in the bundle,
    not the old hardcoded "you"."""
    from streamlit.testing.v1 import AppTest

    import engine.bridge_bundle as bridge_bundle_mod
    import engine.data_bridge_crypto as data_bridge_crypto_mod
    import views.setup.data_bridge as data_bridge_mod

    captured: dict[str, object] = {}

    def _fake_build_bundle(scalars, snapshot, ledger, *, owner="you", ytd=None, grants=None):
        captured["owner"] = owner
        return {"format_version": 4}

    # build_bundle/seal are imported INSIDE _handle_personal_exports (deferred
    # for Pyodide), so they must be patched on their defining modules.
    monkeypatch.setattr(bridge_bundle_mod, "build_bundle", _fake_build_bundle)
    monkeypatch.setattr(data_bridge_crypto_mod, "seal", lambda payload, pubkey: b"sealed")
    monkeypatch.setattr(data_bridge_mod, "_resolved_pubkey", lambda: b"\x00" * 32)
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "_load_pdf_ledger", lambda: {"koinly": {}, "brokerage": {}})
    monkeypatch.setattr(data_bridge_mod, "load_ytd_snapshot", lambda: None)

    def _render() -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_exports

        st.session_state["instance_owner"] = "spouse"
        _handle_personal_exports()

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    assert captured["owner"] == "spouse"


def test_import_targets_the_other_person_with_no_radio(monkeypatch) -> None:
    """Import into a "you" instance targets "spouse" automatically; the
    "Whose data?" pc_role radio no longer renders."""
    from streamlit.testing.v1 import AppTest

    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)

    def _render() -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads, _import_target_owner

        st.session_state["instance_owner"] = "you"
        _handle_personal_uploads()
        # Stashed for assertion: AppTest cannot populate the file_uploader, so
        # the Apply body never runs -- the derivation helper is checked directly.
        st.session_state["_test_target_owner"] = _import_target_owner()

    at = AppTest.from_function(_render)
    at.run()

    assert not at.exception
    assert not any(w.key == "pc_role" for w in at.radio)
    assert at.session_state["_test_target_owner"] == "spouse"
```

- [ ] Append the import-gating tests (spec requirement: import is unavailable while `instance_owner` is unset, same `disabled=` precedent as Task 5's sync button and Task 6's scan button):

```python
def _run_uploads(monkeypatch, instance_owner: str | None):
    from streamlit.testing.v1 import AppTest

    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)

    def _render(owner: str | None = None) -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads

        if owner is not None:
            st.session_state["instance_owner"] = owner
        _handle_personal_uploads()

    at = AppTest.from_function(_render, kwargs={"owner": instance_owner})
    at.run()
    return at


def test_apply_uploads_disabled_when_instance_owner_unset(monkeypatch) -> None:
    at = _run_uploads(monkeypatch, None)

    assert not at.exception
    assert next(b for b in at.button if b.key == "apply_uploads").disabled is True


def test_apply_uploads_enabled_when_instance_owner_set(monkeypatch) -> None:
    at = _run_uploads(monkeypatch, "you")

    assert not at.exception
    assert next(b for b in at.button if b.key == "apply_uploads").disabled is False
```

- [ ] Run them and confirm they FAIL — `_import_target_owner` does not exist yet (ImportError), export hardcodes `owner="you"`, a `pc_role` radio still renders, and `apply_uploads` has no `disabled` kwarg:

```bash
pixi run -e ci python -m pytest tests/test_shells.py -q -rE -k "export_stamps_owner or import_targets or apply_uploads"
```

- [ ] In `views/setup/data_bridge.py`, add the import (the module imports nothing from `engine.instance_identity` today) alongside the other engine imports (:11-25, alphabetically between `engine.data_sources.record` and `engine.pdf_ledger`):

```python
from engine.instance_identity import CorruptInstanceOwnerError, load_instance_owner
```

- [ ] Add the two module-level helpers just above `_handle_personal_uploads` (:224):

```python
def _this_instance_owner() -> str | None:
    """This instance's owner, or None when unset/corrupt.

    A corrupt file degrades to None (same "unset" treatment as a first-run
    install) rather than crashing the tab -- Command Center's identity gate
    is where the user fixes it (see app.py's _seed_session_state, Task 4).
    """
    try:
        return st.session_state.get("instance_owner") or load_instance_owner()
    except CorruptInstanceOwnerError:
        return None


def _import_target_owner() -> str:
    """An imported bundle always belongs to the OTHER household member.

    Replaces the "Whose data?" pc_role radio: this instance's own identity is
    already known, so asking again only invites a mis-click that overwrites
    the wrong owner's slot. Defaults to a "you" instance (so imports target
    "spouse") when identity is unset -- the Apply button is disabled in that
    state anyway.
    """
    return "spouse" if (_this_instance_owner() or "you") == "you" else "you"
```

- [ ] At the export call site (:476), replace:

```python
            bundle = build_bundle(scalars, snapshot, ledger, owner="you", ytd=ytd, grants=grants)
```

with:

```python
            export_owner = _this_instance_owner() or "you"
            bundle = build_bundle(scalars, snapshot, ledger, owner=export_owner, ytd=ytd, grants=grants)
```

- [ ] At the import block (:247-259), remove the `pc_role` radio:

```python
        pc_role = st.radio(
            "Whose data?",
            ["Me", "Spouse"],
            horizontal=True,
            key="pc_role",
        )
```

and replace its caption line's mention of the toggle:

```python
        st.caption(
            "Upload your encrypted bundle for a personalized session. "
            "Values stay in this browser only; refresh = back to demo. "
            "`.enc` files require the private key configured above. "
            "An imported bundle is automatically attributed to the other "
            "household member -- this instance's own identity never changes."
        )
```

- [ ] `pc_role` is also referenced later in this same block, at `data_bridge.py:348`, inside the success message printed after a successful bundle apply:

```python
                    st.success(f"Applied: {bundle_file.name} ({pc_role.lower()}). Rerunning…")
```

  Replace it with:

```python
                    st.success(f"Applied: {bundle_file.name} ({target_owner}). Rerunning…")
```

  **Warning: this line is NOT covered by any test in this plan.** The Apply body is unreachable under `AppTest` (no `st.file_uploader` support), so none of this task's tests can catch a missed reference here. `target_owner` (assigned at :279, becoming `_import_target_owner()` after the next sub-step below) is in scope at :348 at the same indentation level, and its value is already lowercase ("you"/"spouse"), so `.lower()` is dropped, not kept. Skipping this edit leaves a dangling `pc_role` name that raises `NameError: name 'pc_role' is not defined` — but only on a real upload, silently, since no test exercises the Apply body.

- [ ] At the import target-owner derivation (:279), replace:

```python
                    target_owner = "spouse" if pc_role == "Spouse" else "you"
```

with:

```python
                    target_owner = _import_target_owner()
```

- [ ] Gate the Apply button on identity, same `disabled=` precedent as Task 5 (visible but inert — never hide it). Replace `data_bridge.py:265-266`:

```python
        col_a, col_b = st.columns(2)
        if col_a.button("Apply", key="apply_uploads", use_container_width=True) and bundle_file is not None:
```

with:

```python
        identity_set = bool(_this_instance_owner())
        if not identity_set:
            st.caption(
                "Importing is unavailable until this planner instance has an "
                "owner — set it on **🎛️ Command Center**."
            )
        col_a, col_b = st.columns(2)
        apply_clicked = col_a.button(
            "Apply",
            key="apply_uploads",
            use_container_width=True,
            disabled=not identity_set,
        )
        if apply_clicked and bundle_file is not None:
```

- [ ] Run the new tests and confirm they PASS:

```bash
pixi run -e ci python -m pytest tests/test_shells.py -q -rE -k "export_stamps_owner or import_targets or apply_uploads"
```

- [ ] Run the full shells test file to confirm the existing Setup-shell tests still pass:

```bash
pixi run -e ci python -m pytest tests/test_shells.py -q -rE
```

- [ ] Lint and type-check:

```bash
pixi run -e ci lint
pixi run -e ci type-check
```

- [ ] Commit:

```
mcp__git__execute_tool("git_add", {"repo_path": ".", "files": ["views/setup/data_bridge.py", "tests/test_shells.py"]})
mcp__git__execute_tool("git_commit", {"repo_path": ".", "message": "feat(instance-identity): derive bundle owner from instance and gate import on identity"})
```

---

### Task 9: Holder-name cross-check

**Files:**
- Modify: `views/ytd_income/_partials/_sync_scan.py`
- Test: `tests/test_ytd_shell.py`

**Rule to hold onto:** WARN, NEVER BLOCK. Silence never means agreement — it only means no name was available to check (IBKR/Fidelity/UBS statements return `owner_key=None` today; only Schwab and Vanguard populate it).

- [ ] Append failing tests to `tests/test_ytd_shell.py`, reusing Task 6's `_patch_scan`/`_brokerage_record`/`_scan` helpers:

```python
def _mismatch_warnings(at) -> list[str]:
    """Only the holder-name cross-check warnings (its wording is unique --
    the scan's other st.warning calls are about unrecognized/failed files)."""
    return [w.value for w in at.warning if "holder name" in w.value]


def test_holder_name_mismatch_warns_but_does_not_block(monkeypatch, tmp_path) -> None:
    from views.ytd_income._partials import _sync_scan as sync_scan_mod

    # The name says "spouse"; this instance attributes the account to "you".
    monkeypatch.setattr(sync_scan_mod, "load_owner_map", lambda: {"jane doe": "spouse"})
    _patch_scan(monkeypatch, tmp_path, brokerage_records=[_brokerage_record(owner_key="Jane Doe")])
    at = _run_ytd(monkeypatch, snapshot_date=None, ui_theme="Classic")
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    warnings = _mismatch_warnings(at)
    assert any("****-*123" in text and "spouse" in text for text in warnings)
    # WARN, NEVER BLOCK: the scan still ran to completion and still applied
    # the account to the YTD snapshot.
    assert any(s.value.startswith("Imported:") for s in at.success)
    assert any(s.value.startswith("Applied to YTD snapshot:") for s in at.success)


def test_holder_name_match_is_silent(monkeypatch, tmp_path) -> None:
    from views.ytd_income._partials import _sync_scan as sync_scan_mod

    monkeypatch.setattr(sync_scan_mod, "load_owner_map", lambda: {"jane doe": "you"})
    _patch_scan(monkeypatch, tmp_path, brokerage_records=[_brokerage_record(owner_key="Jane Doe")])
    at = _run_ytd(monkeypatch, snapshot_date=None, ui_theme="Classic")
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    assert any(s.value.startswith("Imported:") for s in at.success)
    assert _mismatch_warnings(at) == []


def test_absent_holder_name_is_silent(monkeypatch, tmp_path) -> None:
    """owner_key=None (IBKR/Fidelity/UBS today) -- absence of a name is not
    evidence of anything, so it must never warn."""
    from views.ytd_income._partials import _sync_scan as sync_scan_mod

    monkeypatch.setattr(sync_scan_mod, "load_owner_map", lambda: {"jane doe": "spouse"})
    _patch_scan(monkeypatch, tmp_path, brokerage_records=[_brokerage_record(owner_key=None)])
    at = _run_ytd(monkeypatch, snapshot_date=None, ui_theme="Classic")
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    assert any(s.value.startswith("Imported:") for s in at.success)
    assert _mismatch_warnings(at) == []
```

- [ ] Run them and confirm they FAIL (no cross-check exists yet):

```bash
pixi run -e ci python -m pytest tests/test_ytd_shell.py -q -rE -k holder_name
```

- [ ] In `views/ytd_income/_partials/_sync_scan.py`, re-add the read-only `owner_map` import (narrowed to cross-checking only, per this plan's architecture note):

```python
from engine.pdf_owner import load_owner_map
```

- [ ] Add a small helper near the top of the module:

```python
def _warn_on_holder_name_mismatch(
    owner_key: str | None, resolved: str, owner_map: dict[str, str], account_label: str
) -> None:
    """WARN, never block. A name absent from owner_map (or no name at all --
    IBKR/Fidelity/UBS return owner_key=None) is silent: silence never means
    agreement, only that there was nothing to check against."""
    from engine.pdf_owner import resolve_owner

    named_owner = resolve_owner(owner_key, owner_map)
    if named_owner is not None and named_owner != resolved:
        st.warning(
            f"Account {account_label}: the statement's holder name maps to "
            f"'{named_owner}' but this instance attributes it to '{resolved}'. "
            "Double check the account attribution table on Setup ▸ Command Center."
        )
```

- [ ] In `render_sync_scan_partial`, load `owner_map` once near the top (alongside `account_overrides`):

```python
    owner_map = load_owner_map()
```

- [ ] In the brokerage block from Task 6, add the cross-check call right after resolving `resolved`:

```python
                if stmt_taxable_now:
                    for account_number, rec in stmt_taxable_now.items():
                        resolved = resolve_account_owner(
                            rec.broker, account_number, account_overrides, instance_owner
                        )
                        _warn_on_holder_name_mismatch(rec.owner_key, resolved, owner_map, account_number)
                        ledger = write_brokerage_contribution(ledger, resolved, rec)
```

- [ ] Do the same in the Koinly block:

```python
                    for report in result.koinly_reports:
                        resolved = resolve_account_owner(
                            "koinly", report.owner_key or "unknown", account_overrides, instance_owner
                        )
                        _warn_on_holder_name_mismatch(
                            report.owner_key, resolved, owner_map, f"Koinly {report.tax_year}"
                        )
                        ledger = write_koinly_contribution(ledger, resolved, report)
```

- [ ] Run the three new tests and confirm they PASS:

```bash
pixi run -e ci python -m pytest tests/test_ytd_shell.py -q -rE -k holder_name
```

- [ ] **Review the two existing entries in `.pdf_owner_map.json`** (both `household`) per this plan's closing note below — confirm during this task whether they were artifacts of the removed `or "household"` default rather than deliberate choices; if a live household account is genuinely joint, add it as an account-level override (`save_account_override(..., "household")`) instead of relying on the name map.

- [ ] Run the full YTD shell + views test files:

```bash
pixi run -e ci python -m pytest tests/test_ytd_shell.py tests/test_views_ytd_income.py -q -rE
```

- [ ] Lint and type-check:

```bash
pixi run -e ci lint
pixi run -e ci type-check
```

- [ ] Run both sharded full-suite commands from "Verification commands" above and confirm both are green.

- [ ] Commit:

```
mcp__git__execute_tool("git_add", {"repo_path": ".", "files": ["views/ytd_income/_partials/_sync_scan.py", "tests/test_ytd_shell.py"]})
mcp__git__execute_tool("git_commit", {"repo_path": ".", "message": "feat(instance-identity): warn-only holder-name cross-check against resolved owner"})
```

---

## Deferred — not implemented by this plan

- Changing `instance_owner` after data has been scanned: already-ingested ledger rows keep their old attribution and will not move. Decide before shipping a UI that permits the change.
- Adding an override for an account never yet scanned.
- Bulk accept-all for first-run mismatches: NOT needed — `.pdf_owner_map.json` was measured and holds exactly 2 entries, both `household`, so first run yields at most 2 flagged mismatches.

## Note on the two existing `household` entries

`.pdf_owner_map.json` holds 2 entries, both `household`, though the household has no jointly-titled accounts. These are most likely artifacts of the silent `or "household"` default at `_sync_scan.py:328` that Task 6 removes, not deliberate choices. They are invisible today because `derive_brokerage_totals` sums all owner slots into one household-wide figure, but would start affecting numbers once YTD income is partitioned by owner in a later sub-project. Review them during Task 6.
