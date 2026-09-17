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

    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()
    assert not at.exception
    return at


def test_app_load_emits_exactly_one_style_block(app_test: AppTest) -> None:
    style_blocks = [m.value for m in app_test.markdown if "<style>" in m.value]
    assert len(style_blocks) == 1
