"""Characterization test — freezes the current Setup page's widget `key=` set
and 4 tab labels as a "must not change" baseline.

This is the safety net for the UI-shell-theme-toggle plan (Tasks 3-9): those
tasks extract widgets out of ``views/setup/parameters.py``,
``views/setup/command_center.py``, and ``views/setup/portfolio.py`` into new
composable partial functions for the alternate shells (Domains/Hub/
Contextual). Every one of those tasks re-runs this test and asserts the
widget-key set and tab labels are IDENTICAL to what's recorded here — any
diff means a widget's ``key=`` silently changed (or was dropped/duplicated)
during the extraction, which would corrupt Streamlit's session-state
round-trip for that field.

Uses ``streamlit.testing.v1.AppTest.from_file`` against the real ``app.py``
(mirrors ``tests/test_app_data_sources.py``) so ``get_household()`` and the
Command Center review-gate resolver run exactly as they do in production —
the Setup page is the DEFAULT page (first radio option, index 0), so no
navigation is needed. ``clean_command_center_caches`` (shared fixture,
``tests/conftest.py``) isolates the 3 Command Center cache files.

Determinism: this walks the FULL widget tree (``Block.__iter__`` recurses
through every nested tab/expander/column), so it only needs a truly fresh,
disk-cache-free render to be reproducible across machines/CI:
- ``ROTH_PLANNER_IGNORE_USER_DEFAULTS`` (set repo-wide in tests/conftest.py)
  isolates ``.user_defaults.json`` (a developer's personal grant_strikes /
  survivor / inherited_iras would otherwise render extra per-item widgets).
- ``clean_command_center_caches`` deletes the 3 Command Center cache files
  before AND after the test — a developer's personal
  ``.committed_household.json`` from running the real app locally must not
  leak in.
- ``load_pdf_tax_records`` and ``load_ssa_snapshot`` are monkeypatched to
  empty/None — a developer's local ``.tax_pdf_cache.json`` /
  ``.ssa_snapshot_*.json`` would otherwise record extra MAGI/SS-FRA
  candidates, adding review-gate widgets (``trust_*``/``manual_*``/
  ``confirm_*``) that don't exist on a clean checkout.
- ``load_pubkey`` (V2 data-bridge public key) is monkeypatched to ``None`` —
  a developer's local ``~/.finextract/data-bridge.pub`` would otherwise
  enable the "Export my data" download button (``export_bundle``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"

# The complete, frozen set of widget `key=` values present across all 4 Setup
# tabs (Command Center / Parameters / Portfolio / Data bridge) for a fresh
# demo household: no pending review items, no portfolio snapshot, no
# inherited IRAs, no survivor scenario, no scanned 1040, no generated/pasted
# data-bridge keypair. Recorded 2026-07-24 against development @ b425485.
EXPECTED_WIDGET_KEYS = frozenset(
    {
        # Parameters tab
        "_hh_filing_status_choice",
        "_survivor_enabled",
        "_sync_ssa_you_btn",
        "_sync_ssa_spouse_btn",
        "iira_add",
        # Command Center tab
        "sync_everything_btn",
        # Data bridge tab
        "gen_keypair",
        "_v2_privkey_input",
        "save_v2_privkey",
        "pc_role",
        "bundle_upload",
        "apply_uploads",
        "reset_demo",
        "_export_recipient_pubkey",
    }
)

EXPECTED_TAB_LABELS = [
    "🎛️ Command Center",
    "📊 Parameters",
    "💼 Portfolio",
    "🔗 Data bridge",
]


@pytest.fixture
def setup_app_test(clean_command_center_caches, monkeypatch) -> AppTest:
    """A fresh, already-``.run()`` ``AppTest`` positioned on the Setup page.

    Neutralizes every local-disk source of non-determinism (see module
    docstring) so the widget-key/tab-label snapshot is reproducible across
    machines and CI, then hands back the rendered tree for the caller to
    assert against.
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


def _all_widget_keys(at: AppTest) -> set[str]:
    """Every explicit widget ``key=`` present anywhere in the rendered tree.

    ``Block.__iter__`` recurses into every nested container (tabs, sub-tabs,
    expanders, columns), so this walks the ENTIRE Setup page in one pass —
    not a sample. Widgets rendered without an explicit ``key=`` (most of
    ``views/setup/parameters.py``'s Me/Spouse/Joint number_inputs, which
    instead assign the return value onto a matching ``st.session_state.*``
    attribute) surface ``key is None`` and are correctly excluded — this is
    the same distinction Streamlit itself draws between an explicit and an
    auto-generated widget id.
    """
    return {node.key for node in at.main if getattr(node, "key", None)}


def _top_level_tab_labels(at: AppTest) -> list[str]:
    """Labels of the 4 OUTERMOST Setup tabs only (not the nested Me/Spouse/
    Joint or Me/Spouse/All sub-tabs, which are also ``type == "tab"`` blocks
    but live one level deeper, wrapped in their own ``tab_container``).
    """
    tab_container = next(
        child
        for child in at.main.children.values()
        if getattr(child, "type", None) == "tab_container"
    )
    return [tab.label for tab in tab_container.children.values()]


def test_setup_tab_labels_unchanged(setup_app_test: AppTest) -> None:
    assert _top_level_tab_labels(setup_app_test) == EXPECTED_TAB_LABELS


def test_setup_widget_key_set_unchanged(setup_app_test: AppTest) -> None:
    actual = _all_widget_keys(setup_app_test)
    missing = EXPECTED_WIDGET_KEYS - actual
    extra = actual - EXPECTED_WIDGET_KEYS
    assert not missing, f"Widget keys disappeared from Setup: {sorted(missing)}"
    assert not extra, f"New/unexpected widget keys appeared on Setup: {sorted(extra)}"
