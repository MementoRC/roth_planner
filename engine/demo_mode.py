"""Demo-mode marker (audit-1018 D2): "Reset to demo" parks the personal data.

While the marker file exists the app treats every personal on-disk cache
(committed baseline, candidate store, trust choices, portfolio / YTD / SSA /
1040 caches, PDF ledger, ``.user_defaults.json``) as absent and writes none of
them, so demo values can never overwrite or masquerade as personal data. The
personal files stay on disk untouched; :func:`leave_demo_mode` removes the
marker and they come back.

A marker file (not session state) so the mode survives a restart of a local
run. The stlite build has no localStorage, but its worker has a writable
in-memory filesystem, which is all this needs; there a refresh is a fresh demo
session anyway.

Pure module: stdlib only (``config.loader`` imports it, so it must not import
back into ``config`` or ``engine`` packages that do).
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

# Repo-root anchored like the other cache files; a single seam so tests can
# redirect it (tests/conftest.py).
DEMO_MODE_PATH = Path(__file__).resolve().parents[1] / ".demo_mode"


def is_demo_mode() -> bool:
    """True while the demo-mode marker exists."""
    return DEMO_MODE_PATH.exists()


def enter_demo_mode() -> None:
    """Create the marker (best-effort: a read-only filesystem must not break the page)."""
    try:
        DEMO_MODE_PATH.write_text("demo\n")
    except OSError:
        _log.warning("Could not write the demo-mode marker %s", DEMO_MODE_PATH)


def leave_demo_mode() -> None:
    """Remove the marker so the personal on-disk data is read again."""
    with contextlib.suppress(OSError):
        DEMO_MODE_PATH.unlink(missing_ok=True)
