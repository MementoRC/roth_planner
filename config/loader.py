"""Default loader with optional gitignored personal overrides.

Resolution order:
  1. $ROTH_PLANNER_DEFAULTS env var (sniffed by extension: .json → JSON
     loader, .py → Python loader)
  2. ./.user_defaults.json in the current working directory (new, preferred)
  3. ./.user_defaults.py in the current working directory (still works)
  4. fall through to config.defaults.DEFAULTS

A missing or empty file falls through to the next one. The autosave
(:func:`save_user_defaults`) writes to the JSON file this order selects, so an
edit survives a restart even when ``ROTH_PLANNER_DEFAULTS`` is set.

The .py override file should define `OVERRIDES: dict` with any subset of
the keys in DEFAULTS; partial overrides are merged on top.

The .json override file should be a flat dict with any subset of DEFAULTS
keys, plus an optional `grant_strikes: {year_str: strike_price}` map.
Strikes are passed through as-is; the JOIN with FinExtract grant data
happens in app.py, not here.

Defaults are loaded at module-import time. Restart the app to pick
up changes to the override file.

Note: ``.user_defaults.json`` is no longer purely a manually-edited, read-only
file — the app itself writes to it via :func:`save_user_defaults` whenever
personal data (age, SS, etc.) changes on the Setup page, so overwrites of this
file by the running app are expected.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Mapping
from importlib import util
from pathlib import Path

from engine.demo_mode import is_demo_mode
from models.legacy_keys import migrate_legacy_keys

from .defaults import DEFAULTS

_log = logging.getLogger(__name__)

# cwd-relative, exactly as the 3 inline literals this replaces were (see
# audit-0805 W1) -- a single seam so tests can redirect it via
# monkeypatch.setattr(config.loader, "_USER_DEFAULTS_PATH", tmp_path) instead
# of every call site re-deriving the same literal.
_USER_DEFAULTS_PATH = Path(".user_defaults.json")


def _warn_if_insecure_permissions(path: Path) -> None:
    """Warn (do not modify) if *path* is group/world-accessible.

    ``.user_defaults.json`` may hold financial PII but is user-created — the app
    reads it without owning it, so it cannot safely chmod it. Surface a startup
    warning so the user can tighten the mode themselves.
    """
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & 0o077:
        _log.warning(
            "%s is group/world-accessible (mode %#o); it may contain financial "
            "data. Restrict it with: chmod 600 %s",
            path,
            mode & 0o777,
            path,
        )


def _py_override_is_trusted(path: Path) -> bool:
    """False (with a warning) if *path* is unsafe to exec as a Python override.

    Executing a .py override is arbitrary code execution, so refuse it unless the
    file is owned by the current user and is not group/world-writable — this blocks
    a planted or tampered override from running while preserving the owner's use.
    """
    try:
        info = path.stat()
    except OSError:
        return False
    _getuid = getattr(os, "getuid", None)
    if _getuid is not None and info.st_uid != _getuid():
        _log.warning("Refusing to exec %s: not owned by the current user.", path)
        return False
    if info.st_mode & 0o022:
        _log.warning(
            "Refusing to exec %s: group/world-writable (mode %#o). Restrict it with: chmod 600 %s",
            path,
            info.st_mode & 0o777,
            path,
        )
        return False
    return True


def _load_overrides_from_py(path: Path) -> dict:
    if not _py_override_is_trusted(path):
        return {}
    spec = util.spec_from_file_location("_user_defaults", path)
    if spec is None or spec.loader is None:
        return {}
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    overrides = getattr(mod, "OVERRIDES", {})
    return dict(overrides) if isinstance(overrides, dict) else {}


def _load_overrides_from_json(path: Path) -> Mapping[str, object]:
    """Parse a .user_defaults.json file.

    Keys are any subset of DEFAULTS plus optional ``grant_strikes``
    (a ``{year_str: strike_price}`` map — NOT full StockGrant objects).
    Returns an empty mapping on any parse or I/O error.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):  # ValueError covers JSONDecodeError and non-UTF-8 bytes
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _load_override_file(path: Path) -> dict:
    """Read one override file, dispatching on its extension.

    Pre-rename key names are moved to their current ones here, the single
    read path every loader and the autosave share.
    """
    if path.suffix == ".json":
        return migrate_legacy_keys(_load_overrides_from_json(path))
    return migrate_legacy_keys(_load_overrides_from_py(path))


def _env_override_path() -> Path | None:
    """The ``ROTH_PLANNER_DEFAULTS`` file, if the variable is set and the file exists."""
    env_path_str = os.environ.get("ROTH_PLANNER_DEFAULTS")
    if env_path_str:
        env_path = Path(env_path_str)
        if env_path.exists():
            return env_path
    return None


def _resolve_overrides() -> tuple[Path | None, dict]:
    """Return ``(source_path, overrides)`` for the override file that counts.

    The ONE resolution rule shared by :func:`load_defaults`,
    :func:`load_raw_user_defaults` and :func:`save_user_defaults`, so the two
    readers can never disagree and the autosave always lands in the file the
    next load reads (audit-1008 F-38). Order: the ``ROTH_PLANNER_DEFAULTS``
    file, then ``.user_defaults.json``, then ``.user_defaults.py``; a missing
    or empty file falls through to the next one. ``source_path`` is None when
    no file supplied any override.

    In demo mode (:mod:`engine.demo_mode`, set by "Reset to demo") no file
    counts, so the demo ``DEFAULTS`` apply and the autosave has no target; the
    personal files are left on disk untouched (audit-1018 D2).
    """
    if is_demo_mode():
        return None, {}
    env_path = _env_override_path()
    if env_path is not None:
        overrides = _load_override_file(env_path)
        if overrides:
            return env_path, overrides

    # Test isolation: skip implicit local override files when set (see tests/conftest.py).
    if os.environ.get("ROTH_PLANNER_IGNORE_USER_DEFAULTS"):
        return None, {}

    for local_path in (_USER_DEFAULTS_PATH, Path(".user_defaults.py")):
        if local_path.exists():
            overrides = _load_override_file(local_path)
            if overrides:
                return local_path, overrides
    return None, {}


def _save_target() -> Path:
    """Where :func:`save_user_defaults` writes: the JSON file the loader reads.

    Normally the file that currently supplies the overrides. When none does
    yet, a ``.json`` ``ROTH_PLANNER_DEFAULTS`` path is preferred over the local
    ``.user_defaults.json`` because the env file wins at load time once it is
    non-empty. A Python override file cannot be written, so a ``.py`` source
    falls back to the local JSON file, which the loader reads only while that
    ``.py`` file supplies nothing.
    """
    source, _overrides = _resolve_overrides()
    if source is not None and source.suffix == ".json":
        return source
    if source is not None and source == _env_override_path():
        _log.warning(
            "ROTH_PLANNER_DEFAULTS points at a Python file (%s); edits are saved to "
            "%s but the Python file takes precedence on the next start.",
            source,
            _USER_DEFAULTS_PATH,
        )
    env_path_str = os.environ.get("ROTH_PLANNER_DEFAULTS")
    if source is None and env_path_str and Path(env_path_str).suffix == ".json":
        return Path(env_path_str)
    return _USER_DEFAULTS_PATH


def save_user_defaults(data: dict) -> None:
    """Merge *data* into the override file the loader reads — the write-side
    counterpart to :func:`load_defaults` (see :func:`_save_target`).

    Best-effort: this file can hold financial PII, but a failed save must
    never raise or break page rendering, so I/O errors are caught and logged.
    On success, the file is chmod'd to 0o600 (best-effort) since we are the
    writer this time and can proactively enforce safe permissions.
    """
    if is_demo_mode():
        return  # demo values must never land in the personal file (audit-1018 F-28)
    path = _save_target()
    # Preserve manually-added override keys the app doesn't manage (e.g.
    # grant_strikes): merge the incoming managed data ON TOP of whatever is
    # already on disk rather than overwriting the whole file. Without this the
    # autosave silently wiped hand-edited keys — which dropped the user's 2019
    # option grant after every FinExtract sync (strikes fell back to demo
    # defaults, so the 2019 grant had no strike and was skipped).
    existing: dict = {}
    if path.exists():
        with contextlib.suppress(OSError, ValueError):
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                # Rewrite pre-rename keys so the merged file carries only
                # current names (old names are read, never written).
                existing = migrate_legacy_keys(loaded)
    merged = {**existing, **data}
    try:
        path.write_text(json.dumps(merged, indent=2, default=str))
    except OSError:
        _log.warning("Failed to save user defaults to %s", path)
        return
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def load_raw_user_defaults() -> dict:
    """Return the RAW persisted override keys — no ``DEFAULTS`` overlay.

    Answers one question: *which scalars has this user deliberately set?*
    :func:`save_user_defaults` writes every scalar present in session state
    whenever Setup data changes (``views/setup/_state.py``'s
    ``_user_defaults_from_session``), so a key's presence in this file is the
    app's own record of a deliberate choice, and its absence means the user has
    never touched it.

    Deliberately NOT :func:`load_defaults` — that returns ``{**DEFAULTS,
    **overrides}``, so ``living_expenses``, ``stock_price_now`` and
    ``spouse_is_sole_beneficiary`` would look "set" on a brand-new install
    purely because ``DEFAULTS`` carries them.

    Session state is equally unusable for this: ``app.py``'s
    ``_seed_session_state`` ``setdefault``s every scalar before any view runs,
    so nothing is ever absent there.

    Resolves through :func:`_resolve_overrides`, the same rule
    :func:`load_defaults` uses — the two readers of this file must never
    disagree about whether it counts.
    """
    _source, overrides = _resolve_overrides()
    return overrides


def load_defaults() -> dict:
    """Return DEFAULTS overlaid with user overrides if present."""
    source, overrides = _resolve_overrides()
    if not overrides:
        return DEFAULTS
    if source == _USER_DEFAULTS_PATH:
        _warn_if_insecure_permissions(source)
    return {**DEFAULTS, **overrides}
