"""Integration smoke test for app.py's Setup / Command Center wiring (Wave 3.1b).

Runs the REAL app.py end-to-end via ``streamlit.testing.v1.AppTest.from_file``
to verify ``get_household()`` is correctly wired to
``engine.data_sources.orchestrator.resolve_for_app`` and that the per-load
snapshot-clobber block has been removed.

Isolation: the three new Setup / Command Center cache files
(``.candidate_store.json``, ``.trust_choices.json``, ``.committed_household.json``)
are written at repo-root (mirroring the existing ``__file__``-anchored cache
path convention used throughout ``engine/*`` — see e.g.
``engine/exercise_schedule_store.py``). They are already covered by
``.gitignore``, but this test still deletes them afterward so repeated local
test runs never leave stray state behind. ``_suppress_snapshot_autoload`` is
pre-seeded so a developer's real ``.portfolio_cache.json`` (personal data)
cannot influence the migration-identity assertion below.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from config.defaults import DEFAULTS
from engine.data_sources.committed import load_committed

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
REPO_ROOT = APP_PATH.parent

_NEW_CACHE_FILES = [
    REPO_ROOT / ".candidate_store.json",
    REPO_ROOT / ".trust_choices.json",
    REPO_ROOT / ".committed_household.json",
]


@pytest.fixture
def clean_command_center_caches():
    """Delete the 3 new Setup/Command Center cache files after the test.

    Paths are repo-root-anchored (via ``__file__``, matching the existing
    cache-path convention), so cwd is irrelevant — cleanup targets the exact
    files app.py's get_household() writes.
    """
    yield
    for p in _NEW_CACHE_FILES:
        p.unlink(missing_ok=True)


def test_setup_page_renders_without_exception(clean_command_center_caches) -> None:
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception


def test_dashboard_page_renders_without_exception(clean_command_center_caches) -> None:
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()
    assert not at.exception

    at.sidebar.radio[0].set_value("📊 Dashboard")
    at.run()

    assert not at.exception


def test_get_household_exposes_pending_review_gate(clean_command_center_caches) -> None:
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception
    assert "_pending_review" in at.session_state


def test_fresh_run_creates_committed_file_with_migration_identity(
    clean_command_center_caches,
) -> None:
    """First-ever load (no committed baseline on disk) migrates the session
    default numerically unchanged — the "migration is a numeric no-op"
    invariant resolve_for_app relies on.
    """
    for p in _NEW_CACHE_FILES:
        p.unlink(missing_ok=True)

    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception

    committed_path = REPO_ROOT / ".committed_household.json"
    assert committed_path.exists()

    committed_json = load_committed(committed_path)
    assert committed_json is not None
    assert committed_json["your_ira"]["value"] == DEFAULTS["your_ira"]
