"""Tests for ``views/_style.py`` — the app's single CSS injection point.

Two levels, deliberately behavioural rather than asserting on specific CSS
property values (which would be brittle for no benefit — see the module
docstring's version-fragility warning):

1. Unit: ``inject_app_css()`` itself calls ``st.markdown`` exactly once with
   ``unsafe_allow_html=True`` and a ``<style>`` block.
2. Integration: driving the real ``app.py`` end-to-end (mirrors
   ``tests/test_setup_shell_characterization.py``'s ``setup_app_test``
   fixture) shows exactly one style-block markdown element on the rendered
   page — proving ``app.py`` calls ``inject_app_css()`` exactly once on
   load, not zero and not on every rerun accumulating duplicates.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def test_inject_app_css_calls_markdown_once_with_unsafe_html(monkeypatch) -> None:
    import views._style as style_mod

    fake_markdown = MagicMock()
    monkeypatch.setattr(style_mod.st, "markdown", fake_markdown)

    style_mod.inject_app_css()

    fake_markdown.assert_called_once()
    args, kwargs = fake_markdown.call_args
    assert kwargs.get("unsafe_allow_html") is True
    assert "<style>" in args[0]


@pytest.fixture
def app_test(clean_command_center_caches, monkeypatch) -> AppTest:
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    # default_timeout raised from AppTest's 3s default. This fixture leaves
    # identity unset (unlike test_setup_shell_characterization's, which
    # pre-seeds session_state["instance_owner"] before .run()), so it renders
    # a genuinely cold, auto-defaulting first run of the whole app -- the
    # most expensive path any test here takes.
    #
    # MEASURED, and NOT attributable to any one feature: on the tree BEFORE
    # the UBS ACTIVITY CSV uploader existed, this test already failed 3 of 5
    # runs under coverage (setup 2.92-3.52s against the 3.0s limit); with it,
    # 2.86-3.05s over 5 runs. The two distributions overlap almost entirely.
    # This is a PRE-EXISTING marginal test sitting on the 3s cliff, so do not
    # go hunting for a culprit change if it resurfaces -- the cause is the
    # full-app-boot cost under coverage instrumentation.
    #
    # The test asserts "exactly one style block", not a performance budget,
    # so a longer timeout weakens no assertion here.
    at = AppTest.from_file(str(APP_PATH), default_timeout=10)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()
    assert not at.exception
    return at


def test_app_load_emits_exactly_one_style_block(app_test: AppTest) -> None:
    style_blocks = [m.value for m in app_test.markdown if "<style>" in m.value]
    assert len(style_blocks) == 1
