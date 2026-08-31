"""Regression tests for audit-0809 F18: cache paths bound at IMPORT time
escape the autouse per-test redirect fixture (``tests/conftest.py``'s
``_redirect_cache_paths_to_tmp``), because a ``monkeypatch.setattr`` on a
module attribute cannot reach a name already copied into another namespace
via ``from ... import NAME`` (or built from a repo-root constant into a
module-level list) at import time.

FOUR independent DESTRUCTIVE escape sites were found and fixed. The original
diagnosis named sites 1-2; sites 3-4 were discovered by the class-level guard
at the bottom of this file (it failed on its first run, naming both) and
fixed alongside them so the guard isn't vacuous:

Site 1: ``tests/test_scan_ingest.py``'s former module-level
``from engine.data_sources.paths import CANDIDATE_STORE_PATH``, consumed by
the ``clean_candidate_store`` fixture. Fixed: reads
``engine.data_sources.paths.CANDIDATE_STORE_PATH`` at call time.

Site 2: ``tests/conftest.py``'s former ``_COMMAND_CENTER_CACHE_FILES`` list,
built from ``_REPO_ROOT / "..."`` at conftest IMPORT time, consumed by the
``clean_command_center_caches`` fixture. Fixed: ``_command_center_cache_files()``,
a call-time lookup function.

Site 3: ``tests/test_app_data_sources.py``'s former ``_NEW_CACHE_FILES``
list, same shape as site 2, consumed inline by 5 tests doing an explicit
mid-test pre-clean. Fixed: ``_new_cache_files()``, a call-time lookup
function.

Site 4: the SAME file's former module-level
``from engine.portfolio_sync import _CACHE_PATH as _PORTFOLIO_CACHE_PATH``,
unlinked inline in 2 tests. Fixed: reads ``engine.portfolio_sync._CACHE_PATH``
(the package-level re-export the redirect fixture patches) via a module
import, at call time.

One additional module-level import of the same 3 constants,
``tests/test_data_sources.py``'s ``from engine.data_sources.paths import
CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH``, was inspected and
found NOT to be an instance of this defect: both its uses are read-only
identity assertions (``CANDIDATE_STORE_PATH == repo_root / ".candidate_store.json"``
and a ``!=`` comparison) that deliberately need the REAL, pre-redirect value
-- structurally the same "captured once, before any monkeypatch" pattern as
``tests.conftest._WATCHED_CACHE_PATHS`` itself, and never used for I/O. It is
exempted below rather than "fixed" (converting it to call-time resolution
would make the identity assertion fail during every redirected test run).

Sites 1, 2 and 4 are proven here without ever touching a real repo-root
file: instead of letting the fixture/cleanup code actually call
``Path.unlink``, the relevant tests patch ``Path.unlink`` to a no-op
recorder and assert about *which* path object was targeted. Site 3 (not a
fixture, just an inline call-time helper) is proven via path resolution
only.

Guard vacuity precondition: the class-level guard below only "sees"
``tests.*`` modules already present in ``sys.modules`` (see its own
docstring), so an empty offenders list is ambiguous between "no leak" and
"nothing meaningful was imported to check". The guard test therefore
asserts a small, fixed CANARY set of module names is present in
``sys.modules`` BEFORE trusting an empty-offenders result -- all four
canaries are imported by THIS file at module level (three as bound names
below, ``tests.test_data_sources`` via a bare ``importlib.import_module``
call purely for the side effect of populating ``sys.modules``), so the
canary check is deterministic whether the FULL suite or just this file is
run: Python guarantees this module's own top-level imports run before any
test in it executes, in either mode. A "minimum plausible module count"
precondition was considered and rejected -- the plausible minimum differs
by roughly an order of magnitude between an isolated single-file run
(a handful of modules) and a full-suite run (100+), so no single threshold
is both meaningful and non-flaky across both invocation modes.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import engine.data_sources.paths as _paths_mod
import tests.conftest as conftest_mod
import tests.test_app_data_sources as app_data_sources_mod
import tests.test_scan_ingest as scan_ingest_mod

# Imported for the side effect of populating sys.modules with this exact
# name -- it is one of the class-level guard's canary modules (see below)
# and is otherwise only referenced by string in its _EXEMPT set, never
# needing a bound name of its own here.
importlib.import_module("tests.test_data_sources")


def _install_unlink_recorder(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Replace ``Path.unlink`` with a no-op that records the target path
    instead of touching the filesystem, so these tests can drive a fixture's
    setup/teardown phases without any risk to a real file."""
    recorded: list[Path] = []

    def _fake_unlink(self: Path, missing_ok: bool = False) -> None:
        recorded.append(self)

    monkeypatch.setattr(Path, "unlink", _fake_unlink)
    return recorded


def _drive_generator_fixture(fixture_def) -> None:
    """Run both the setup and teardown halves of a ``@pytest.fixture``
    generator function, accessed via its ``__wrapped__`` (the plain function
    ``functools.update_wrapper`` stashed there — pytest's
    ``FixtureFunctionDefinition.__call__`` itself refuses direct calls)."""
    gen = fixture_def.__wrapped__()
    next(gen)
    with pytest.raises(StopIteration):
        next(gen)


class TestSite1ScanIngestFixtureCallTimeResolution:
    def test_clean_candidate_store_targets_redirected_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded = _install_unlink_recorder(monkeypatch)

        _drive_generator_fixture(scan_ingest_mod.clean_candidate_store)

        assert recorded, "fixture did not attempt to unlink anything"
        current_redirected = _paths_mod.CANDIDATE_STORE_PATH
        for p in recorded:
            assert p == current_redirected, (
                f"clean_candidate_store unlinked stale path {p!r}, expected the "
                f"call-time redirected path {current_redirected!r}"
            )


class TestSite2ConftestCommandCenterFixtureCallTimeResolution:
    def test_command_center_cache_files_resolve_under_tmp_path(self, tmp_path: Path) -> None:
        files = conftest_mod._command_center_cache_files()
        repo_root = conftest_mod._REPO_ROOT
        for p in files:
            assert not str(p).startswith(str(repo_root)), (
                f"_command_center_cache_files() returned repo-root path {p!r} "
                "instead of a value redirected under tmp_path"
            )

    def test_clean_command_center_caches_targets_redirected_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded = _install_unlink_recorder(monkeypatch)

        _drive_generator_fixture(conftest_mod.clean_command_center_caches)

        assert recorded, "fixture did not attempt to unlink anything"
        redirected = {
            _paths_mod.CANDIDATE_STORE_PATH,
            _paths_mod.TRUST_CHOICES_PATH,
            _paths_mod.COMMITTED_PATH,
            conftest_mod._instance_identity_mod.INSTANCE_OWNER_PATH,
            conftest_mod._account_attribution_mod._ACCOUNT_ATTRIBUTION_PATH,
        }
        for p in recorded:
            assert p in redirected, (
                f"clean_command_center_caches unlinked stale repo-root path {p!r}, "
                f"expected one of the call-time redirected paths {redirected!r}"
            )


class TestSite3AppDataSourcesHelperCallTimeResolution:
    def test_new_cache_files_resolve_under_tmp_path(self, tmp_path: Path) -> None:
        files = app_data_sources_mod._new_cache_files()
        repo_root = app_data_sources_mod.REPO_ROOT
        for p in files:
            assert not str(p).startswith(str(repo_root)), (
                f"_new_cache_files() returned repo-root path {p!r} instead of "
                "a value redirected under tmp_path"
            )


class TestSite4PortfolioCachePathCallTimeResolution:
    def test_portfolio_cache_path_resolves_under_tmp_path(self, tmp_path: Path) -> None:
        p = app_data_sources_mod._portfolio_sync_pkg._CACHE_PATH
        repo_root = app_data_sources_mod.REPO_ROOT
        assert not str(p).startswith(str(repo_root)), (
            f"engine.portfolio_sync._CACHE_PATH resolved to repo-root path {p!r} "
            "instead of a value redirected under tmp_path"
        )


class TestClassLevelGuardNoTestModuleBindsWatchedCachePathAtImportTime:
    """Closes the CLASS, not just the four fixed instances above: no
    ``tests.*`` module may hold one of ``tests.conftest._WATCHED_CACHE_PATHS``
    (the ground-truth REAL repo-root paths, captured once at conftest import
    time before any redirect) as a bare module-level attribute, or inside a
    module-level list/tuple/set/frozenset. That shape is exactly how all
    four sites above escaped the autouse redirect: a value copied out of a
    production module (or built from a repo-root constant) at IMPORT time
    instead of resolved at call time.

    This guard is proven non-vacuous: on its FIRST run (before this file's
    site-4 fix), it independently caught site 4
    (``tests/test_app_data_sources.py``'s ``_PORTFOLIO_CACHE_PATH``) --
    a real, previously-unnoticed instance the human-authored diagnosis had
    NOT named -- by naming it in its own failure output. It also flagged
    ``tests/test_data_sources.py``'s ``CANDIDATE_STORE_PATH`` /
    ``COMMITTED_PATH`` / ``TRUST_CHOICES_PATH`` import, which on inspection
    turned out to be a deliberate, read-only, non-destructive use (see the
    module docstring) and is exempted below rather than "fixed".

    Exemptions (both deliberate, both documented, neither destructive):
    - ``tests.conftest._WATCHED_CACHE_PATHS`` -- the guard's own ground truth.
    - ``tests.test_data_sources.{CANDIDATE_STORE_PATH,COMMITTED_PATH,
      TRUST_CHOICES_PATH}`` -- read-only identity assertions against the
      real production defaults; never used for I/O.

    Scope / what this does NOT cover (documented rather than pretending
    otherwise, per audit-0805's "vacuous guard" lesson): this only inspects
    module TOP-LEVEL attributes of ``tests.*`` modules already present in
    ``sys.modules``, and one level into list/tuple/set/frozenset values. It
    will NOT see a real path smuggled inside a dict, a nested class
    attribute, a function's default argument value, or a plain string that
    was never converted to a ``Path``. It also only "sees" modules that have
    actually been imported in this process. A dedicated PRECONDITION check
    (below, run first, inside the same test) guards against exactly the
    failure mode of trusting an empty offenders list when too little was
    imported to check -- see the module docstring's "Guard vacuity
    precondition" section for why a fixed canary set (not a raw module
    count) is what's actually deterministic across both a full-suite run
    and this file run in isolation. Not flaky otherwise: pure introspection,
    no I/O, no timing/ordering dependency once a module is imported.
    """

    _EXEMPT: frozenset[tuple[str, str]] = frozenset(
        {
            ("tests.conftest", "_WATCHED_CACHE_PATHS"),
            ("tests.test_data_sources", "CANDIDATE_STORE_PATH"),
            ("tests.test_data_sources", "COMMITTED_PATH"),
            ("tests.test_data_sources", "TRUST_CHOICES_PATH"),
        }
    )

    # Force-imported by this file at module level (see top of file) --
    # guaranteed present in sys.modules whether the full suite or just this
    # file is run, so their presence is a reliable, non-flaky precondition
    # for trusting an empty offenders result below.
    _CANARY_MODULES: tuple[str, ...] = (
        "tests.conftest",
        "tests.test_app_data_sources",
        "tests.test_scan_ingest",
        "tests.test_data_sources",
    )

    @staticmethod
    def _paths_in(value: object) -> list[Path]:
        if isinstance(value, Path):
            return [value]
        if isinstance(value, list | tuple | set | frozenset):
            return [item for item in value if isinstance(item, Path)]
        return []

    def test_no_test_module_binds_a_watched_cache_path_at_module_level(self) -> None:
        missing_canaries = [name for name in self._CANARY_MODULES if name not in sys.modules]
        assert not missing_canaries, (
            "SANITY PRECONDITION FAILED -- this is NOT a real cache-path leak. "
            "The guard below could not see enough tests.* modules in "
            f"sys.modules to be meaningful: expected canary module(s) "
            f"{missing_canaries} to already be imported (this file imports "
            "them itself at module level specifically so this precondition "
            "is deterministic whether the FULL suite or just this file is "
            "run). An empty offenders list from the guard below would be "
            "indistinguishable from 'nothing was imported' otherwise -- fix "
            "whatever broke this file's own top-level imports, do not "
            "delete this precondition."
        )

        watched = set(conftest_mod._WATCHED_CACHE_PATHS)
        offenders: list[str] = []
        for mod_name, mod in list(sys.modules.items()):
            if mod is None or not (mod_name == "tests" or mod_name.startswith("tests.")):
                continue
            for attr_name, value in vars(mod).items():
                if (mod_name, attr_name) in self._EXEMPT:
                    continue
                for p in self._paths_in(value):
                    if p in watched:
                        offenders.append(f"{mod_name}.{attr_name} = {p!r}")

        assert not offenders, (
            "Test module(s) bind a watched repo-root cache path at IMPORT "
            "time (escapes the autouse per-test redirect fixture): " + "; ".join(offenders)
        )
