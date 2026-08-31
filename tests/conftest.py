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
import engine.brokerage_statement_pdf as _brokerage_mod  # noqa: E402
import engine.data_sources.paths as _paths_mod  # noqa: E402
import engine.data_sources.record as _record_mod  # noqa: E402
import engine.data_sources.scan_ingest as _scan_ingest_mod  # noqa: E402
import engine.exercise_schedule_store as _exercise_schedule_store_mod  # noqa: E402
import engine.instance_identity as _instance_identity_mod  # noqa: E402
import engine.koinly_report_pdf as _koinly_mod  # noqa: E402
import engine.pdf_ledger as _pdf_ledger_mod  # noqa: E402
import engine.pdf_owner as _pdf_owner_mod  # noqa: E402
import engine.portfolio_sync as _portfolio_sync_pkg  # noqa: E402
import engine.portfolio_sync.portfolio as _portfolio_mod  # noqa: E402
import engine.portfolio_sync.social_security as _social_security_mod  # noqa: E402
import engine.portfolio_sync.ytd as _ytd_mod  # noqa: E402
import engine.tax_return_pdf as _tax_return_pdf_mod  # noqa: E402
import views._shared as _views_shared_mod  # noqa: E402
import views.option_exercise._partials._helpers as _option_exercise_helpers_mod  # noqa: E402
import views.setup._partials._governance as _setup_governance_mod  # noqa: E402
import views.setup.command_center as _command_center_mod  # noqa: E402

_WATCHED_CACHE_PATHS: list[Path] = [
    _tax_return_pdf_mod._PDF_TAX_CACHE_PATH,
    _ytd_mod._YTD_CACHE_PATH,
    _portfolio_mod._CACHE_PATH,
    _social_security_mod._SSA_CACHE_PATH,
    _koinly_mod._KOINLY_CACHE_PATH,
    _brokerage_mod._STATEMENT_CACHE_PATH,
    _brokerage_mod._FOLDER_CONFIG_PATH,
    _brokerage_mod._ACCOUNT_TYPE_OVERRIDES_PATH,
    _pdf_owner_mod._OWNER_MAP_PATH,
    _pdf_ledger_mod._LEDGER_PATH,
    _paths_mod.CANDIDATE_STORE_PATH,
    _paths_mod.TRUST_CHOICES_PATH,
    _paths_mod.COMMITTED_PATH,
    _exercise_schedule_store_mod._EXERCISE_SCHEDULE_CACHE_PATH,
    _config_loader_mod._USER_DEFAULTS_PATH.resolve(),
    _account_attribution_mod._ACCOUNT_ATTRIBUTION_PATH,
    _instance_identity_mod.INSTANCE_OWNER_PATH,
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
            if p.default is not inspect.Parameter.empty and p.kind is not inspect.Parameter.KEYWORD_ONLY
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
    monkeypatch.setattr(_tax_return_pdf_mod, "_PDF_TAX_CACHE_PATH", _tmp(".tax_pdf_cache.json"))
    monkeypatch.setattr(_ytd_mod, "_YTD_CACHE_PATH", _tmp(".ytd_cache.json"))
    monkeypatch.setattr(_portfolio_mod, "_CACHE_PATH", _tmp(".portfolio_cache.json"))
    monkeypatch.setattr(_social_security_mod, "_SSA_CACHE_PATH", _tmp(".ssa_cache.json"))
    monkeypatch.setattr(_koinly_mod, "_KOINLY_CACHE_PATH", _tmp(".koinly_cache.json"))
    monkeypatch.setattr(_brokerage_mod, "_STATEMENT_CACHE_PATH", _tmp(".brokerage_statement_cache.json"))
    monkeypatch.setattr(_brokerage_mod, "_FOLDER_CONFIG_PATH", _tmp(".statement_folder_config.json"))
    monkeypatch.setattr(_brokerage_mod, "_ACCOUNT_TYPE_OVERRIDES_PATH", _tmp(".statement_account_overrides.json"))
    monkeypatch.setattr(_pdf_owner_mod, "_OWNER_MAP_PATH", _tmp(".pdf_owner_map.json"))
    monkeypatch.setattr(_pdf_ledger_mod, "_LEDGER_PATH", _tmp(".pdf_import_ledger.json"))
    monkeypatch.setattr(_paths_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(_paths_mod, "TRUST_CHOICES_PATH", _tmp(".trust_choices.json"))
    monkeypatch.setattr(_paths_mod, "COMMITTED_PATH", _tmp(".committed_household.json"))
    monkeypatch.setattr(
        _exercise_schedule_store_mod, "_EXERCISE_SCHEDULE_CACHE_PATH", _tmp(".exercise_schedule_cache.json")
    )
    monkeypatch.setattr(_config_loader_mod, "_USER_DEFAULTS_PATH", _tmp(".user_defaults.json"))
    monkeypatch.setattr(_account_attribution_mod, "_ACCOUNT_ATTRIBUTION_PATH", _tmp(".account_attribution.json"))
    monkeypatch.setattr(_instance_identity_mod, "INSTANCE_OWNER_PATH", _tmp(".instance_owner.json"))

    # 2a. engine.portfolio_sync package-level re-exports -- its custom
    # __setattr__ (see engine/portfolio_sync/__init__.py) forwards these
    # writes to the owning submodule too, but patch both ends explicitly.
    monkeypatch.setattr(_portfolio_sync_pkg, "_CACHE_PATH", _tmp(".portfolio_cache.json"))
    monkeypatch.setattr(_portfolio_sync_pkg, "_SSA_CACHE_PATH", _tmp(".ssa_cache.json"))
    monkeypatch.setattr(_portfolio_sync_pkg, "_YTD_CACHE_PATH", _tmp(".ytd_cache.json"))

    # 2b. Other production modules with their own `from ... import <CONST>`
    # binding of CANDIDATE_STORE_PATH / COMMITTED_PATH / TRUST_CHOICES_PATH.
    monkeypatch.setattr(_scan_ingest_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(_record_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(_views_shared_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(_option_exercise_helpers_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(_setup_governance_mod, "COMMITTED_PATH", _tmp(".committed_household.json"))
    monkeypatch.setattr(_setup_governance_mod, "TRUST_CHOICES_PATH", _tmp(".trust_choices.json"))
    monkeypatch.setattr(_command_center_mod, "CANDIDATE_STORE_PATH", _tmp(".candidate_store.json"))
    monkeypatch.setattr(_command_center_mod, "COMMITTED_PATH", _tmp(".committed_household.json"))
    monkeypatch.setattr(_command_center_mod, "TRUST_CHOICES_PATH", _tmp(".trust_choices.json"))

    # 3. Functions whose default parameter value was baked from
    # CANDIDATE_STORE_PATH at their module's import time.
    _patch_default(monkeypatch, _record_mod.record_magi_candidates, "store_path", _tmp(".candidate_store.json"))
    _patch_default(monkeypatch, _record_mod.record_ss_fra_candidate, "store_path", _tmp(".candidate_store.json"))
    _patch_default(monkeypatch, _record_mod.record_txn_quote_candidate, "store_path", _tmp(".candidate_store.json"))
    _patch_default(monkeypatch, _scan_ingest_mod.scan_and_record, "store_path", _tmp(".candidate_store.json"))
    _patch_default(
        monkeypatch, _option_exercise_helpers_mod.handle_txn_quote_fetch, "store_path", _tmp(".candidate_store.json")
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


def _command_center_cache_files() -> list[Path]:
    """The 3 Setup/Command Center cache paths, resolved at CALL time.

    audit-0809 F18: this used to be a module-level ``_COMMAND_CENTER_CACHE_FILES``
    list built from ``_REPO_ROOT`` at CONFTEST IMPORT time, which escaped
    ``_redirect_cache_paths_to_tmp`` (a ``monkeypatch.setattr`` on
    ``engine.data_sources.paths`` cannot reach a value already copied into a
    separate list at import time) and caused ``clean_command_center_caches``
    to unlink the developer's REAL repo-root cache files instead of the
    per-test tmp redirect. Reading ``_paths_mod``'s attributes here instead --
    at the time this function is actually called, always after the autouse
    redirect fixture has run -- reflects whatever it currently points at.
    """
    return [
        _paths_mod.CANDIDATE_STORE_PATH,
        _paths_mod.TRUST_CHOICES_PATH,
        _paths_mod.COMMITTED_PATH,
        _instance_identity_mod.INSTANCE_OWNER_PATH,
        _account_attribution_mod._ACCOUNT_ATTRIBUTION_PATH,
    ]


@pytest.fixture
def clean_command_center_caches():
    """Delete the 3 Setup/Command Center cache files before AND after a test.

    Repo-root-anchored (mirrors the existing ``__file__``-anchored cache-path
    convention used throughout ``engine/*``), so cwd is irrelevant — cleanup
    targets the exact files ``app.py``'s ``get_household()`` writes
    (``.candidate_store.json``, ``.trust_choices.json``,
    ``.committed_household.json``). Deleting BEFORE (not just after) guards
    against a developer's personal committed/candidate state, from running
    ``pixi run app`` locally, leaking into a test's pending-review/migration
    assertions. Shared by every test module that drives the real ``app.py``
    via ``AppTest.from_file`` (``test_app_data_sources.py``,
    ``test_setup_shell_characterization.py``) — keep it here rather than
    re-declaring per-file so behavior can't silently diverge between copies.
    """
    for p in _command_center_cache_files():
        p.unlink(missing_ok=True)
    yield
    for p in _command_center_cache_files():
        p.unlink(missing_ok=True)
