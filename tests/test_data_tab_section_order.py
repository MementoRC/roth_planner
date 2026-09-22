"""Pins the render order of the Setup Data tab's sections.

``views/shells/domains_shell.py``'s ``with tab_data:`` block has been ordered
four ways. Originally Command Center -> Stock Price -> PDF Statements ->
Import previous data -> 1040 Import; then (PR C, 2026-09) import-first, so
the two ingest sections led; then (2026-09) Command Center first, constrained
to the left two thirds (``st.columns([2, 1])``, right column empty as
margin), with the two import sections SIDE BY SIDE beneath it in
``st.columns(2)`` and Stock Price / 1040 Import full width below them; now
(2026-09) those last two moved INTO the left column, stacked beneath Import
previous data as their own bordered cards.

The import-first arrangement optimised for the returning user, but every
control in both import sections is gated on instance identity
(``disabled=not identity_set``), and the widget satisfying that gate lives in
Command Center. Rendering the gated sections above their unlock meant a
first-time user met a disabled button and was pointed at a section they had
already scrolled past.

Stock Price and 1040 Import moved off the page foot because they sat below
the full-width scan results, which grow substantially once a scan has run.
Import previous data stays FIRST within its column: it is step two of the
first-run sequence (owner -> key -> import), whereas Stock Price is a lone
setting and 1040 Import a secondary route.

Script order IS render order -- the left column's body executes in full
before the right column's -- so the sequence below now reads down the left
column and then into the right. That is also what a narrow viewport sees once
Streamlit stacks the columns, which means Stock Price and 1040 Import appear
ABOVE PDF Statements there. This test therefore pins both the vertical
sequence and, implicitly, which sections occupy the left column -- swapping
the columns would fail it.

No prior test pinned this order (see domains_shell.py's PR A commit message:
"Known gap, not addressed here: no test pins the Data tab's section
ORDER."). This closes that gap.

Uses the same ``AppTest.from_function`` + minimal session-state seed idiom
as ``tests/test_shells.py``'s ``_render_shell``/``_run_shell`` (rather than
driving the real ``app.py``, which needs more monkeypatching than this
ordering assertion requires).
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

# The subheaders authored directly in domains_shell.py's tab_data block.
# Filtering to just this set keeps the assertion independent of subheaders any
# partial might add or remove internally. "Account attribution" used to need
# explicit exclusion here (render_command_center emitted it as a nested
# subheader); it is now a collapsed st.expander label, so it is not a
# subheader at all and cannot reach this list either way.
_DATA_TAB_SECTION_LABELS = frozenset(
    {"PDF Statements", "Import previous data", "Stock Price", "1040 Import"}
)

# "Command Center" is absent from this list on purpose: the shell no longer
# emits a subheader for it, because render_command_center opens with its own
# st.header and the pair rendered the name twice. Its position is pinned
# separately by _COMMAND_CENTER_HEADER below -- it renders FIRST, above these.
EXPECTED_ORDER = [
    "Import previous data",
    "Stock Price",
    "1040 Import",
    "PDF Statements",
]

_COMMAND_CENTER_HEADER = "🎛️ Command Center"


def _render_shell() -> None:
    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from models.household import Household
    from views.shells import render_setup

    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("txn_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    render_setup(Household())


def _run_shell(monkeypatch) -> AppTest:
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    at = AppTest.from_function(_render_shell)
    at.run()
    return at


def test_data_tab_sections_render_in_new_order(clean_command_center_caches, monkeypatch) -> None:
    at = _run_shell(monkeypatch)
    assert not at.exception

    rendered = [s.value for s in at.subheader if s.value in _DATA_TAB_SECTION_LABELS]
    assert rendered == EXPECTED_ORDER


def test_command_center_titles_itself_once_above_the_imports(
    clean_command_center_caches, monkeypatch
) -> None:
    """Command Center self-titles via st.header; the shell must not double it."""
    at = _run_shell(monkeypatch)
    assert not at.exception

    headers = [h.value for h in at.header]
    assert _COMMAND_CENTER_HEADER in headers
    # The shell's own duplicate subheader is gone.
    assert "Command Center" not in [s.value for s in at.subheader]


def test_import_sections_are_replaced_by_placeholder_when_identity_is_corrupt(
    clean_command_center_caches, monkeypatch
) -> None:
    """Corrupt identity => both import bodies are suppressed, not merely inert.

    A genuinely MISSING .instance_owner.json no longer reaches this branch:
    render_command_center auto-defaults such an instance to "you" (and
    persists that) inside its own render, before the shell ever checks
    identity_set for these sections -- so this seeds a corrupt file instead,
    the only remaining state where identity stays unset after render.
    """
    import engine.instance_identity as instance_identity_mod

    instance_identity_mod.INSTANCE_OWNER_PATH.write_text("{not json")

    at = _run_shell(monkeypatch)
    assert not at.exception

    placeholders = [i for i in at.info if "owner in **Command Center**, above" in i.value]
    assert len(placeholders) == 2, "expected one placeholder per import column"
    # The real bodies must NOT have rendered: both uploaders are absent.
    upload_keys = {w.key for w in at.get("file_uploader")}
    assert "bundle_upload" not in upload_keys
    assert "pdf_upload" not in upload_keys


def test_stock_price_and_1040_import_survive_a_missing_owner(
    clean_command_center_caches, monkeypatch
) -> None:
    """Moving them into the left column must not put them behind the owner gate.

    Import previous data shares that column, and its body IS swapped for the
    placeholder without an owner. Stock Price and 1040 Import are not
    owner-gated and never were, so they get their own bordered cards outside
    that branch. Folding them into the same card would have hidden a market
    quote and a PDF importer behind a gate that does not apply to them --
    silently, since the placeholder explains the import section, not them.
    """
    at = _run_shell(monkeypatch)
    assert not at.exception

    rendered = [s.value for s in at.subheader if s.value in _DATA_TAB_SECTION_LABELS]
    assert "Stock Price" in rendered
    assert "1040 Import" in rendered


def _render_shell_with_owner() -> None:
    # Self-contained, NOT a call to _render_shell: AppTest.from_function ships
    # only this function's own source to the script runner, so module-level
    # names are not in scope there (that is also why _render_shell imports
    # inside its body rather than at module level).
    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from models.household import Household
    from views.shells import render_setup

    st.session_state["instance_owner"] = "you"
    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("txn_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    render_setup(Household())


def test_import_sections_render_their_real_bodies_once_an_owner_is_set(
    clean_command_center_caches, monkeypatch
) -> None:
    """The counterpart to the placeholder test.

    Without this, the placeholder assertion above would still pass if the real
    bodies had simply stopped rendering in every state.
    """
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    at = AppTest.from_function(_render_shell_with_owner)
    at.run()
    assert not at.exception

    assert not [i for i in at.info if "owner in **Command Center**, above" in i.value]
    upload_keys = {w.key for w in at.get("file_uploader")}
    assert "bundle_upload" in upload_keys
    assert "pdf_upload" in upload_keys


def test_pdf_1040_import_runs_after_the_scan_in_the_same_pass(
    clean_command_center_caches, monkeypatch
) -> None:
    """Regression for the one-rerun lag: ``render_sync_scan_partial`` writes
    ``st.session_state["_pdf_1040_scanned"]`` (``views/_shared.py:127,145``);
    ``_render_pdf_1040_import`` reads that key and returns early when empty
    (``views/setup/parameters.py:90-97``). If the shell called the 1040
    import panel before the scan ran in the same script pass, a fresh scan's
    result would not appear in the panel until the NEXT rerun. This asserts
    ``domains_shell``'s own call order directly, since that is what fixes the
    lag regardless of what a given scan happens to find.
    """
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod
    import views.shells.domains_shell as domains_shell_mod

    call_order: list[str] = []
    original_scan = domains_shell_mod.render_sync_scan_partial
    original_1040_import = domains_shell_mod._render_pdf_1040_import

    def _tracking_scan(hh):
        call_order.append("scan")
        return original_scan(hh)

    def _tracking_1040_import():
        call_order.append("1040_import")
        return original_1040_import()

    monkeypatch.setattr(domains_shell_mod, "render_sync_scan_partial", _tracking_scan)
    monkeypatch.setattr(domains_shell_mod, "_render_pdf_1040_import", _tracking_1040_import)
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    at = AppTest.from_function(_render_shell_with_owner)
    at.run()
    assert not at.exception

    assert call_order == ["scan", "1040_import"], (
        "render_sync_scan_partial must run before _render_pdf_1040_import so "
        "a fresh scan's _pdf_1040_scanned is visible in the same pass "
        f"-- got {call_order}"
    )
