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

import json
from datetime import datetime
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


# --- Task 3 supplementary safety net: render_household_partial ----------------
#
# ``views/setup/_partials.py:render_household_partial`` extracts filing status +
# your/spouse age/workplace-plan/ACA-eligible/RMD-timing/sole-beneficiary widgets
# out of ``views/setup/parameters.py``. Every one of those (except filing status)
# is UNKEYED (Owner decision 5), so the key-set test above cannot catch a
# typo'd ``session_state.<attr>`` name introduced during the move — a typo would
# silently create a NEW session_state attribute rather than raising. These
# tests drive a distinct sentinel value through each extracted field and
# assert the correct attribute reflects it.


def _number_input_by_label(at: AppTest, label: str):
    return next(w for w in at.number_input if w.label == label)


def _checkbox_by_label(at: AppTest, label: str):
    return next(w for w in at.checkbox if w.label == label)


def _selectbox_by_label(at: AppTest, label: str):
    return next(w for w in at.selectbox if w.label == label)


def test_household_partial_joint_filing_status_round_trip(setup_app_test: AppTest) -> None:
    at = setup_app_test
    assert at.session_state["filing_status"] == "MFJ"
    at.radio(key="_hh_filing_status_choice").set_value("Single").run()
    assert at.session_state["filing_status"] == "Single"


def test_household_partial_your_fields_round_trip(setup_app_test: AppTest) -> None:
    at = setup_app_test

    _number_input_by_label(at, "Your Age").set_value(51).run()
    assert at.session_state["your_age"] == 51

    _checkbox_by_label(at, "You have a workplace retirement plan (401k/403b)").set_value(
        True
    ).run()
    assert at.session_state["your_has_workplace_plan"] is True

    _selectbox_by_label(at, "Your RMD start age").select(73).run()
    assert at.session_state["your_rmd_start_age"] == 73

    _checkbox_by_label(at, "Defer first RMD to April 1 (two RMDs in year 2)").set_value(
        True
    ).run()
    assert at.session_state["your_defer_first_rmd"] is True

    _number_input_by_label(at, "Your FRA (Full Retirement Age)").set_value(66).run()
    assert at.session_state["your_fra_age"] == 66

    _checkbox_by_label(at, "You on ACA Marketplace").set_value(True).run()
    assert at.session_state["your_aca"] is True


def test_household_partial_spouse_fields_round_trip(setup_app_test: AppTest) -> None:
    at = setup_app_test

    _number_input_by_label(at, "Spouse Age").set_value(52).run()
    assert at.session_state["spouse_age"] == 52

    _checkbox_by_label(at, "Spouse has a workplace retirement plan (401k/403b)").set_value(
        True
    ).run()
    assert at.session_state["spouse_has_workplace_plan"] is True

    _selectbox_by_label(at, "Spouse RMD start age").select(73).run()
    assert at.session_state["spouse_rmd_start_age"] == 73

    _checkbox_by_label(
        at, "Defer spouse's first RMD to April 1 (two RMDs in year 2)"
    ).set_value(True).run()
    assert at.session_state["spouse_defer_first_rmd"] is True

    _checkbox_by_label(
        at,
        "Spouse is sole IRA beneficiary and >10 yrs younger (use IRS Joint & "
        "Last Survivor Table for RMDs)",
    ).set_value(True).run()
    assert at.session_state["spouse_is_sole_beneficiary"] is True

    _number_input_by_label(at, "Spouse FRA (Full Retirement Age)").set_value(66).run()
    assert at.session_state["spouse_fra_age"] == 66

    _checkbox_by_label(at, "Spouse on ACA Marketplace").set_value(True).run()
    assert at.session_state["spouse_aca"] is True


# --- Task 4 supplementary safety net: render_accounts_partial -----------------
#
# ``views/setup/_partials.py:render_accounts_partial`` extracts IRA/Roth/SS-FRA
# balance widgets and SS-start-age out of ``views/setup/parameters.py``.
# SS-start-age is UNKEYED (Owner decision 5) and not covered by the key-set
# test above — same sentinel round-trip pattern as Task 3's tests. The
# IRA/Roth/SS-FRA fields' trust_*/manual_*/confirm_* governance-card keys are
# NOT rendered by this partial (Task 4's relocation was reversed — see
# views/setup/command_center.py's docstring) — they're exercised by
# tests/test_command_center_view.py instead, and this partial's negative
# regression test (below) guards that it stays that way.


def test_accounts_partial_ss_start_age_round_trip(setup_app_test: AppTest) -> None:
    at = setup_app_test

    _number_input_by_label(at, "Your SS claim age").set_value(65).run()
    assert at.session_state["your_ss_start_age"] == 65

    _number_input_by_label(at, "Spouse SS claim age").set_value(64).run()
    assert at.session_state["spouse_ss_start_age"] == 64


def test_classic_mode_no_duplicate_widget_id_with_multiple_pending_accounts_fields(
    clean_command_center_caches, monkeypatch
) -> None:
    """Task-4 reversal regression: IRA/Roth/SS-FRA governance cards render in
    ``views/setup/command_center.py``'s generic per-pending-field loop again
    (Command Center tab) — NOT inline inside ``render_accounts_partial``
    (Parameters tab) anymore. Classic mode's ``st.tabs()`` executes EVERY
    tab's body every script run regardless of which tab is visually
    selected, so if ``render_accounts_partial`` still rendered its own
    inline card too, the same ``trust_<field>``/``manual_<field>``/
    ``confirm_<field>`` widget key would be registered TWICE in one run --
    Streamlit's ``DuplicateWidgetID``. Seeds TWO simultaneously-pending
    accounts fields (``your_ira``, ``your_ss_fra``) to actually exercise
    this, not just one.

    Verified this would fail against the naive "restore Command Center's
    loop but leave the partial's inline card in place" version: temporarily
    re-adding the removed ``_maybe_card``/call-sites (calling the same
    ``_render_field_card`` Command Center's loop calls) to
    ``render_accounts_partial`` reproduces a ``DuplicateWidgetID`` exception
    here; the partial NOT rendering its own card (the actual reversal fix)
    makes this test pass.
    """
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod
    from engine.data_sources.candidate_store import CandidateStore
    from engine.data_sources.choices import ChoiceMap
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
    from models.sourced import Provenance, Source, SourcedValue

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    recorded_at = datetime(2026, 7, 24, 12, 0, 0)
    committed_json = {
        "your_ira": SourcedValue(1_700_000.0, Provenance(Source.UNKNOWN, recorded_at)).to_json(),
        "your_ss_fra": SourcedValue(2_000.0, Provenance(Source.UNKNOWN, recorded_at)).to_json(),
    }
    COMMITTED_PATH.write_text(json.dumps(committed_json))

    store = CandidateStore()
    store.record_candidate(
        "your_ira", 2_000_000.0, Provenance(Source.FINEXTRACT_LIVE, recorded_at, "live sync")
    )
    store.record_candidate(
        "your_ss_fra", 2_500.0, Provenance(Source.FINEXTRACT_LIVE, recorded_at, "SSA statement")
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)

    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    # Pre-seed session_state to match the committed baseline exactly (mirrors
    # tests/test_app_data_sources.py's isolation pattern) -- otherwise
    # reconcile_manual_edits compares the fresh config-default session value
    # against the committed baseline, sees a "genuine edit", and silently
    # promotes the DEFAULT to a new MANUAL commit before resolve() ever runs.
    # (This bit us for real here: the config default your_ss_fra is 2_500,
    # which coincidentally equals the candidate below, so the reconciled
    # MANUAL/2_500 baseline no longer differed from the candidate and the
    # field never went pending -- isolating the mechanism this way avoids
    # depending on candidate values never colliding with config defaults.)
    at.session_state["your_ira"] = 1_700_000
    at.session_state["your_ss_fra"] = 2_000
    at.run()

    assert not at.exception
    assert {"your_ira", "your_ss_fra"} <= at.session_state["_pending_review"]
    # Each pending field's governance card renders exactly once (inside
    # Command Center's loop) -- proves render_accounts_partial does NOT also
    # render one, not merely that AppTest silently swallowed a crash. Checking
    # at.exception alone is NOT enough: _render_field_card wraps its own
    # widget calls in a defensive `except Exception` that swallows
    # DuplicateWidgetID into an "rejected" st.warning instead of propagating
    # it, so the duplicate-registration failure must be asserted via the
    # ABSENCE of that rejection warning, not just a clean at.exception.
    rejected = [w.value for w in at.warning if "rejected" in w.value]
    assert not rejected, f"a governance card silently swallowed an error: {rejected}"
    assert at.button(key="confirm_your_ira") is not None
    assert at.button(key="confirm_your_ss_fra") is not None


# --- Task 6 supplementary safety net: render_portfolio_partial -----------------
#
# ``views/setup/_partials.py:render_portfolio_partial`` extracts the "Sync from
# FinExtract" button, the read-only accounts/holdings tables, and the Account
# Type Overrides expander out of ``views/setup/portfolio.py``. None of these
# widgets bind to a plain unkeyed ``session_state.<attr>`` (Owner decision 5) --
# the Account Type Overrides Type/Owner selectboxes ARE explicitly keyed
# (``key=f"_override_type_{acct_id}"``/``key=f"_override_owner_{acct_id}"``,
# verified by reading the moved source), so the typo-prone unkeyed-widget
# failure mode Owner decision 5 guards against does not apply here. This test
# still pins the dynamic-key round-trip as a straight-move regression: it
# proves the move didn't silently break the override write-through into
# ``session_state["account_type_overrides"]``.


def _render_portfolio_with_snapshot(snap) -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_portfolio_partial

    st.session_state["portfolio_snapshot"] = snap
    render_portfolio_partial(Household(), st)


def test_portfolio_partial_account_type_overrides_round_trip(
    clean_command_center_caches,
) -> None:
    from engine.portfolio_sync import AccountSummary, PortfolioSnapshot

    snap = PortfolioSnapshot(
        accounts=[
            AccountSummary(
                account_type="trad_ira",
                owner="you",
                account_name="U1234567",
                total_value=750_000.0,
            )
        ],
        equity_grants=[],
        server_available=True,
    )

    at = AppTest.from_function(_render_portfolio_with_snapshot, kwargs={"snap": snap})
    at.run()
    assert not at.exception

    at.selectbox(key="_override_type_U1234567").select("roth_ira").run()
    at.selectbox(key="_override_owner_U1234567").select("spouse").run()

    overrides = at.session_state["account_type_overrides"]
    assert overrides["U1234567"] == {"type": "roth_ira", "owner": "spouse"}


def test_portfolio_partial_widget_keys_present_with_snapshot(
    clean_command_center_caches,
) -> None:
    """Extends the Task 1 characterization baseline to the Portfolio tab: on a
    clean checkout, ``EXPECTED_WIDGET_KEYS`` has no Portfolio-tab entries
    because the Sync button is unkeyed and the overrides expander short-circuits
    with no accounts loaded — this test instead confirms the dynamic
    ``_override_type_*``/``_override_owner_*`` keys DO appear once a snapshot
    with accounts is present, so a future regression that silently drops their
    ``key=`` would be caught here.
    """
    from engine.portfolio_sync import AccountSummary, PortfolioSnapshot

    snap = PortfolioSnapshot(
        accounts=[
            AccountSummary(
                account_type="brokerage",
                owner="you",
                account_name="ACCT-1",
                total_value=100_000.0,
            )
        ],
        equity_grants=[],
        server_available=True,
    )

    at = AppTest.from_function(_render_portfolio_with_snapshot, kwargs={"snap": snap})
    at.run()
    assert not at.exception

    keys = {node.key for node in at.main if getattr(node, "key", None)}
    assert {"_override_type_ACCT-1", "_override_owner_ACCT-1"} <= keys


# --- Code-quality fix regression: container threading in the Portfolio partial's
# nested-tabs/expander helpers ------------------------------------------------
#
# A 2026-07-24 code-quality review of Task 6's ``render_portfolio_partial``
# found that its two private helpers, ``_render_portfolio_sub_tabs`` and
# ``_render_account_type_overrides``, hard-coded module-level ``st.tabs``/
# ``st.expander`` calls instead of using the ``container`` parameter the
# caller passes in. It was harmless while the only caller passes ``st``
# itself (module-level ``st.tabs`` and ``st``-as-container behave
# identically), but would silently misplace the tabs/expander once Task 8
# nests this partial inside a real non-``st`` container object (an actual
# ``st.tabs(...)`` tab or ``st.expander(...)``). A plain ``render(hh, st)``
# characterization test cannot catch this — it needs a container that is
# NOT ``st`` itself, with ``st.tabs``/``st.expander`` monkeypatched to fail
# loudly if bypassed.


def test_portfolio_sub_tabs_uses_passed_container_not_bare_st(monkeypatch) -> None:
    """``_render_portfolio_sub_tabs`` must call ``container.tabs(...)``, not
    the module-level ``st.tabs(...)`` — and render its Me/Spouse/All content
    onto the returned tab objects, not bare ``st.``.
    """
    from unittest.mock import MagicMock

    import streamlit as st

    from views.setup._partials import _render_portfolio_sub_tabs

    monkeypatch.setattr(
        st, "tabs", MagicMock(side_effect=AssertionError("st.tabs() must not be called directly"))
    )

    fake_container = MagicMock()
    me_tab, spouse_tab, all_tab = MagicMock(), MagicMock(), MagicMock()
    fake_container.tabs.return_value = (me_tab, spouse_tab, all_tab)

    _render_portfolio_sub_tabs(None, fake_container)

    fake_container.tabs.assert_called_once_with(["Me", "Spouse", "All"])
    me_tab.info.assert_called_once()
    spouse_tab.info.assert_called_once()
    all_tab.info.assert_called_once()


def test_account_type_overrides_uses_passed_container_not_bare_st(monkeypatch) -> None:
    """``_render_account_type_overrides`` must call ``container.expander(...)``,
    not the module-level ``st.expander(...)``, and render its empty-state
    message onto the returned expander object, not bare ``st.``.
    """
    from unittest.mock import MagicMock

    import streamlit as st

    from views.setup._partials import _render_account_type_overrides

    monkeypatch.setattr(
        st,
        "expander",
        MagicMock(side_effect=AssertionError("st.expander() must not be called directly")),
    )

    fake_container = MagicMock()
    fake_expander = MagicMock()
    fake_container.expander.return_value = fake_expander

    _render_account_type_overrides(None, fake_container)

    fake_container.expander.assert_called_once_with("🏷️ Account Type Overrides")
    fake_expander.info.assert_called_once()


def test_portfolio_partial_threads_its_container_into_both_helpers(monkeypatch) -> None:
    """``render_portfolio_partial`` must pass its OWN ``container`` argument
    through to both ``_render_portfolio_sub_tabs`` and
    ``_render_account_type_overrides`` (not the module-level ``st``).

    Mirrors ``TestSyncSsaForRecordsCandidate._run_sync`` (tests/test_views_setup.py)
    for monkeypatching ``st.session_state`` to a plain dict so the partial can
    be exercised directly without ``AppTest``.

    Post Task-6b (package split): ``render_portfolio_partial`` and its two
    collaborator helpers now live in the ``_partials`` package's
    ``_portfolio`` submodule, so this test patches that submodule directly
    rather than the package's ``__init__.py`` re-export —
    ``render_portfolio_partial``'s internal calls to
    ``_render_portfolio_sub_tabs``/``_render_account_type_overrides``/
    ``is_pyodide``/``st`` resolve against its own defining module's globals,
    not the package namespace.
    """
    from unittest.mock import MagicMock

    from models.household import Household
    from views.setup._partials import _portfolio as portfolio_partial_mod

    monkeypatch.setattr(portfolio_partial_mod.st, "session_state", {"portfolio_snapshot": None})
    fake_container = MagicMock()
    fake_container.button.return_value = False

    sub_tabs_mock = MagicMock()
    overrides_mock = MagicMock()
    monkeypatch.setattr(portfolio_partial_mod, "_render_portfolio_sub_tabs", sub_tabs_mock)
    monkeypatch.setattr(portfolio_partial_mod, "_render_account_type_overrides", overrides_mock)
    monkeypatch.setattr(portfolio_partial_mod, "is_pyodide", lambda: True)

    portfolio_partial_mod.render_portfolio_partial(Household(), fake_container)

    sub_tabs_mock.assert_called_once_with(None, fake_container)
    overrides_mock.assert_called_once_with(None, fake_container)
