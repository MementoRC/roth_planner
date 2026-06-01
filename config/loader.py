"""Default loader with optional gitignored personal overrides.

Resolution order:
  1. $ROTH_PLANNER_DEFAULTS env var (path to a .py file)
  2. ./.user_defaults.py in the current working directory
  3. fall through to config.defaults.DEFAULTS

The override file should define `OVERRIDES: dict` with any subset of
the keys in DEFAULTS; partial overrides are merged on top.

Defaults are loaded at module-import time. Restart the app to pick
up changes to the override file.
"""
from __future__ import annotations

import os
from importlib import util
from pathlib import Path

from .defaults import DEFAULTS


def _load_overrides_from(path: Path) -> dict:
    spec = util.spec_from_file_location("_user_defaults", path)
    if spec is None or spec.loader is None:
        return {}
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    overrides = getattr(mod, "OVERRIDES", {})
    return dict(overrides) if isinstance(overrides, dict) else {}


def load_defaults() -> dict:
    """Return DEFAULTS overlaid with .user_defaults.OVERRIDES if present."""
    candidates: list[str | None] = [
        os.environ.get("ROTH_PLANNER_DEFAULTS"),
        ".user_defaults.py",
    ]
    for path_str in candidates:
        if not path_str:
            continue
        path = Path(path_str)
        if not path.exists():
            continue
        overrides = _load_overrides_from(path)
        if overrides:
            return {**DEFAULTS, **overrides}
    return DEFAULTS
