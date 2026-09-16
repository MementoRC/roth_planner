"""The UI shell was collapsed to Domains-only: the sidebar "Layout" selectbox
(``ui_theme``, ex ``tests/test_app_theme_switch.py``'s Task 10 payoff test)
was deleted along with the Classic/Hub/Contextual/Wizard shells it used to
switch between. This module asserts the negative: no layout picker renders,
and there is no ``ui_theme`` session_state key left for it to drive.

Uses ``AppTest.from_file`` against the real ``app.py`` (mirrors
``tests/test_setup_shell_characterization.py``'s ``setup_app_test`` fixture)
so the sidebar and page dispatch run exactly as they do in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture
def app_test(clean_command_center_caches, monkeypatch) -> AppTest:
    """A fresh, already-``.run()`` ``AppTest`` of the real app.py.

    Neutralizes the same local-disk sources of non-determinism as
    ``tests/test_setup_shell_characterization.py``'s ``setup_app_test``
    fixture (a developer's local V2 pubkey / PDF-tax cache / SSA snapshot
    must not leak into these tests).
    """
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


def test_no_layout_picker_selectbox_rendered(app_test: AppTest) -> None:
    """The retired "Layout" selectbox must not exist -- there is no theme
    axis left to drive it."""
    assert not any(w.key == "ui_theme" for w in app_test.selectbox)


def test_no_ui_theme_session_state_key(app_test: AppTest) -> None:
    """No code path should seed or write ``session_state["ui_theme"]``
    anymore -- it was a UI-only display preference for the deleted picker."""
    assert "ui_theme" not in app_test.session_state


def test_setup_page_renders_domains_shell_only(app_test: AppTest) -> None:
    """With the picker gone, the default landing page (Setup) always renders
    the Domains shell's tab set -- there is no other layout it could be.

    PR A consolidated the four separate ingest entry points (Command Center,
    the YTD sync/scan partial, Data bridge, 1040 Import) into one leading
    "📥 Data" tab -- see ``views/shells/domains_shell.py``'s module docstring.
    This golden is the full, exact, ordered 6-tab list post-consolidation."""
    tab_container = next(
        child
        for child in app_test.main.children.values()
        if getattr(child, "type", None) == "tab_container"
    )
    labels = [tab.label for tab in tab_container.children.values()]
    assert labels == [
        "📥 Data",
        "Household",
        "Accounts",
        "Options",
        "Assumptions",
        "Portfolio",
    ]
