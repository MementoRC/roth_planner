"""Characterization test — freezes the widget `key=` set (formerly the six-tab
Setup page, now the union of the six Profile/Data pages) as a "must not
change" baseline.

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
each run selects the Setup page explicitly via ``nav_page`` (YTD Income is
the default page since redesign PR 1). Every test runs on its own in-memory store.

Determinism: this walks the FULL widget tree (``Block.__iter__`` recurses
through every nested tab/expander/column), so it only needs a truly fresh,
disk-cache-free render to be reproducible across machines/CI:
- ``ROTH_PLANNER_IGNORE_USER_DEFAULTS`` (set repo-wide in tests/conftest.py)
  isolates ``.user_defaults.json`` (a developer's personal grant_strikes /
  survivor / inherited_iras would otherwise render extra per-item widgets).
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

from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"

# The complete, frozen set of widget `key=` values present across all 6 Setup
# tabs (📥 Data / Household / Accounts / Options / Assumptions / Portfolio —
# the Domains shell's grouping) for a fresh demo household: no pending review
# items, no portfolio snapshot, no inherited IRAs, no survivor scenario, no
# scanned 1040, no generated/pasted data-bridge keypair. Originally recorded
# 2026-07-24 against development @ b425485 (Classic's 4-tab grouping); tab
# grouping updated for the Domains-only shell. PR A (2026-09) then
# consolidated Command Center / Data bridge / 1040 Import (formerly 3
# separate tabs) into the single "📥 Data" tab AND relocated
# render_sync_scan_partial's call site there from views/ytd_income -- that
# partial's widget keys (scan_pdf_folder_btn / statement_folder_path /
# ytd_sync_btn) are NEW to Setup as of this move (they always existed, just
# not on this page before), so the widget-key set below is NOT unchanged
# across that step, unlike the earlier tab-grouping-only refactor.
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
        "instance_owner_change_choice",
        "instance_owner_change_save",
        # YTD Sync & Scan section (views/ytd_income/_partials/_sync_scan.py,
        # relocated onto this page by PR A -- see module docstring)
        "ytd_sync_btn",
        "statement_folder_path",
        "scan_pdf_folder_btn",
        # Uploaded-PDF import (public-pdf-upload work): renders in BOTH
        # environments, unlike the folder-path widgets above it.
        "pdf_upload_0",
        "scan_uploaded_pdfs_btn",
        # UBS ACTIVITY CSV uploader (stage B wiring): sibling of the PDF
        # uploader immediately above, same both-environments rendering.
        "ubs_csv_upload",
        "import_ubs_csv_btn",
        # Data bridge tab
        "gen_keypair",
        "_v2_privkey_input",
        "save_v2_privkey",
        "bundle_upload",
        # "import_whose_data" retired by bridge v5 (plan B4): a legacy file's
        # slot is now picked by "import_v4_slot", which -- like the per-slot
        # "import_slot_you"/"import_slot_spouse" checkboxes -- renders only once
        # a file is uploaded, so none of them belong in this upload-free snapshot.
        "apply_uploads",
        "reset_demo",
        "_export_recipient_pubkey",
        # "Mark X as confirmed" buttons in the shared completeness badge's
        # "Data sources" expander (views/_shared.py). A fresh demo household has
        # every governed field "unconfirmed", so all of them render.
        "confirm_asis_your_ira",
        "confirm_asis_spouse_ira",
        "confirm_asis_your_roth",
        "confirm_asis_spouse_roth",
        "confirm_asis_stock_price_now",
        "confirm_asis_grants",
        "confirm_asis_your_ss_fra",
        "confirm_asis_spouse_ss_fra",
    }
)


@pytest.fixture
def setup_app_test(monkeypatch) -> AppTest:
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
    # An owner is seeded because this snapshot's job is to freeze the FULL
    # Setup widget-key set, and the Data tab's two import sections only render
    # their bodies once this instance has one (views/data_pages.py
    # replaces them with a placeholder otherwise -- a first-time user should
    # not be handed controls that cannot work yet). Without this, 14 keys
    # (bundle_upload, pdf_upload, apply_uploads, gen_keypair, save_v2_privkey,
    # _v2_privkey_input, _export_recipient_pubkey, reset_demo,
    # scan_pdf_folder_btn, scan_uploaded_pdfs_btn, statement_folder_path,
    # ytd_sync_btn, ubs_csv_upload, import_ubs_csv_btn) drop out and this test
    # characterizes the EMPTY first-run page instead of the working one. The
    # placeholder state has its own
    # coverage in tests/test_data_tab_section_order.py, both directions.
    at.session_state["instance_owner"] = "you"
    # The six-tab Setup page no longer routes (redesign PR 3b). The
    # household/accounts round-trip tests assert on widgets that live on the
    # Household & Accounts page; the whole-set key tests below render all six
    # pages themselves.
    at.session_state["nav_page"] = "household"
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


SIX_PAGES = ("household", "assumptions", "holdings", "import", "review", "backup")

# Keys the six pages add on top of the old Setup page's frozen set: link buttons
# introduced by the split. "import_to_manual_entry" is the Import page's link to
# YTD manual entry; "holdings_to_import" is the Holdings empty-state link (no
# snapshot loaded, as here).
_NEW_LINK_KEYS = frozenset({"import_to_manual_entry", "holdings_to_import"})

# Rendered only while the instance owner is unresolvable (Import's gate).
_OWNER_GATE_LINK_KEYS = frozenset({"import_owner_gate_link"})


def _run_page(page_id: str, *, owner: str | None) -> AppTest:
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = page_id
    if owner is not None:
        at.session_state["instance_owner"] = owner
    at.run()
    assert not at.exception, (page_id, [e.value for e in at.exception])
    return at


def _neutralize_disk_sources(monkeypatch) -> None:
    """Same local-disk isolation as the ``setup_app_test`` fixture."""
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _union_of_page_keys(*, owner: str | None) -> set[str]:
    keys: set[str] = set()
    for page_id in SIX_PAGES:
        keys |= _all_widget_keys(_run_page(page_id, owner=owner))
    return keys


# Setup no longer has ONE widget-key set: two groups are mutually exclusive,
# so no single render can contain both and the frozen set above is the UNION.
#
# - The correction control (radio + Update) renders only while this
#   instance's identity is resolvable (views/setup/command_center.py's
#   `if identity_set:`) -- which now includes a genuinely first-run instance,
#   since it auto-defaults to "you" inside that same render. Only a CORRUPT
#   .instance_owner.json still leaves identity unset after render.
# - The PDF Statements section renders its body only ONCE identity is set
#   (views/data_pages.py swaps in a placeholder otherwise), for the
#   same reason. The "Import previous data" (bundle) card is NOT gated: bundle
#   import no longer depends on identity (spec 2026-09-20 §5), and its export
#   control carries its own gate inside the card.
#
# Both groups stay covered by asserting each state separately rather than
# dropping either from the baseline.
_OWNER_GATE_KEYS = frozenset({"instance_owner_change_choice", "instance_owner_change_save"})

_PDF_SECTION_KEYS = frozenset(
    {
        "pdf_upload_0",
        "scan_pdf_folder_btn",
        "scan_uploaded_pdfs_btn",
        "statement_folder_path",
        "ytd_sync_btn",
        "ubs_csv_upload",
        "import_ubs_csv_btn",
    }
)


def test_setup_widget_key_set_unchanged(monkeypatch) -> None:
    """Owner resolvable (pre-seeded here, but a fresh instance now reaches the
    same state via auto-default): across the six pages, the correction control
    and both import bodies are all present. The union equals the old Setup
    page's frozen set plus the split's link buttons."""
    _neutralize_disk_sources(monkeypatch)
    expected = EXPECTED_WIDGET_KEYS | _NEW_LINK_KEYS
    actual = _union_of_page_keys(owner="you")
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"Widget keys disappeared from the six pages: {sorted(missing)}"
    assert not extra, f"New/unexpected widget keys appeared on the six pages: {sorted(extra)}"


def test_setup_widget_key_set_unchanged_when_identity_is_corrupt(monkeypatch, corrupt_row) -> None:
    """Corrupt identity: the correction control and the PDF Statements body
    are absent; the bundle import card still renders in full.

    This is the only remaining state that diverges from the test above -- a
    genuinely MISSING .instance_owner.json no longer reaches it, since
    render_command_center auto-defaults such an instance to "you" (and
    persists that) within its own render, before the shell ever checks
    identity_set for the import sections. Together with the test above, this
    still covers every key in EXPECTED_WIDGET_KEYS.
    """
    from engine.store.namespaces import INSTANCE_OWNER

    _neutralize_disk_sources(monkeypatch)
    corrupt_row(INSTANCE_OWNER)

    expected = (
        (EXPECTED_WIDGET_KEYS | _NEW_LINK_KEYS | _OWNER_GATE_LINK_KEYS)
        - _OWNER_GATE_KEYS
        - _PDF_SECTION_KEYS
    )
    actual = _union_of_page_keys(owner=None)
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"Widget keys disappeared from corrupt-identity pages: {sorted(missing)}"
    assert not extra, f"New/unexpected widget keys on corrupt-identity pages: {sorted(extra)}"


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

    _checkbox_by_label(at, "You have a workplace retirement plan (401k/403b)").set_value(True).run()
    assert at.session_state["your_has_workplace_plan"] is True

    _selectbox_by_label(at, "Your RMD start age").select(73).run()
    assert at.session_state["your_rmd_start_age"] == 73

    _checkbox_by_label(at, "Defer first RMD to April 1 (two RMDs in year 2)").set_value(True).run()
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

    _checkbox_by_label(at, "Defer spouse's first RMD to April 1 (two RMDs in year 2)").set_value(
        True
    ).run()
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
    monkeypatch,
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
    from engine.store import get_store
    from engine.store.namespaces import COMMITTED
    from models.sourced import Provenance, Source, SourcedValue

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    recorded_at = datetime(2026, 7, 24, 12, 0, 0)
    committed_json = {
        "your_ira": SourcedValue(1_600_000.0, Provenance(Source.UNKNOWN, recorded_at)).to_json(),
        "your_ss_fra": SourcedValue(2_000.0, Provenance(Source.UNKNOWN, recorded_at)).to_json(),
    }
    get_store().put(COMMITTED, committed_json)

    store = CandidateStore()
    store.record_candidate(
        "your_ira", 2_000_000.0, Provenance(Source.FINEXTRACT_LIVE, recorded_at, "live sync")
    )
    store.record_candidate(
        "your_ss_fra", 2_500.0, Provenance(Source.FINEXTRACT_LIVE, recorded_at, "SSA statement")
    )
    store.save()
    ChoiceMap().save()

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
    at.session_state["your_ira"] = 1_600_000
    at.session_state["your_ss_fra"] = 2_000
    at.session_state["nav_page"] = "review"
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


# --- Task 6 supplementary safety net: render_portfolio_legacy ------------------
#
# ``render_portfolio_legacy`` (+ ``_render_portfolio_sub_tabs``) extracts the "Sync from
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
    from views.setup._partials import _render_portfolio_sub_tabs, render_portfolio_legacy

    st.session_state["portfolio_snapshot"] = snap
    _render_portfolio_sub_tabs(snap, st)
    render_portfolio_legacy(Household(), st)


def test_portfolio_partial_account_type_overrides_round_trip() -> None:
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


def test_portfolio_partial_widget_keys_present_with_snapshot() -> None:
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
# A 2026-07-24 code-quality review of Task 6's ``render_portfolio_legacy``
# (then named ``render_portfolio_partial``) found that its two private helpers, ``_render_portfolio_sub_tabs`` and
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
    fake_section = MagicMock()
    fake_container.container.return_value = fake_section

    _render_account_type_overrides(None, fake_container)

    # No longer an expander: Holdings nests this inside "Legacy: FinExtract",
    # and Streamlit forbids nested expanders.
    fake_container.container.assert_called_once_with()
    fake_container.expander.assert_not_called()
    fake_section.info.assert_called_once()


def test_portfolio_legacy_threads_its_container_into_the_overrides(monkeypatch) -> None:
    """``render_portfolio_legacy`` must pass its OWN ``container`` argument
    through to ``_render_account_type_overrides`` (not the module-level
    ``st``). The sub-tabs are no longer its job (Holdings renders them).

    Mirrors ``TestSyncSsaForRecordsCandidate._run_sync`` (tests/test_views_setup.py)
    for monkeypatching ``st.session_state`` to a plain dict so the partial can
    be exercised directly without ``AppTest``.

    ``render_portfolio_legacy`` and its collaborator live in the ``_partials``
    package's ``_portfolio`` submodule, so this test patches that submodule
    directly rather than the package's ``__init__.py`` re-export — the
    internal calls to ``_render_account_type_overrides``/``is_pyodide``/``st``
    resolve against the defining module's globals, not the package namespace.
    """
    from unittest.mock import MagicMock

    from models.household import Household
    from views.setup._partials import _portfolio as portfolio_partial_mod

    monkeypatch.setattr(portfolio_partial_mod.st, "session_state", {"portfolio_snapshot": None})
    fake_container = MagicMock()
    fake_container.button.return_value = False

    overrides_mock = MagicMock()
    monkeypatch.setattr(portfolio_partial_mod, "_render_account_type_overrides", overrides_mock)
    monkeypatch.setattr(portfolio_partial_mod, "is_pyodide", lambda: True)

    portfolio_partial_mod.render_portfolio_legacy(Household(), fake_container)

    overrides_mock.assert_called_once_with(None, fake_container)
