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
"""
from __future__ import annotations

import json
import os
from importlib import util
from pathlib import Path
from typing import Mapping

from .defaults import DEFAULTS


def _load_overrides_from_py(path: Path) -> dict:
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

    # 2. .user_defaults.json (preferred local file)
    json_path = Path(".user_defaults.json")
    if json_path.exists():
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
