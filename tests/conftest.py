"""Pytest configuration for roth_planner test suite.

Isolates tests from a developer's local .user_defaults.json/.py by setting
ROTH_PLANNER_IGNORE_USER_DEFAULTS before any app module (e.g. models.household)
is imported, since Household's dataclass field defaults are resolved at
import time via config.loader.load_defaults().
"""

import os

os.environ.setdefault("ROTH_PLANNER_IGNORE_USER_DEFAULTS", "1")

import hashlib
import sys
from pathlib import Path

# Add project root to path so `from engine...` and `from models...` work
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent

# --- audit-0805 W1: forbid any test from touching a real repo-root cache ---
# Import the modules that own each repo-root dotfile-cache constant and record
# their CURRENT (literal, pre-any-monkeypatch) value at conftest import time —
# i.e. before any test module has had a chance to run and monkeypatch one of
# these away. This is the ground-truth watch-list; stage 2 adds a redirect
# fixture that patches these same module attributes to a tmp_path so
# production code never reaches the real path, but the watch-list itself
# below is captured once and is immune to that later patching.
import config.loader as _config_loader_mod  # noqa: E402
import engine.account_attribution as _account_attribution_mod  # noqa: E402
import engine.account_link as _account_link_mod  # noqa: E402
import engine.data_sources.paths as _paths_mod  # noqa: E402
import engine.data_sources.record as _record_mod  # noqa: E402
import engine.data_sources.scan_ingest as _scan_ingest_mod  # noqa: E402
import engine.demo_mode as _demo_mode_mod  # noqa: E402
import engine.dividend_forecast as _dividend_forecast_mod  # noqa: E402
import engine.instance_identity as _instance_identity_mod  # noqa: E402
import engine.pdf_owner as _pdf_owner_mod  # noqa: E402
import engine.store as _store_pkg  # noqa: E402
import engine.store.namespaces  # noqa: E402,F401 -- every namespace registered before the copies below
import views._shared as _views_shared_mod  # noqa: E402
import views.option_exercise._partials._helpers as _option_exercise_helpers_mod  # noqa: E402
import views.setup._partials._governance as _setup_governance_mod  # noqa: E402
import views.setup.command_center as _command_center_mod  # noqa: E402

_WATCHED_CACHE_PATHS: list[Path] = [
    _REPO_ROOT / ".tax_pdf_cache.json",
    _REPO_ROOT / ".ytd_cache.json",
    _REPO_ROOT / ".portfolio_cache.json",
    _REPO_ROOT / ".ssa_cache.json",
    _REPO_ROOT / ".ssa_pdf_cache.json",
    _REPO_ROOT / ".koinly_cache.json",
    _REPO_ROOT / ".brokerage_statement_cache.json",
    _REPO_ROOT / ".statement_folder_config.json",
    _REPO_ROOT / ".statement_account_overrides.json",
    _pdf_owner_mod._OWNER_MAP_PATH,
    _REPO_ROOT / ".pdf_import_ledger.json",
    _paths_mod.CANDIDATE_STORE_PATH,
    _paths_mod.TRUST_CHOICES_PATH,
    _REPO_ROOT / ".committed_household.json",
    _REPO_ROOT / ".exercise_schedule_cache.json",
    _config_loader_mod._USER_DEFAULTS_PATH.resolve(),
    _account_attribution_mod._ACCOUNT_ATTRIBUTION_PATH,
    _instance_identity_mod.INSTANCE_OWNER_PATH,
    _REPO_ROOT / ".ubs_activity_cache.json",
    _account_link_mod._ACCOUNT_LINK_PATH,
    _REPO_ROOT / ".dividend_rates.json",
    _demo_mode_mod.DEMO_MODE_PATH,
    _REPO_ROOT / ".roth_planner.db",
    _REPO_ROOT / ".roth_planner.db-wal",
    _REPO_ROOT / ".roth_planner.db-shm",
]


def _snapshot(path: Path) -> tuple[bool, str | None]:
    """Return (exists, sha256-of-bytes-or-None) for *path*."""
    if not path.exists():
        return False, None
    return True, hashlib.sha256(path.read_bytes()).hexdigest()


def _patch_default(monkeypatch: pytest.MonkeyPatch, func, param_name: str, value: Path) -> None:
    """Patch *func*'s *param_name* default value to *value*.

    A plain ``monkeypatch.setattr(module, "SOME_CONST", tmp)`` does NOT
    retroactively change a default value that was already baked into a
    function object's ``__defaults__``/``__kwdefaults__`` at module-import
    time (e.g. ``def f(store_path=CANDIDATE_STORE_PATH)``) — this patches the
    function object itself instead, so callers that omit the argument still
    get redirected.
    """
    import inspect

    param = inspect.signature(func).parameters[param_name]
    if param.kind is inspect.Parameter.KEYWORD_ONLY:
        kwdefaults = dict(func.__kwdefaults__ or {})
        kwdefaults[param_name] = value
        monkeypatch.setattr(func, "__kwdefaults__", kwdefaults)
    else:
        sig = inspect.signature(func)
        defaulted = [
            p.name
            for p in sig.parameters.values()
            if p.default is not inspect.Parameter.empty
            and p.kind is not inspect.Parameter.KEYWORD_ONLY
        ]
        defaults = list(func.__defaults__ or ())
        defaults[defaulted.index(param_name)] = value
        monkeypatch.setattr(func, "__defaults__", tuple(defaults))


@pytest.fixture(autouse=True)
def _redirect_cache_paths_to_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Redirect every repo-root cache-file constant to a per-test tmp dir.

    Runs before ``_forbid_real_cache_writes`` (which depends on it below) so
    production code is already redirected by the time the guard starts its
    before-snapshot. Patches:

    1. The owning module's attribute (what each function reads at call time).
    2. Every OTHER module's own ``from ... import <CONST>`` binding (a
       separate copy, frozen at that module's import time) — found via
       grep across the repo.
    3. Every function whose default parameter value was ALSO baked from one
       of these constants at module-import time (``__defaults__`` /
       ``__kwdefaults__`` do not see attribute patches after the fact).

    Existing per-test ``monkeypatch.setattr(...)`` calls on these same
    targets simply override this fixture's value for that test (whichever
    fixture/test code runs later during setup wins) — nothing here removes
    or conflicts with them.
    """
    cache_dir = tmp_path / "_redirected_caches"
    cache_dir.mkdir(exist_ok=True)

    def _tmp(filename: str) -> Path:
        return cache_dir / filename

    # 1. Defining-module attributes.
    monkeypatch.setattr(_pdf_owner_mod, "_OWNER_MAP_PATH", _tmp(".pdf_owner_map.json"))
    monkeypatch.setattr(_account_link_mod, "_ACCOUNT_LINK_PATH", _tmp(".ira_account_links.json"))
    monkeypatch.setattr(_paths_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(_paths_mod, "TRUST_CHOICES_PATH", _tmp(".trust_choices.json"))
    monkeypatch.setattr(_config_loader_mod, "_USER_DEFAULTS_PATH", _tmp(".user_defaults.json"))
    monkeypatch.setattr(
        _account_attribution_mod, "_ACCOUNT_ATTRIBUTION_PATH", _tmp(".account_attribution.json")
    )
    monkeypatch.setattr(_instance_identity_mod, "INSTANCE_OWNER_PATH", _tmp(".instance_owner.json"))
    monkeypatch.setattr(_demo_mode_mod, "DEMO_MODE_PATH", _tmp(".demo_mode"))

    # The SQLite store file: never let a test reach the repo-root database.
    monkeypatch.setenv("ROTH_PLANNER_DB", str(_tmp(".roth_planner.db")))
    # ...nor read or move a repo-root legacy dotfile during the first-open import.
    monkeypatch.setattr(_store_pkg, "_LEGACY_DIR", cache_dir)

    # 2. Other production modules with their own `from ... import <CONST>`
    # binding of CANDIDATE_STORE_PATH / TRUST_CHOICES_PATH.
    monkeypatch.setattr(_scan_ingest_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(_record_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(_views_shared_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(
        _option_exercise_helpers_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json")
    )
    monkeypatch.setattr(_setup_governance_mod, "TRUST_CHOICES_PATH", _tmp(".trust_choices.json"))
    monkeypatch.setattr(_command_center_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(_command_center_mod, "TRUST_CHOICES_PATH", _tmp(".trust_choices.json"))

    # 3. Functions whose default parameter value was baked from
    # CANDIDATE_STORE_PATH at their module's import time.
    _patch_default(
        monkeypatch, _record_mod.record_magi_candidates, "store_path", _tmp(".candidate_store.json")
    )
    _patch_default(
        monkeypatch,
        _record_mod.record_ss_fra_candidate,
        "store_path",
        _tmp(".candidate_store.json"),
    )
    _patch_default(
        monkeypatch,
        _record_mod.record_stock_quote_candidate,
        "store_path",
        _tmp(".candidate_store.json"),
    )
    _patch_default(
        monkeypatch,
        _record_mod.record_ira_balance_candidate,
        "store_path",
        _tmp(".candidate_store.json"),
    )
    _patch_default(
        monkeypatch,
        _record_mod.record_roth_balance_candidate,
        "store_path",
        _tmp(".candidate_store.json"),
    )
    _patch_default(
        monkeypatch, _scan_ingest_mod.scan_and_record, "store_path", _tmp(".candidate_store.json")
    )
    _patch_default(
        monkeypatch,
        _scan_ingest_mod.scan_documents_and_record,
        "store_path",
        _tmp(".candidate_store.json"),
    )
    _patch_default(
        monkeypatch,
        _option_exercise_helpers_mod.handle_stock_quote_fetch,
        "store_path",
        _tmp(".candidate_store.json"),
    )

    # 3b. forecast_portfolio defaults overrides_path to the cwd-relative, personal,
    # gitignored ".dividend_rates.json" (audit-1018 F-52). Point it at a
    # per-test path that never exists, so no test reads the developer's file.
    _patch_default(
        monkeypatch,
        _dividend_forecast_mod.forecast_portfolio,
        "overrides_path",
        _tmp(".dividend_rates.json"),
    )


def _diff_cache_snapshots(
    before: dict[Path, tuple[bool, str | None]],
    after: dict[Path, tuple[bool, str | None]],
) -> list[str]:
    """Compare *before*/*after* ``{path: (exists, digest_or_None)}`` snapshots
    (same keys in both) and describe every path that was created, modified,
    or deleted, as ``"<path>: created|modified|deleted"`` strings. Returns
    ``[]`` when nothing changed.

    Pure function, no I/O — deliberately factored out of
    ``_forbid_real_cache_writes`` so the create/modify/delete classification
    logic itself has a permanent, real-path-free unit test (see
    ``tests/test_cache_write_guard.py``, audit-0805 W1 follow-up) instead of
    only ever being exercised indirectly through the live guard.
    """
    offenders: list[str] = []
    for p, (existed_before, digest_before) in before.items():
        existed_after, digest_after = after[p]
        if existed_before == existed_after and digest_before == digest_after:
            continue
        if not existed_before and existed_after:
            offenders.append(f"{p}: created")
        elif existed_before and not existed_after:
            offenders.append(f"{p}: deleted")
        else:
            offenders.append(f"{p}: modified")
    return offenders


@pytest.fixture(autouse=True)
def _forbid_real_cache_writes(_redirect_cache_paths_to_tmp):
    """Fail any test that creates, modifies, or deletes a real repo-root cache.

    Snapshots every path in ``_WATCHED_CACHE_PATHS`` (captured once at import
    time, see above) before the test body runs, and again after. Any
    divergence (computed by ``_diff_cache_snapshots``) fails the test loudly,
    naming the offending file(s) and whether it was created, modified, or
    deleted — this is audit-0805 evidence that the suite currently
    reads/writes the developer's real caches (findings C98, C67, C68, C69,
    C99, C100).
    """
    before = {p: _snapshot(p) for p in _WATCHED_CACHE_PATHS}
    yield
    after = {p: _snapshot(p) for p in _WATCHED_CACHE_PATHS}
    offenders = _diff_cache_snapshots(before, after)
    if offenders:
        pytest.fail(
            "Test touched a real repo-root cache file (must be redirected/mocked): "
            + "; ".join(offenders)
        )


@pytest.fixture(autouse=True)
def _isolate_namespaces(monkeypatch):
    """Copy, don't empty: test-time registrations are discarded, import-time ones survive."""
    import engine.store.core as store_core

    monkeypatch.setattr(store_core, "_NAMESPACES", dict(store_core._NAMESPACES))


@pytest.fixture(autouse=True)
def _isolate_legacy_registry(monkeypatch):
    """Copy, don't empty: test-time registrations are discarded, import-time ones survive."""
    import engine.store.legacy_files as store_legacy_files

    monkeypatch.setattr(store_legacy_files, "_REGISTRY", dict(store_legacy_files._REGISTRY))


@pytest.fixture(autouse=True)
def _isolated_store(_redirect_cache_paths_to_tmp):
    """Every test gets a fresh in-memory Store; nothing reaches a real database."""
    import engine.store as store_pkg
    from engine.store.core import Store
    from engine.store.sqlite_backend import MEMORY, SqliteBackend

    store = Store(SqliteBackend(MEMORY))
    store_pkg.set_store(store)
    yield store
    store_pkg.set_store(None)
    store_pkg._reset_cached_stores()
    store.backend.close()


@pytest.fixture
def store(_isolated_store):
    """The test's Store (same object get_store() returns)."""
    return _isolated_store


@pytest.fixture
def file_store(tmp_path):
    """A Store over a real SQLite file in tmp_path, closed at teardown."""
    from engine.store.core import Store
    from engine.store.sqlite_backend import SqliteBackend

    backend = SqliteBackend(tmp_path / "store.db")
    yield Store(backend)
    backend.close()


CORRUPT_PAYLOAD = "{not json"


@pytest.fixture
def corrupt_row(store):
    """``corrupt_row(namespace, key="_")`` stores an undecodable row and returns its payload."""
    from engine.store.backend import Row
    from engine.store.core import SINGLE_KEY

    def _put(namespace: str, key: str = SINGLE_KEY) -> str:
        store.backend.put(namespace, key, Row(CORRUPT_PAYLOAD, 1, "2026-01-01T00:00:00+00:00"))
        return CORRUPT_PAYLOAD

    return _put
