"""Browser-side helpers for the V2 data bridge (stlite / Pyodide).

Provides :func:`is_pyodide` for runtime detection and thin ``localStorage``
wrappers that no-op outside Pyodide. Used by the public-site upload widget
to cache the V2 private key across page reloads.
"""

from __future__ import annotations

import sys

BROWSER_PRIVKEY_LS_KEY = "roth_planner.data_bridge.priv_b64"


def is_pyodide() -> bool:
    """Return True when running inside stlite / Pyodide WebAssembly."""
    return "pyodide" in sys.modules or sys.platform == "emscripten"


def local_storage_get(key: str) -> str | None:
    """Read a value from browser ``localStorage``.

    Returns ``None`` outside Pyodide or on any access failure.
    """
    if not is_pyodide():
        return None
    try:
        import js  # type: ignore[import-not-found]

        value = js.localStorage.getItem(key)
    except (ImportError, AttributeError):
        return None
    if value is None:
        return None
    return str(value)


def local_storage_set(key: str, value: str) -> None:
    """Write a value to browser ``localStorage``. No-op outside Pyodide."""
    if not is_pyodide():
        return
    try:
        import js  # type: ignore[import-not-found]

        js.localStorage.setItem(key, value)
    except (ImportError, AttributeError):
        return


def local_storage_remove(key: str) -> None:
    """Remove a key from browser ``localStorage``. No-op outside Pyodide."""
    if not is_pyodide():
        return
    try:
        import js  # type: ignore[import-not-found]

        js.localStorage.removeItem(key)
    except (ImportError, AttributeError):
        return
