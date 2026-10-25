"""Pins the section structure and render order of the Data pages.

Setup's old "📥 Data" tab (Command Center, Stock Price, PDF Statements, Import
previous data, 1040 Import) was split in redesign PR 3b:

* ``render_import`` (Import Statements): PDF Statements, then 1040 Import.
* ``render_review`` (Review Changes): Command Center.
* ``render_backup`` (Backup & Transfer): the bundle import/export card.
* Stock Price moved to ``render_assumptions`` (see
  ``tests/test_profile_data_pages.py``).

Command Center used to lead the old tab because it holds the instance-owner
control that unlocks PDF scanning. Now that it is its own page, the owner gate
on Import links to it (``import_owner_gate_link``) instead of pointing "above".

Order still matters inside Import: ``render_sync_scan_partial`` writes
``st.session_state["_pdf_1040_scanned"]`` and ``_render_pdf_1040_import``
reads it and returns early when empty, so the scan must run first in the same
pass or a fresh scan's result appears one rerun late.

Uses ``AppTest.from_function`` with a minimal session-state seed rather than
the real ``app.py``, which needs more monkeypatching than these assertions
require.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

_IMPORT = "views.data_pages:render_import"
_REVIEW = "views.data_pages:render_review"
_BACKUP = "views.data_pages:render_backup"

_IMPORT_SECTION_LABELS = frozenset({"PDF Statements", "1040 Import"})
_OWNER_GATE_TEXT = "owner on **Review Changes**"
_COMMAND_CENTER_HEADER = "🎛️ Command Center"


def _render_page(target: str, owner: str | None = None) -> None:
    # Self-contained: AppTest.from_function ships only this function's own
    # source to the script runner, so module-level names are not in scope there.
    import importlib

    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from models.household import Household

    if owner is not None:
        st.session_state["instance_owner"] = owner
    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("stock_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    mod, attr = target.split(":")
    getattr(importlib.import_module(mod), attr)(Household())


def _neutralize_disk_sources(monkeypatch) -> None:
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _run(monkeypatch, target: str, owner: str | None = None) -> AppTest:
    _neutralize_disk_sources(monkeypatch)
    at = AppTest.from_function(_render_page, kwargs={"target": target, "owner": owner})
    at.run()
    return at


def test_import_sections_render_in_order(monkeypatch) -> None:
    at = _run(monkeypatch, _IMPORT)
    assert not at.exception

    rendered = [s.value for s in at.subheader if s.value in _IMPORT_SECTION_LABELS]
    assert rendered == ["PDF Statements", "1040 Import"]


def test_review_titles_command_center_once_as_a_header(monkeypatch) -> None:
    """Command Center self-titles via st.header; the page must not double it."""
    at = _run(monkeypatch, _REVIEW)
    assert not at.exception

    assert [h.value for h in at.header].count(_COMMAND_CENTER_HEADER) == 1
    assert "Command Center" not in [s.value for s in at.subheader]


def test_pdf_statements_gate_links_to_review_when_identity_is_corrupt(monkeypatch) -> None:
    """Corrupt identity => PDF Statements is replaced by the gate; 1040 Import is not.

    A genuinely MISSING .instance_owner.json no longer reaches this branch:
    render_command_center auto-defaults such an instance to "you" (and persists
    that), so this seeds a corrupt file instead, the only remaining state where
    identity stays unset.

    Bundle import does not depend on identity (spec 2026-09-20 §5: a v5 file
    declares its own slots, a v4 file lands where the slot picker says); it
    lives on Backup & Transfer and is covered below. PDF Statements needs an
    owner to attribute scanned accounts to, so it keeps the gate, with a link
    to the page that sets the owner.
    """
    import engine.instance_identity as instance_identity_mod

    instance_identity_mod.INSTANCE_OWNER_PATH.write_text("{not json")

    at = _run(monkeypatch, _IMPORT)
    assert not at.exception

    gates = [i for i in at.info if _OWNER_GATE_TEXT in i.value]
    assert len(gates) == 1, "expected the owner gate for PDF Statements only"
    assert at.button(key="import_owner_gate_link") is not None
    assert "pdf_upload_0" not in {w.key for w in at.get("file_uploader")}


def test_1040_import_survives_a_missing_owner(monkeypatch) -> None:
    """The 1040 importer is not owner-gated and never was, so it gets its own card."""
    at = _run(monkeypatch, _IMPORT)
    assert not at.exception

    assert "1040 Import" in [s.value for s in at.subheader]


def test_import_renders_the_scan_body_once_an_owner_is_set(monkeypatch) -> None:
    """The counterpart to the gate test: without it, the gate assertion would
    still pass if the real body had simply stopped rendering in every state."""
    at = _run(monkeypatch, _IMPORT, owner="you")
    assert not at.exception

    assert not [i for i in at.info if _OWNER_GATE_TEXT in i.value]
    assert "pdf_upload_0" in {w.key for w in at.get("file_uploader")}


def test_backup_bundle_import_renders_with_and_without_an_owner(monkeypatch) -> None:
    """Bundle import does not depend on identity, so its uploader always renders."""
    for owner in (None, "you"):
        at = _run(monkeypatch, _BACKUP, owner=owner)
        assert not at.exception
        assert "bundle_upload" in {w.key for w in at.get("file_uploader")}, owner


def test_pdf_1040_import_runs_after_the_scan_in_the_same_pass(monkeypatch) -> None:
    """Regression for the one-rerun lag: ``render_sync_scan_partial`` writes
    ``st.session_state["_pdf_1040_scanned"]``; ``_render_pdf_1040_import`` reads
    that key and returns early when empty (``views/setup/parameters.py``). If
    Import called the 1040 panel before the scan in the same script pass, a
    fresh scan's result would not appear in the panel until the NEXT rerun.
    This asserts ``render_import``'s own call order directly, since that is
    what fixes the lag regardless of what a given scan happens to find.
    """
    import views.data_pages as data_pages_mod

    call_order: list[str] = []
    original_scan = data_pages_mod.render_sync_scan_partial
    original_1040_import = data_pages_mod._render_pdf_1040_import

    def _tracking_scan(hh):
        call_order.append("scan")
        return original_scan(hh)

    def _tracking_1040_import():
        call_order.append("1040_import")
        return original_1040_import()

    monkeypatch.setattr(data_pages_mod, "render_sync_scan_partial", _tracking_scan)
    monkeypatch.setattr(data_pages_mod, "_render_pdf_1040_import", _tracking_1040_import)

    at = _run(monkeypatch, _IMPORT, owner="you")
    assert not at.exception

    assert call_order == ["scan", "1040_import"], (
        "render_sync_scan_partial must run before _render_pdf_1040_import so "
        "a fresh scan's _pdf_1040_scanned is visible in the same pass "
        f"-- got {call_order}"
    )
