"""Demo-mode marker (audit-1018 D2): "Reset to demo" parks the personal data.

While the marker file exists, ``engine.store.get_store()`` serves a null store
(every saved store reads as absent and nothing is written), and
``.user_defaults.json`` / ``.dividend_rates.json`` are ignored, so demo values
can never overwrite or masquerade as personal data. The personal data stays
untouched; :func:`leave_demo_mode` removes the marker and it comes back.

A marker file (not session state) so the mode survives a restart of a local
run. The stlite build has no localStorage, but its worker has a writable
in-memory filesystem, which is all this needs; there a refresh is a fresh demo
session anyway.

The marker is shared by every session and can flip in the middle of a run
("Leave demo mode", or another tab). A run that loaded demo values and saved
after the marker went away wrote those demo values into the personal file and
database. So each script run binds its mode once (:func:`begin_run`), and the
storage gates ask :func:`demo_for_run`: a run that began in demo mode stays in
demo mode for every read and write it makes, whatever the marker does later.

Pure module: stdlib only (``config.loader`` imports it, so it must not import
back into ``config`` or ``engine`` packages that do).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator
from pathlib import Path

_log = logging.getLogger(__name__)

# Per script run: Streamlit runs each session's script on its own thread.
_run = threading.local()

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


def begin_run(*, demo: bool) -> None:
    """Bind this script run's storage mode; app.py calls it once at the top of every run."""
    _run.demo = demo
    _run.read_real = False


def clear_run_binding() -> None:
    """Forget this thread's run binding (tests)."""
    _run.demo = False
    _run.read_real = False


def demo_for_run() -> bool:
    """True when this run must keep away from the personal files and database.

    The marker exists now, or this run began in demo mode (see the module
    docstring). Every storage gate uses this, not :func:`is_demo_mode`.
    """
    if getattr(_run, "read_real", False):
        return False
    return is_demo_mode() or bool(getattr(_run, "demo", False))


@contextlib.contextmanager
def reading_real_storage() -> Iterator[None]:
    """Let the wrapped block READ the personal storage in a demo-bound run.

    Only for a read whose result is held in memory, e.g. leaving demo mode
    re-reads the stored instance owner (audit-1026 F-21). Never wrap a write.
    """
    previous = getattr(_run, "read_real", False)
    _run.read_real = True
    try:
        yield
    finally:
        _run.read_real = previous
