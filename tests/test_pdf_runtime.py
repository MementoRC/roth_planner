"""Tests for ``views._pdf_runtime`` -- the Pyodide-safe pdfplumber
install-on-demand gate + log-silencing helper behind the uploaded-PDF import
flow (``views/ytd_income/_partials/_sync_scan.py:_render_pdf_uploader``).

pdfplumber is a normal pixi dependency in this (local/CI) environment, so it
is never actually ABSENT the way it is on a fresh Pyodide runtime --
``_pdfplumber_importable`` is monkeypatched directly wherever a test needs to
force the "not yet installed" branch, per that function's own docstring.
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock

import pytest

import views._pdf_runtime as pdf_runtime_mod


class _FakeSt:
    """Minimal stand-in for the ``streamlit`` module -- only
    ``session_state`` (a plain dict, which supports every operation
    ``ensure_pdf_backend`` performs: ``.get``, ``.pop``, ``__setitem__``)."""

    def __init__(self) -> None:
        self.session_state: dict = {}


class _FakeTask:
    """Stand-in for the ``asyncio.Future`` returned by ``ensure_future``."""

    def __init__(self) -> None:
        self._done = False
        self._exc: BaseException | None = None

    def done(self) -> bool:
        return self._done

    def exception(self) -> BaseException | None:
        return self._exc


@pytest.fixture
def fake_st(monkeypatch: pytest.MonkeyPatch) -> _FakeSt:
    fake = _FakeSt()
    monkeypatch.setattr(pdf_runtime_mod, "st", fake)
    return fake


class TestSilencePdfLogging:
    def test_sets_pdfminer_and_pdfplumber_to_warning(self) -> None:
        logging.getLogger("pdfminer").setLevel(logging.DEBUG)
        logging.getLogger("pdfplumber").setLevel(logging.DEBUG)

        pdf_runtime_mod.silence_pdf_logging()

        assert logging.getLogger("pdfminer").level == logging.WARNING
        assert logging.getLogger("pdfplumber").level == logging.WARNING

    def test_idempotent(self) -> None:
        pdf_runtime_mod.silence_pdf_logging()
        pdf_runtime_mod.silence_pdf_logging()
        assert logging.getLogger("pdfminer").level == logging.WARNING
        assert logging.getLogger("pdfplumber").level == logging.WARNING


class TestEnsurePdfBackendReadyOrUnavailable:
    def test_ready_when_pdfplumber_importable(
        self, fake_st: _FakeSt, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdf_runtime_mod, "_pdfplumber_importable", lambda: True)

        assert pdf_runtime_mod.ensure_pdf_backend() == "ready"

    def test_ready_short_circuits_before_checking_pyodide(
        self, fake_st: _FakeSt, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real local-install case: pdfplumber is already importable, so
        this must return "ready" without even asking is_pyodide()."""
        monkeypatch.setattr(pdf_runtime_mod, "_pdfplumber_importable", lambda: True)
        monkeypatch.setattr(
            pdf_runtime_mod,
            "is_pyodide",
            MagicMock(side_effect=AssertionError("is_pyodide() must not be called")),
        )

        assert pdf_runtime_mod.ensure_pdf_backend() == "ready"

    def test_unavailable_when_not_pyodide_and_not_importable(
        self, fake_st: _FakeSt, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdf_runtime_mod, "_pdfplumber_importable", lambda: False)
        monkeypatch.setattr(pdf_runtime_mod, "is_pyodide", lambda: False)

        assert pdf_runtime_mod.ensure_pdf_backend() == "unavailable"


class TestEnsurePdfBackendPyodideInstall:
    def test_installing_then_ready_across_reruns(
        self, fake_st: _FakeSt, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdf_runtime_mod, "is_pyodide", lambda: True)
        importable_answers = iter([False, False, True])
        monkeypatch.setattr(
            pdf_runtime_mod, "_pdfplumber_importable", lambda: next(importable_answers)
        )
        fake_micropip = MagicMock()
        monkeypatch.setitem(sys.modules, "micropip", fake_micropip)
        task = _FakeTask()
        monkeypatch.setattr(pdf_runtime_mod.asyncio, "ensure_future", lambda coro: task)

        # First rerun: not importable -> kicks off the (fake) install task.
        assert pdf_runtime_mod.ensure_pdf_backend() == "installing"
        fake_micropip.install.assert_called_once_with("pdfplumber==0.11.9", deps=False)
        assert pdf_runtime_mod._TASK_KEY in fake_st.session_state

        # Second rerun: task still pending -- no real network/event loop needed.
        assert pdf_runtime_mod.ensure_pdf_backend() == "installing"

        # Third rerun: task completes, pdfplumber now importable.
        task._done = True
        assert pdf_runtime_mod.ensure_pdf_backend() == "ready"
        assert pdf_runtime_mod.pdf_backend_error() is None

    def test_failed_surfaces_the_stored_message(
        self, fake_st: _FakeSt, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdf_runtime_mod, "is_pyodide", lambda: True)
        monkeypatch.setattr(pdf_runtime_mod, "_pdfplumber_importable", lambda: False)
        fake_micropip = MagicMock()
        monkeypatch.setitem(sys.modules, "micropip", fake_micropip)
        task = _FakeTask()
        monkeypatch.setattr(pdf_runtime_mod.asyncio, "ensure_future", lambda coro: task)

        assert pdf_runtime_mod.ensure_pdf_backend() == "installing"

        task._done = True
        task._exc = RuntimeError("network unreachable")
        assert pdf_runtime_mod.ensure_pdf_backend() == "failed"
        assert "network unreachable" in pdf_runtime_mod.pdf_backend_error()
        # The exhausted task must not be reused on a later rerun.
        assert pdf_runtime_mod._TASK_KEY not in fake_st.session_state

    def test_failed_when_import_still_fails_after_successful_install(
        self, fake_st: _FakeSt, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The install task itself can succeed while `import pdfplumber`
        still fails right after (e.g. a broken wheel) -- also "failed"."""
        monkeypatch.setattr(pdf_runtime_mod, "is_pyodide", lambda: True)
        monkeypatch.setattr(pdf_runtime_mod, "_pdfplumber_importable", lambda: False)
        fake_micropip = MagicMock()
        monkeypatch.setitem(sys.modules, "micropip", fake_micropip)
        task = _FakeTask()
        monkeypatch.setattr(pdf_runtime_mod.asyncio, "ensure_future", lambda coro: task)

        assert pdf_runtime_mod.ensure_pdf_backend() == "installing"

        task._done = True
        assert pdf_runtime_mod.ensure_pdf_backend() == "failed"
        assert pdf_runtime_mod.pdf_backend_error()
