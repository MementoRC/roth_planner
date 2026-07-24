"""Pytest configuration for roth_planner test suite.

Isolates tests from a developer's local .user_defaults.json/.py by setting
ROTH_PLANNER_IGNORE_USER_DEFAULTS before any app module (e.g. models.household)
is imported, since Household's dataclass field defaults are resolved at
import time via config.loader.load_defaults().
"""

import os

os.environ.setdefault("ROTH_PLANNER_IGNORE_USER_DEFAULTS", "1")

import sys
from pathlib import Path

# Add project root to path so `from engine...` and `from models...` work
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent

_COMMAND_CENTER_CACHE_FILES = [
    _REPO_ROOT / ".candidate_store.json",
    _REPO_ROOT / ".trust_choices.json",
    _REPO_ROOT / ".committed_household.json",
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
    for p in _COMMAND_CENTER_CACHE_FILES:
        p.unlink(missing_ok=True)
    yield
    for p in _COMMAND_CENTER_CACHE_FILES:
        p.unlink(missing_ok=True)
