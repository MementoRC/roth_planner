"""Task 10 of the ui-shell-theme-toggle plan — the theme selector's wiring
into ``app.py``.

This is the payoff test for the entire plan: a field entered through the
Classic shell must survive a mid-session switch to any other shell, because
all shells bind the exact same ``session_state`` keys (Tasks 3-9's key-set
parity work). Uses ``AppTest.from_file`` against the real ``app.py`` (mirrors
``tests/test_setup_shell_characterization.py``'s ``setup_app_test`` fixture)
so the sidebar ``ui_theme`` selectbox, ``nav_page`` radio, and
``get_household()`` resolver all run exactly as they do in production.
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


def _number_input_by_label(at: AppTest, label: str):
    return next(w for w in at.number_input if w.label == label)


def test_ui_theme_selectbox_defaults_to_classic(app_test: AppTest) -> None:
    """``index=0`` on the new selectbox must resolve to "Classic" — the
    backward-compatibility guarantee for any user who never touches it.
    """
    theme_box = next(w for w in app_test.selectbox if w.key == "ui_theme")
    assert theme_box.value == "Classic"


def test_classic_default_setup_page_unchanged(app_test: AppTest) -> None:
    """With the new selectbox present but untouched, the Setup page (the
    default landing page) still renders its 4 classic tabs — proves this
    change is a no-op for anyone who ignores the new control.
    """
    tab_container = next(
        child
        for child in app_test.main.children.values()
        if getattr(child, "type", None) == "tab_container"
    )
    labels = [tab.label for tab in tab_container.children.values()]
    assert labels == ["🎛️ Command Center", "📊 Parameters", "💼 Portfolio", "🔗 Data bridge"]


def test_theme_switch_preserves_field_value(app_test: AppTest) -> None:
    """THE critical end-to-end proof of the whole plan: set a field in
    Classic, switch to Hub mid-session, rerun — the field's value survives
    (not reset, not forked onto a different session_state key), and the
    Hub-rendered widget reflects the same value.
    """
    at = app_test

    # 1. Set a representative field via a real widget interaction in Classic.
    _number_input_by_label(at, "Your Trad IRA").set_value(555_000).run()
    assert not at.exception
    assert at.session_state["your_ira"] == 555_000

    # 2. Switch the theme selector to Hub and rerun.
    at.selectbox(key="ui_theme").select("Hub").run()
    assert not at.exception

    # 3. The underlying session_state key is untouched by the switch itself.
    assert at.session_state["your_ira"] == 555_000

    # 4. The Hub shell's own rendering of the same field reflects the
    #    preserved value — proves it's not just session_state surviving by
    #    coincidence, but that Hub actually reads/displays the same data.
    assert at.title[0].value == "⚙️ Setup — Hub"
    assert _number_input_by_label(at, "Your Trad IRA").value == 555_000


def test_theme_switch_does_not_affect_other_pages(app_test: AppTest) -> None:
    """Smoke check: switching ``ui_theme`` and navigating to a non-Setup page
    (Dashboard) still routes and renders normally — the new selector is
    scoped to the Setup domain only, per the plan.
    """
    at = app_test

    at.selectbox(key="ui_theme").select("Hub").run()
    assert not at.exception

    at.radio(key="nav_page").set_value("📊 Dashboard").run()
    assert not at.exception
    # Dashboard rendered, not the Hub Setup shell.
    assert not any(t.value == "⚙️ Setup — Hub" for t in at.title)
