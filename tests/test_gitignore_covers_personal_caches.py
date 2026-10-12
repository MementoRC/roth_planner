"""Guard: every cache path the write-guard watches must be git-ignored.

``tests/conftest.py`` maintains ``_WATCHED_CACHE_PATHS``, the authoritative
list of repo-root cache files that hold real household financial data and
that the suite guards against real writes. Historically, adding a new cache
to that list did not imply adding the matching filename to ``.gitignore`` —
three watched paths were found missing from ``.gitignore``: ``.instance_owner.json``
(PR #452), ``.ubs_activity_cache.json`` (PR #518), and ``.account_attribution.json``.
None had been committed; the risk was that a single ``git add -A`` would have
published real financial data to this public repo. ``.account_attribution.json``
was discovered by this test on its first run, establishing the need to bind
the two lists together.

This test binds the two lists together: it asks git itself (via
``git check-ignore``) whether each watched path is ignored, so a future cache
file cannot be added to ``_WATCHED_CACHE_PATHS`` without also being added to
``.gitignore`` — the suite will fail loudly and name the offending file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import _WATCHED_CACHE_PATHS

_REPO_ROOT = Path(__file__).resolve().parent.parent

_GIT_AVAILABLE = shutil.which("git") is not None
_INSIDE_WORK_TREE = False
if _GIT_AVAILABLE:
    try:
        _probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        _INSIDE_WORK_TREE = _probe.returncode == 0 and _probe.stdout.strip() == "true"
    except OSError:
        _INSIDE_WORK_TREE = False

pytestmark = pytest.mark.skipif(
    not (_GIT_AVAILABLE and _INSIDE_WORK_TREE),
    reason="git is not on PATH or this checkout is not a git work tree "
    "(e.g. a tarball/sdist build) — the gitignore-coverage check needs a "
    "real git repository to query.",
)


def _cache_path_id(path: Path) -> str:
    return path.name


@pytest.mark.parametrize("cache_path", _WATCHED_CACHE_PATHS, ids=_cache_path_id)
def test_watched_cache_path_is_gitignored(cache_path: Path) -> None:
    """Every path in ``_WATCHED_CACHE_PATHS`` must be ignored by git.

    Ignored-ness is determined by invoking ``git check-ignore`` directly
    rather than re-parsing ``.gitignore`` in Python, so this test can never
    drift from git's own matching rules (a second, hand-rolled implementation
    of gitignore semantics would itself be a source of false confidence).
    """
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", str(cache_path)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"{cache_path} is registered in tests/conftest.py's "
        "_WATCHED_CACHE_PATHS (the write-guard's list of personal financial "
        "cache files) but is NOT covered by .gitignore. This file can hold "
        "real personal financial data and this is a PUBLIC repo — add "
        f"'{cache_path.name}' to the 'Personal household data (never "
        "commit)' block in .gitignore before merging."
    )
