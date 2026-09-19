"""Live-verification tests for ``views/ytd_income`` — Task 9 of the UI Shell
Phase 3 (YTD Income pilot) plan
(``docs/superpowers/plans/2026-07-28-ui-shell-phase3-ytd-pilot-plan.md``).

Prior tasks (1-8) landed the ``render(hh)`` dispatcher with its
data-completeness caption; the theme axis was removed when the UI shell was
collapsed to Domains-only (the surviving ``views/shells/domains_shell.py``).
Unlike the mocked-``st`` unit tests in ``tests/test_views_ytd_income.py``
(which verify behavior is preserved across the refactor), these tests drive
a REAL rendered Streamlit session via ``streamlit.testing.v1.AppTest`` —
proving the actual widgets, badges, and tab structure behave correctly, not
just that the underlying computation is unchanged.

Mirrors ``tests/test_shells.py``'s established ``AppTest.from_function``
pattern: a single self-contained target function (``_render_ytd``) seeds a
minimal ``Household``/``session_state`` and calls
``views.ytd_income.render(...)``. As that module's docstring notes (verified
empirically there), ``AppTest.from_function`` execs only the target
function's OWN source text in a fresh namespace — no other names from this module
are visible inside it — so all imports/setup live entirely inside
``_render_ytd``.

``render()`` unconditionally calls ``engine.portfolio_sync.save_ytd_snapshot``
at the end, and its ``render_sync_scan_partial`` reads several repo-root
JSON caches (ledger, statement records, statement folder path, Koinly
report) on every render. ``_run_ytd`` neutralizes all of these the same way
``tests/test_shells.py``'s ``_run_shell`` neutralizes Setup's disk sources —
a developer's real caches in this worktree must not leak into these tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from streamlit.testing.v1 import AppTest

from engine.data_status import YTD_STALE_AFTER_DAYS


def _render_ytd(snapshot_date: str | None = None) -> None:
    """AppTest.from_function target: seed a minimal ``Household`` and an
    optional ``YTDSnapshot`` (to control the completeness badge), then render
    the sync/scan partial followed by ``views.ytd_income``.

    ``render_sync_scan_partial`` moved to Setup's "Data" tab
    (``views/shells/domains_shell.py``) and is no longer called by
    ``views.ytd_income.render`` -- it is called here explicitly, in the same
    session/render pass, so this file's scan-button/widget assertions keep
    exercising the identical behavior in its new host.

    Both halves are called, mirroring what the Data tab does. The partial was
    split (2026-09) so the ingest controls can sit in a half-width column
    while the scan RESULTS -- the per-account review table, the "Apply to YTD
    snapshot" button, the Koinly summary -- render full width beneath both
    columns. ``render_sync_scan_partial`` returns the context its results half
    needs; calling only the first half renders the uploader but none of the
    review widgets this file asserts on.
    """
    import streamlit as st

    from models.household import Household
    from models.ytd_income import YTDSnapshot
    from views.ytd_income import render
    from views.ytd_income._partials import render_sync_scan_partial, render_sync_scan_results

    st.session_state["_suppress_snapshot_autoload"] = True
    if snapshot_date is not None:
        st.session_state["ytd_snapshot"] = YTDSnapshot(snapshot_date=snapshot_date)

    hh = Household()
    render_sync_scan_results(render_sync_scan_partial(hh))
    render(hh)


def _run_ytd(monkeypatch, snapshot_date: str | None = None) -> AppTest:
    """Run ``_render_ytd`` under ``AppTest``, neutralizing the repo-root JSON
    caches ``render_sync_scan_partial`` reads/writes on every render (mirrors
    ``tests/test_shells.py``'s ``_run_shell`` disk-source neutralization).
    """
    import engine.brokerage_statement_pdf as brokerage_statement_pdf_mod
    import engine.koinly_report_pdf as koinly_report_pdf_mod
    import views.ytd_income as ytd_income_mod
    from views.ytd_income._partials import _sync_scan as sync_scan_mod

    monkeypatch.setattr(ytd_income_mod, "save_ytd_snapshot", lambda ytd: None)
    monkeypatch.setattr(sync_scan_mod, "load_ledger", lambda: {"koinly": {}, "brokerage": {}})
    monkeypatch.setattr(brokerage_statement_pdf_mod, "load_statement_folder_path", lambda: None)
    monkeypatch.setattr(brokerage_statement_pdf_mod, "load_statement_records", lambda: {})
    monkeypatch.setattr(koinly_report_pdf_mod, "load_koinly_report", lambda: None)

    at = AppTest.from_function(_render_ytd, kwargs={"snapshot_date": snapshot_date})
    at.run()
    return at


def _number_input_by_label(at: AppTest, label: str):
    return next(w for w in at.number_input if w.label == label)


def _badge_captions(at: AppTest) -> list[str]:
    """Completeness-badge captions only (the ⚠️ prefix is unique to the
    badge — see ``views/ytd_income/__init__.py`` and the unmatched-grant
    caption in ``_analysis.py``, which does not fire for our grant-less
    fixtures).
    """
    return [c.value for c in at.caption if c.value.startswith("⚠️")]


# --- Step 2: completeness-badge tests ---------------------------------------


def test_completeness_caption_shown_when_snapshot_missing(monkeypatch) -> None:
    at = _run_ytd(monkeypatch, snapshot_date=None)
    assert not at.exception

    badges = _badge_captions(at)
    assert any("No YTD data recorded yet" in b for b in badges), badges


def test_completeness_caption_shown_when_snapshot_stale(monkeypatch) -> None:
    stale_date = (datetime.now() - timedelta(days=YTD_STALE_AFTER_DAYS + 5)).isoformat()
    at = _run_ytd(monkeypatch, snapshot_date=stale_date)
    assert not at.exception

    badges = _badge_captions(at)
    assert any("days ago" in b for b in badges), badges


def test_completeness_caption_absent_when_snapshot_recent(monkeypatch) -> None:
    recent_date = datetime.now().isoformat()
    at = _run_ytd(monkeypatch, snapshot_date=recent_date)
    assert not at.exception

    assert _badge_captions(at) == []


# --- Step 3: tab-layout test --------------------------------------------------


def test_layout_has_two_tabs_and_multiple_manual_entry_widgets(monkeypatch) -> None:
    at = _run_ytd(monkeypatch)
    assert not at.exception

    tab_container = next(
        child
        for child in at.main.children.values()
        if getattr(child, "type", None) == "tab_container"
    )
    labels = [tab.label for tab in tab_container.children.values()]
    assert labels == ["Update Your Data", "Review Headroom"]

    number_input_labels = {w.label for w in at.number_input}
    assert len(number_input_labels) > 5, (
        "expected multiple manual-entry number inputs, found too few"
    )


# --- Task 6: owner-resolution + scan-gating tests ----------------------------


def _canned_scan_result(brokerage_records=(), koinly_reports=()):
    """A ScanIngestResult whose ``.raw`` carries the given parsed records.

    ``run_folder_scan`` returns ScanIngestResult and ``_sync_scan.py`` reads
    ``.raw`` (a PdfImportResult) off it -- see engine/data_sources/scan_ingest.py.
    """
    from engine.data_sources.scan_ingest import ScanIngestResult
    from engine.pdf_import import PdfImportResult

    raw = PdfImportResult(
        brokerage_records=list(brokerage_records),
        koinly_reports=list(koinly_reports),
    )
    return ScanIngestResult(
        brokerage_count=len(raw.brokerage_records),
        form_1040_count=0,
        koinly_count=len(raw.koinly_reports),
        skipped_count=0,
        unrecognized_count=0,
        magi_candidates_recorded=0,
        errors=[],
        raw=raw,
        pdf_cache={},
    )


def _brokerage_record(account_number="****-*123", account_type="taxable", owner_key=None):
    from engine.brokerage_statement_pdf import BrokerageStatementRecord

    return BrokerageStatementRecord(
        account_number=account_number,
        broker="schwab",
        account_type=account_type,
        statement_period_end="2026-06-30",
        interest_taxable_ytd=10.0,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=20.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-06-30T00:00:00",
        owner_key=owner_key,
    )


def _koinly_report(owner_key=None):
    from engine.koinly_report_pdf import KoinlyReport

    return KoinlyReport(
        tax_year=2026,
        crypto_stcg=100.0,
        crypto_ltcg=200.0,
        crypto_income=50.0,
        captured_at="2026-06-30T00:00:00",
        owner_key=owner_key,
    )


def _patch_scan(monkeypatch, tmp_path, *, brokerage_records=(), koinly_reports=()):
    """Make the "Scan folder" button branch runnable and write-free."""
    import engine.brokerage_statement_pdf as brokerage_statement_pdf_mod
    import engine.koinly_report_pdf as koinly_report_pdf_mod
    from views.ytd_income._partials import _sync_scan as sync_scan_mod

    monkeypatch.setattr(
        brokerage_statement_pdf_mod, "validate_local_folder", lambda raw: (tmp_path, None)
    )
    monkeypatch.setattr(brokerage_statement_pdf_mod, "save_statement_folder_path", lambda p: None)
    monkeypatch.setattr(brokerage_statement_pdf_mod, "save_statement_records", lambda d: None)
    monkeypatch.setattr(brokerage_statement_pdf_mod, "load_account_type_overrides", lambda: {})
    monkeypatch.setattr(koinly_report_pdf_mod, "save_koinly_report", lambda r: None)
    monkeypatch.setattr(sync_scan_mod, "save_ledger", lambda ledger: None)
    monkeypatch.setattr(sync_scan_mod, "save_ytd_snapshot", lambda snap: None)
    monkeypatch.setattr(
        sync_scan_mod,
        "run_folder_scan",
        lambda folder_path: _canned_scan_result(
            brokerage_records=brokerage_records, koinly_reports=koinly_reports
        ),
    )


def _scan(at):
    """Click "Scan folder" and rerun. Requires instance_owner already set --
    the button is disabled while identity is unset (see the gating step below).
    """
    at.button(key="scan_pdf_folder_btn").click().run()
    return at


def test_no_brokerage_owner_selectbox_renders_after_scan(monkeypatch, tmp_path) -> None:
    _patch_scan(monkeypatch, tmp_path, brokerage_records=[_brokerage_record(owner_key="Jane Doe")])
    at = _run_ytd(monkeypatch, snapshot_date=None)
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    # Positive control: the scan branch really ran (otherwise the negative
    # assertions below would pass trivially).
    assert any(s.value.startswith("Imported:") for s in at.success)
    assert not any(w.key == "brokerage_owner_confirm_****-*123" for w in at.selectbox)
    assert not any(w.key == "brokerage_owner_correct_****-*123" for w in at.selectbox)


def test_no_koinly_owner_selectbox_renders_after_scan(monkeypatch, tmp_path) -> None:
    _patch_scan(monkeypatch, tmp_path, koinly_reports=[_koinly_report(owner_key="Jane Doe")])
    at = _run_ytd(monkeypatch, snapshot_date=None)
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    assert any(s.value.startswith("Imported:") for s in at.success)
    assert not any(w.key == "koinly_owner_confirm_2026-06-30T00:00:00" for w in at.selectbox)
    assert not any(w.key == "koinly_owner_correct_2026-06-30T00:00:00" for w in at.selectbox)


def test_account_type_confirm_selectbox_still_renders_for_unknown_tax_status(
    monkeypatch, tmp_path
) -> None:
    """The tax-status confirm selectbox is NOT an owner prompt and must survive.

    ``"unknown"`` IS a valid ``ACCOUNT_TYPES`` member
    (engine/brokerage_statement_pdf.py:80), so the fixture states it directly.
    """
    _patch_scan(
        monkeypatch,
        tmp_path,
        brokerage_records=[_brokerage_record(account_number="****-*999", account_type="unknown")],
    )
    at = _run_ytd(monkeypatch, snapshot_date=None)
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    assert any(w.key == "account_type_confirm_****-*999" for w in at.selectbox)


def test_scan_button_disabled_when_instance_owner_unset(monkeypatch) -> None:
    at = _run_ytd(monkeypatch, snapshot_date=None)

    assert not at.exception
    assert next(b for b in at.button if b.key == "scan_pdf_folder_btn").disabled is True


def test_scan_button_enabled_when_instance_owner_set(monkeypatch) -> None:
    at = _run_ytd(monkeypatch, snapshot_date=None)
    at.session_state["instance_owner"] = "you"
    at.run()

    assert not at.exception
    assert next(b for b in at.button if b.key == "scan_pdf_folder_btn").disabled is False


def test_apply_button_disabled_when_instance_owner_unset(monkeypatch, tmp_path) -> None:
    """The "Apply to YTD snapshot" button re-resolves owners independently of
    the scan gate (resolve_account_owner at ~:300-302), so it must be gated
    too -- not just "Scan folder". Reproduces the reported sequence: a prior
    scan already persisted statement records to disk, a later render finds
    identity unset, and the Apply button must not be clickable.
    """
    import engine.brokerage_statement_pdf as brokerage_statement_pdf_mod

    at = _run_ytd(monkeypatch, snapshot_date=None)

    # statement_by_account is cached into session_state on first render (see
    # "statement_by_account" not in st.session_state) and never reloaded
    # from disk again this session -- clear it so the rerun below picks up
    # the freshly-patched disk record, mirroring "records persisted from an
    # earlier scan, identity now unset in a later render".
    monkeypatch.setattr(
        brokerage_statement_pdf_mod,
        "load_statement_records",
        lambda: {"****-*123": _brokerage_record()},
    )
    del at.session_state["statement_by_account"]
    at.run()

    assert not at.exception
    apply_btn = next(b for b in at.button if b.key == "apply_statements_btn")
    assert apply_btn.disabled is True


def test_apply_button_enabled_when_instance_owner_set(monkeypatch, tmp_path) -> None:
    import engine.brokerage_statement_pdf as brokerage_statement_pdf_mod

    at = _run_ytd(monkeypatch, snapshot_date=None)
    monkeypatch.setattr(
        brokerage_statement_pdf_mod,
        "load_statement_records",
        lambda: {"****-*123": _brokerage_record()},
    )
    at.session_state["instance_owner"] = "you"
    del at.session_state["statement_by_account"]
    at.run()

    assert not at.exception
    apply_btn = next(b for b in at.button if b.key == "apply_statements_btn")
    assert apply_btn.disabled is False


# --- Task 9: holder-name cross-check tests -----------------------------------


def _mismatch_warnings(at) -> list[str]:
    """Only the holder-name cross-check warnings (its wording is unique --
    the scan's other st.warning calls are about unrecognized/failed files)."""
    return [w.value for w in at.warning if "holder name" in w.value]


def test_holder_name_mismatch_warns_but_does_not_block(monkeypatch, tmp_path) -> None:
    from views.ytd_income._partials import _sync_scan as sync_scan_mod

    # The name says "spouse"; this instance attributes the account to "you".
    monkeypatch.setattr(sync_scan_mod, "load_owner_map", lambda: {"jane doe": "spouse"})
    _patch_scan(monkeypatch, tmp_path, brokerage_records=[_brokerage_record(owner_key="Jane Doe")])
    at = _run_ytd(monkeypatch, snapshot_date=None)
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    warnings = _mismatch_warnings(at)
    assert any("****-*123" in text and "spouse" in text for text in warnings)
    # WARN, NEVER BLOCK: the scan still ran to completion and still applied
    # the account to the YTD snapshot.
    assert any(s.value.startswith("Imported:") for s in at.success)
    assert any(s.value.startswith("Applied to YTD snapshot:") for s in at.success)


def test_holder_name_match_is_silent(monkeypatch, tmp_path) -> None:
    from views.ytd_income._partials import _sync_scan as sync_scan_mod

    monkeypatch.setattr(sync_scan_mod, "load_owner_map", lambda: {"jane doe": "you"})
    _patch_scan(monkeypatch, tmp_path, brokerage_records=[_brokerage_record(owner_key="Jane Doe")])
    at = _run_ytd(monkeypatch, snapshot_date=None)
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    assert any(s.value.startswith("Imported:") for s in at.success)
    assert _mismatch_warnings(at) == []


def test_apply_button_warns_on_holder_name_mismatch_but_does_not_block(
    monkeypatch, tmp_path
) -> None:
    """The third call site (~:337 in ``_sync_scan.py``): the "Apply to YTD
    snapshot" button re-resolves owners independently of any scan and must
    carry its own cross-check.

    This test never clicks "Scan folder" -- ``_patch_scan`` is reused only
    for its write-free ``save_ledger``/``save_ytd_snapshot`` mocks, and its
    canned scan result is never consumed because ``run_folder_scan`` is
    never called. ``statement_by_account`` is seeded purely from a
    disk-loaded record (mirrors ``test_apply_button_enabled_when_instance_
    owner_set``'s "records persisted from an earlier scan" setup), so any
    mismatch warning here can only come from the Apply block's own
    cross-check -- not the scan loop's (which this test never executes).
    """
    import engine.brokerage_statement_pdf as brokerage_statement_pdf_mod
    from views.ytd_income._partials import _sync_scan as sync_scan_mod

    _patch_scan(monkeypatch, tmp_path)
    monkeypatch.setattr(sync_scan_mod, "load_owner_map", lambda: {"jane doe": "spouse"})

    at = _run_ytd(monkeypatch, snapshot_date=None)
    # Patched AFTER _run_ytd -- its own neutralizing patch
    # (load_statement_records -> {}) runs during construction and would
    # otherwise clobber this one if set beforehand (see the existing
    # Apply-gating tests, which follow the same order).
    monkeypatch.setattr(
        brokerage_statement_pdf_mod,
        "load_statement_records",
        lambda: {"****-*123": _brokerage_record(owner_key="Jane Doe")},
    )
    at.session_state["instance_owner"] = "you"
    del at.session_state["statement_by_account"]
    at.run()

    apply_btn = next(b for b in at.button if b.key == "apply_statements_btn")
    apply_btn.click().run()

    assert not at.exception
    warnings = _mismatch_warnings(at)
    assert any("****-*123" in text and "spouse" in text for text in warnings)
    # WARN, NEVER BLOCK: the Apply still ran to completion and still wrote
    # the account to the YTD snapshot.
    assert any(s.value.startswith("Applied") and "taxable account" in s.value for s in at.success)


def test_absent_holder_name_is_silent(monkeypatch, tmp_path) -> None:
    """owner_key=None (IBKR/Fidelity/UBS today) -- absence of a name is not
    evidence of anything, so it must never warn."""
    from views.ytd_income._partials import _sync_scan as sync_scan_mod

    monkeypatch.setattr(sync_scan_mod, "load_owner_map", lambda: {"jane doe": "spouse"})
    _patch_scan(monkeypatch, tmp_path, brokerage_records=[_brokerage_record(owner_key=None)])
    at = _run_ytd(monkeypatch, snapshot_date=None)
    at.session_state["instance_owner"] = "you"
    _scan(at)

    assert not at.exception
    assert any(s.value.startswith("Imported:") for s in at.success)
    assert _mismatch_warnings(at) == []
