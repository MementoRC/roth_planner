"""Pyodide-safe PDF backend runtime: install-on-demand pdfplumber + log
silencing, shared by every Streamlit-facing PDF import surface.

Engine code (``engine/pdf_import.py`` and friends) defers its own
``import pdfplumber`` so it never breaks a Pyodide import at module load
time -- but pdfplumber genuinely isn't INSTALLED in the stlite build until
something puts it there. stlite's ``requirements`` list cannot skip
dependencies, and pdfplumber's declared pypdfium2/Pillow deps have no WASM
wheels (only ``pdfplumber/display.py`` imports them, reached solely via
``Page.to_image()``, which this app never calls) -- so pdfplumber has to be
installed at runtime with ``micropip.install(..., deps=False)`` instead of
being listed in ``deploy/build_stlite.py``'s ``REQUIREMENTS``.

This module owns that one-time install (:func:`ensure_pdf_backend`) plus a
log-level fix that alone made a 58KB PDF appear to hang in the stlite build:
the root logger there runs at DEBUG, and pdfminer logs every parsed token to
the browser console (:func:`silence_pdf_logging`).
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import traceback

import streamlit as st

from engine.data_bridge_browser import is_pyodide

_TASK_KEY = "_pdf_backend_install_task"
_ERROR_KEY = "_pdf_backend_error"
_PDFPLUMBER_PIN = "pdfplumber==0.11.9"


def silence_pdf_logging() -> None:
    """Set the ``pdfminer`` and ``pdfplumber`` loggers to WARNING.

    Idempotent -- safe to call on every render. In the stlite build the root
    logger runs at DEBUG and pdfminer logs every token it parses, each
    marshalled individually to the browser console; that alone made a small
    (58KB) PDF appear to hang.
    """
    for name in ("pdfminer", "pdfplumber"):
        logging.getLogger(name).setLevel(logging.WARNING)


def pdf_backend_error() -> str | None:
    """The stored install-failure message, if :func:`ensure_pdf_backend`
    last returned ``"failed"``. ``None`` otherwise."""
    return st.session_state.get(_ERROR_KEY)


def _pdfplumber_importable() -> bool:
    """Whether ``import pdfplumber`` succeeds right now.

    Split out from :func:`ensure_pdf_backend` so tests can force the
    "not yet installed" branch directly -- pdfplumber is a normal pixi
    dependency in the local/CI environment, so it is never actually absent
    there the way it is on a fresh Pyodide runtime.
    """
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        return False
    return True


def ensure_pdf_backend() -> str:
    """Ensure a working ``pdfplumber`` import is available, driving the
    Pyodide install across reruns as needed.

    Returns one of:

    - ``"ready"``: pdfplumber imports -- already installed locally (the
      common case), or freshly installed in Pyodide this pass.
    - ``"installing"``: a Pyodide ``micropip.install`` is in flight (just
      kicked off, or still pending from an earlier rerun). Call again on
      the next rerun to poll it.
    - ``"failed"``: the Pyodide install (or the import right after it)
      raised. See :func:`pdf_backend_error` for the message.
    - ``"unavailable"``: not Pyodide, and pdfplumber is not installed
      locally -- there is nothing this function can do about that.
    """
    if _pdfplumber_importable():
        silence_pdf_logging()
        return "ready"

    if not is_pyodide():
        return "unavailable"

    task = st.session_state.get(_TASK_KEY)
    if task is None:
        import micropip  # type: ignore[import-not-found]

        st.session_state.pop(_ERROR_KEY, None)
        st.session_state[_TASK_KEY] = asyncio.ensure_future(
            micropip.install(_PDFPLUMBER_PIN, deps=False)
        )
        return "installing"

    if not task.done():
        return "installing"

    st.session_state.pop(_TASK_KEY, None)
    exc = task.exception()
    if exc is not None:
        st.session_state[_ERROR_KEY] = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        return "failed"

    importlib.invalidate_caches()
    if not _pdfplumber_importable():
        st.session_state[_ERROR_KEY] = "Installed, but `import pdfplumber` still failed."
        return "failed"

    silence_pdf_logging()
    return "ready"
