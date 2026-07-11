"""Default loader with optional gitignored personal overrides.

Resolution order:
  1. $ROTH_PLANNER_DEFAULTS env var (sniffed by extension: .json → JSON
     loader, .py → Python loader)
  2. ./.user_defaults.json in the current working directory (new, preferred)
  3. ./.user_defaults.py in the current working directory (still works)
  4. fall through to config.defaults.DEFAULTS

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

from .defaults import DEFAULTS

_log = logging.getLogger(__name__)


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
            "Refusing to exec %s: group/world-writable (mode %#o). Restrict it "
            "with: chmod 600 %s",
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
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_user_defaults(data: dict) -> None:
    """Write *data* to ``.user_defaults.json`` — the write-side counterpart to
    :func:`load_defaults`.

    Best-effort: this file can hold financial PII, but a failed save must
    never raise or break page rendering, so I/O errors are caught and logged.
    On success, the file is chmod'd to 0o600 (best-effort) since we are the
    writer this time and can proactively enforce safe permissions.
    """
    path = Path(".user_defaults.json")
    try:
        path.write_text(json.dumps(data, indent=2, default=str))
    except OSError:
        _log.warning("Failed to save user defaults to %s", path)
        return
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def load_defaults() -> dict:
    """Return DEFAULTS overlaid with user overrides if present."""
    env_path_str = os.environ.get("ROTH_PLANNER_DEFAULTS")

    # 1. Env-var path (sniff extension)
    if env_path_str:
        env_path = Path(env_path_str)
        if env_path.exists():
            if env_path.suffix == ".json":
                overrides = dict(_load_overrides_from_json(env_path))
            else:
                overrides = _load_overrides_from_py(env_path)
            if overrides:
                return {**DEFAULTS, **overrides}

    # Test isolation: skip implicit local override files when set (see tests/conftest.py).
    if not os.environ.get("ROTH_PLANNER_IGNORE_USER_DEFAULTS"):
        # 2. .user_defaults.json (preferred local file)
        json_path = Path(".user_defaults.json")
        if json_path.exists():
            _warn_if_insecure_permissions(json_path)
            overrides = dict(_load_overrides_from_json(json_path))
            if overrides:
                return {**DEFAULTS, **overrides}

        # 3. .user_defaults.py (legacy local file)
        py_path = Path(".user_defaults.py")
        if py_path.exists():
            overrides = _load_overrides_from_py(py_path)
            if overrides:
                return {**DEFAULTS, **overrides}

    return DEFAULTS
