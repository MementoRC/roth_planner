import hashlib
from collections.abc import Sequence
from typing import NamedTuple

import streamlit as st

from engine.account_attribution import load_account_overrides, resolve_account_owner
from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.data_bridge_browser import is_pyodide
from engine.instance_identity import CorruptInstanceOwnerError, load_instance_owner
from engine.pdf_import import PdfImportResult
from engine.pdf_ledger import (
    PdfLedger,
    derive_brokerage_totals,
    derive_koinly_totals,
    load_ledger,
    save_ledger,
    write_brokerage_contribution,
    write_koinly_contribution,
)
from engine.pdf_owner import load_owner_map
from engine.portfolio_sync import save_ytd_snapshot
from engine.portfolio_sync.ytd import apply_brokerage_totals
from engine.ubs_activity_csv import (
    UbsActivityOrder,
    UbsActivityParseError,
    federal_withholding,
    parse_ubs_activity_text,
)
from engine.ubs_activity_store import UbsActivityStoreError, load_ubs_orders, save_ubs_orders
from engine.ubs_exercise_ytd import apply_ubs_exercise_income, correct_ubs_option_basis
from models.household import Household
from models.ytd_income import YTDSnapshot
from views._format import fmt_dollars
from views._pdf_runtime import ensure_pdf_backend, pdf_backend_error
from views._shared import auto_deselect_manual_entry, run_folder_scan, run_uploaded_scan


class ScanRenderContext(NamedTuple):
    """What ``render_sync_scan_results`` needs from ``render_sync_scan_partial``.

    The two used to be one function. They were split so the *inputs* (sync
    button, folder expander, uploader) can sit in a half-width column while
    the *results* (per-account review, Apply-to-YTD, Koinly summary) render
    full width beneath both columns -- the account-number lists in the review
    banner wrap badly at half width, and the results were most of why the
    right column ran twice as tall as the left.

    Passed explicitly rather than re-derived in the results half. Every
    ledger-mutating path in ``_apply_scan_result`` does call ``save_ledger``,
    so a second ``load_ledger()`` would return the same thing *today* -- but
    that equivalence is an invariant nothing enforces, and a future mutation
    path added without a save would silently desynchronise the two halves.
    Threading the value through cannot drift.
    """

    ledger: PdfLedger
    account_overrides: dict[tuple[str, str], str]
    owner_map: dict[str, str]
    instance_owner: str
    identity_set: bool


def _warn_on_holder_name_mismatch(
    owner_key: str | None, resolved: str, owner_map: dict[str, str], account_label: str
) -> bool:
    """WARN, never block. A name absent from owner_map (or no name at all --
    IBKR/Fidelity/UBS return owner_key=None) is silent: silence never means
    agreement, only that there was nothing to check against.

    Returns whether a warning was actually raised. The scan-loop call sites
    ignore this; the Apply call site uses it to withhold its own immediate
    ``st.rerun()`` -- otherwise the warning would be emitted and wiped by
    that same-render rerun before ever reaching the user (see the SSA-sync
    buttons in ``views/setup/_partials/_accounts.py`` for the same idiom).
    """
    from engine.pdf_owner import resolve_owner

    named_owner = resolve_owner(owner_key, owner_map)
    if named_owner is not None and named_owner != resolved:
        st.warning(
            f"Account {account_label}: the statement's holder name maps to "
            f"'{named_owner}' but this instance attributes it to '{resolved}'. "
            "Double check the account attribution table in **Command Center**, above."
        )
        return True
    return False


def _load_ubs_orders_safe() -> list[UbsActivityOrder]:
    """Load persisted UBS activity orders, surfacing a corrupt store as a
    visible ``st.error`` and treating orders as empty for THIS render --
    never a silent ``[]``. A silently-swallowed corrupt store here is
    indistinguishable from "no exercises this year" and would restore the
    phantom STCG double-count with no signal that anything went wrong (see
    ``UbsActivityStoreError``'s docstring in ``engine.ubs_activity_store``).
    """
    try:
        return load_ubs_orders()
    except UbsActivityStoreError as exc:
        st.error(f"UBS exercise data could not be loaded: {exc}")
        return []


def _apply_ubs_correction(
    brokerage_totals: dict[str, float], ledger: PdfLedger, snap: YTDSnapshot
) -> list[str]:
    """Correct *brokerage_totals*' phantom UBS STCG double-count and land
    both the corrected totals and NQO exercise income onto *snap* IN PLACE;
    return warnings (``[]`` when clean).

    Shared by every ``derive_brokerage_totals`` -> ``apply_brokerage_totals``
    call site (the two brokerage-statement scan paths below, plus the CSV
    uploader's post-upload recompute) so they cannot drift apart. Correction
    happens on the TOTALS DICT, BEFORE ``apply_brokerage_totals`` assigns it
    onto *snap* -- doing this afterwards would be silently undone by that
    function's plain ``ytd.stcg_ytd = totals["stcg_ytd"]`` assignment (see
    ``correct_ubs_option_basis``'s docstring for the full rationale).
    """
    ubs_orders = _load_ubs_orders_safe()
    corrected_totals, warnings = correct_ubs_option_basis(brokerage_totals, ledger, ubs_orders)
    apply_brokerage_totals(snap, corrected_totals)
    warnings += apply_ubs_exercise_income(snap, ubs_orders)
    return warnings


def _merge_ubs_orders(
    existing: Sequence[UbsActivityOrder], new: Sequence[UbsActivityOrder]
) -> list[UbsActivityOrder]:
    """Merge *new* UBS orders into *existing*, deduped by
    ``reference_number`` -- never a wholesale replace. Each CSV export holds
    exactly ONE order, and a browser's repeated re-downloads land as
    ``name.csv``, ``name (1).csv``, ``name (2).csv``, which parse to
    DISTINCT orders (see ``engine.ubs_activity_csv``'s module docstring) --
    losing previously-uploaded orders here would silently understate the
    STCG correction. A *new* order sharing an *existing* order's
    ``reference_number`` wins, treated as a corrected re-upload of the same
    order.
    """
    merged = {o.reference_number: o for o in existing}
    merged.update({o.reference_number: o for o in new})
    return list(merged.values())


def _apply_scan_result(
    result: PdfImportResult,
    *,
    ledger: PdfLedger,
    account_overrides: dict[tuple[str, str], str],
    owner_map: dict[str, str],
    instance_owner: str,
    not_found_message: str = "No importable financial PDFs found.",
) -> PdfLedger:
    """Apply one scanned ``PdfImportResult`` to the ledger + YTD snapshot and
    report the outcome; return the (possibly mutated) *ledger*.

    Shared by the folder-scan handler and the uploaded-PDF handler below so
    their ledger-write / apply / report behavior cannot drift apart -- pure
    extraction of what used to be the "Scan folder" button's inline body,
    same session-state writes, same save_ledger/save_ytd_snapshot calls, same
    summary messages (parameterized only by *not_found_message*, since the
    folder and upload paths word that one line differently).

    Prior-year 1040 exports are NOT handled here: cache merge, candidate
    record, and the single canonical ``_pdf_1040_scanned`` write already
    happened inside ``run_folder_scan``/``run_uploaded_scan`` before this is
    called (single scan entry point, single writer -- W2 Part A, audit
    defect #3). The Parameters-tab confirm preview reads that same session
    key.

    Local imports (not module-level) on purpose -- re-executed on every call
    so tests that ``patch("engine.brokerage_statement_pdf.X", ...)`` still
    reach these call sites (same convention as ``_render_scan_review`` below).
    """
    from engine.brokerage_statement_pdf import (
        apply_account_type_overrides,
        load_account_type_overrides,
        partition_by_account_type,
        pick_latest_per_account,
        save_statement_records,
    )

    # Brokerage statements -> newest record per account.
    by_account = pick_latest_per_account(result.brokerage_records)
    overrides = load_account_type_overrides()
    by_account = apply_account_type_overrides(by_account, overrides)
    st.session_state["statement_by_account"] = by_account
    save_statement_records(by_account)

    # One scan == one import: auto-apply everything parsed straight into
    # the YTD snapshot (no separate "Apply" click). apply_brokerage_*
    # and the Koinly assignment SET the statement/Koinly-derived fields,
    # so re-scanning is idempotent and manual-only fields (wages, NEC,
    # IRA conversions, qualified dividends) are never touched. Accounts
    # whose tax status is not stated are the sole exception: they wait
    # for per-account confirmation below and are applied via the explicit
    # "Apply to YTD snapshot" button after you confirm them.
    applied_bits: list[str] = []
    _snap = st.session_state.get("ytd_snapshot", YTDSnapshot())

    stmt_taxable_now, _stmt_excluded_now, stmt_unknown_now = (
        partition_by_account_type(by_account) if by_account else ({}, {}, {})
    )
    if stmt_taxable_now:
        for account_number, rec in stmt_taxable_now.items():
            resolved = resolve_account_owner(
                rec.broker, account_number, account_overrides, instance_owner
            )
            _warn_on_holder_name_mismatch(rec.owner_key, resolved, owner_map, account_number)
            ledger = write_brokerage_contribution(ledger, resolved, rec)

        save_ledger(ledger)

        brokerage_totals = derive_brokerage_totals(ledger)
        # Correct the UBS phantom-STCG double-count and land NQO exercise
        # income BEFORE recording this as "applied" below -- see
        # _apply_ubs_correction's docstring for why the totals dict must be
        # corrected before apply_brokerage_totals assigns it onto the
        # snapshot.
        ubs_warnings = _apply_ubs_correction(brokerage_totals, ledger, _snap)
        for warning in ubs_warnings:
            st.warning(warning)
        applied_bits.append(
            f"{len(stmt_taxable_now)} taxable brokerage account(s) "
            f"({sum(len(v) for v in ledger['brokerage'].values())} total ledgered)"
        )

    if result.koinly_reports:
        from engine.koinly_report_pdf import save_koinly_report

        for report in result.koinly_reports:
            resolved = resolve_account_owner(
                "koinly",
                report.owner_key or "unknown",
                account_overrides,
                instance_owner,
            )
            _warn_on_holder_name_mismatch(
                report.owner_key, resolved, owner_map, f"Koinly {report.tax_year}"
            )
            ledger = write_koinly_contribution(ledger, resolved, report)

        save_ledger(ledger)
        save_koinly_report(result.koinly_reports[-1])

        koinly_totals = derive_koinly_totals(ledger)
        _snap.crypto_stcg_ytd = koinly_totals["stcg"]
        _snap.crypto_ltcg_ytd = koinly_totals["ltcg"]
        _snap.crypto_income_ytd = koinly_totals["income"]
        applied_bits.append(
            f"Koinly crypto ({len(result.koinly_reports)} report(s), "
            f"{len(ledger['koinly'])} owner(s))"
        )

    _manual_entry_kept_explicit = False
    if applied_bits:
        _snap.with_snapshot_date()
        st.session_state.ytd_snapshot = _snap
        _manual_entry_kept_explicit = not auto_deselect_manual_entry(st.session_state)
        save_ytd_snapshot(_snap)

    # Summary: what was parsed, what was applied, what still needs action.
    parsed_bits: list[str] = []
    if by_account:
        parsed_bits.append(f"{len(by_account)} brokerage account(s)")
    if result.koinly_reports:
        parsed_bits.append(f"Koinly ({len(result.koinly_reports)} report(s))")
    if result.form_1040_records:
        parsed_bits.append(
            "Form 1040 " + ", ".join(str(y) for y in sorted(result.form_1040_records))
        )
    if parsed_bits:
        st.success("Imported: " + "; ".join(parsed_bits))
    elif not (result.skipped or result.unrecognized or result.errors):
        st.info(not_found_message)
    if applied_bits:
        st.success("Applied to YTD snapshot: " + "; ".join(applied_bits))
        if _manual_entry_kept_explicit:
            st.caption(
                "Manual entry stayed ON — you turned it on yourself, so this "
                "import did not switch you back to synced-data display."
            )
    if stmt_unknown_now:
        _partial_now = sum(1 for r in stmt_unknown_now.values() if r.missing_fields)
        _why = "a tax-status confirmation"
        if _partial_now == len(stmt_unknown_now):
            _why = "confirmation (incomplete statement)"
        elif _partial_now:
            _why = "confirmation (tax status, or an incomplete statement)"
        st.info(
            f"{len(stmt_unknown_now)} account(s) need {_why} "
            "below before their income can be applied."
        )
    if result.form_1040_records:
        st.info("Form 1040 MAGI saved — set filing status on the **Household** tab.")
    if result.skipped:
        st.info(
            "Skipped (recognized, nothing to import): "
            + "; ".join(f"{name} — {why}" for name, why in result.skipped)
        )
    if result.unrecognized:
        st.warning("Unrecognized (no known format): " + ", ".join(result.unrecognized))
    if result.errors:
        st.warning(
            f"{len(result.errors)} file(s) could not be parsed: "
            + "; ".join(f"{name}: {msg}" for name, msg in result.errors)
        )

    return ledger


def _render_pdf_uploader(
    *,
    instance_owner: str,
    account_overrides: dict[tuple[str, str], str],
    owner_map: dict[str, str],
    ledger: PdfLedger,
    identity_set: bool,
) -> PdfLedger:
    """Upload-PDFs-directly import flow -- renders in BOTH environments
    (useful locally too, not just on the public site), unlike the folder
    scan above, which needs a real local filesystem.

    Gated on ``ensure_pdf_backend()``: under Pyodide, pdfplumber is installed
    at runtime on first use (a one-time, few-second download per browser
    session); locally it is already installed and the gate is a no-op.

    Files are scanned ONE AT A TIME (a multi-file run in a single pass is
    what crashed the Pyodide runtime) via ``run_uploaded_scan`` called once
    per file, each call wrapped in ``except BaseException`` so one bad
    document cannot take the whole page down. Each per-file call already
    performs the real cache-merge/candidate-record/session-write (mirrors
    calling it once with the full batch, since neither classification nor
    parsing carries any cross-document state) -- results are accumulated
    into one ``PdfImportResult`` and applied/reported together via
    ``_apply_scan_result``, exactly like the folder path.

    Each file is content-digested (sha256 of ``getvalue()``, not filename --
    an edited file reusing the same name must still be rescanned) BEFORE
    parsing. The digest of every successfully-parsed file is kept in
    ``st.session_state["_pdf_scanned_digests"]`` for the life of the browser
    session, so re-clicking "Scan uploaded PDFs" after adding one more file
    to the uploader (the user's actual workflow -- the widget keeps every
    previously-added file selected) does not re-read files already scanned.
    The same set also catches an accidental duplicate selected within one
    click: it is checked and updated in place as the loop runs, so the
    second copy is skipped the moment the first is recognized. A failed
    parse (exception or a reported error) does NOT add its digest, so a
    transient failure can be retried on the next click.
    """
    st.markdown("##### Import from uploaded PDFs")
    st.caption(
        "Upload brokerage statements, your Koinly crypto tax report, or a "
        "TurboTax 1040 export directly — the same content-based recognition "
        "as the folder scan. Files are read one at a time, entirely in this "
        "session; nothing is uploaded to a server."
    )

    backend_status = ensure_pdf_backend()
    if backend_status == "installing":
        st.info(
            "Setting up the PDF reader for this browser session "
            "(one-time download, usually a few seconds)…"
        )
        st.button("Check again", key="pdf_backend_recheck_btn")
        return ledger
    if backend_status == "failed":
        st.error(f"PDF reader failed to install: {pdf_backend_error()}")
        return ledger
    if backend_status == "unavailable":
        st.caption("PDF import requires a local install.")
        return ledger

    uploaded = st.file_uploader(
        "PDFs to import",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_upload",
    )
    if not identity_set:
        st.caption(
            "Importing is unavailable until this planner instance has an "
            "owner — set it in **Command Center**, above."
        )
    scan_clicked = st.button(
        "Scan uploaded PDFs", key="scan_uploaded_pdfs_btn", disabled=not identity_set
    )
    if not (scan_clicked and uploaded):
        return ledger

    combined = PdfImportResult()
    n = len(uploaded)
    imported = 0
    skipped_scanned = 0
    failed = 0
    scanned_digests = st.session_state.get("_pdf_scanned_digests")
    if scanned_digests is None:
        scanned_digests = set()
        st.session_state["_pdf_scanned_digests"] = scanned_digests
    with st.status(f"Scanning {n} PDF(s)…", expanded=True) as status:
        progress = st.progress(0.0)
        for i, uploaded_file in enumerate(uploaded, start=1):
            progress.progress((i - 1) / n, text=f"Reading {uploaded_file.name} — file {i} of {n}")
            digest = hashlib.sha256(uploaded_file.getvalue()).hexdigest()
            if digest in scanned_digests:
                skipped_scanned += 1
                st.write(f"{uploaded_file.name}: already scanned — skipped")
                progress.progress(i / n, text=f"Reading {uploaded_file.name} — file {i} of {n}")
                continue
            try:
                single = run_uploaded_scan([(uploaded_file.name, uploaded_file.getvalue())]).raw
            except BaseException as exc:  # noqa: BLE001 -- one bad file must not kill the scan
                combined.errors.append((uploaded_file.name, str(exc)))
                st.warning(f"{uploaded_file.name}: {exc}")
                st.write(f"{uploaded_file.name}: failed to parse")
                failed += 1
                progress.progress(i / n, text=f"Reading {uploaded_file.name} — file {i} of {n}")
                continue
            combined.brokerage_records.extend(single.brokerage_records)
            combined.koinly_reports.extend(single.koinly_reports)
            combined.form_1040_records.update(single.form_1040_records)
            combined.skipped.extend(single.skipped)
            combined.unrecognized.extend(single.unrecognized)
            combined.errors.extend(single.errors)
            if single.errors:
                failed += 1
                for name, msg in single.errors:
                    st.warning(f"{name}: {msg}")
                st.write(f"{uploaded_file.name}: failed to parse")
            else:
                imported += 1
                # Only a SUCCESSFUL parse is remembered -- a failed one
                # (above, or single.errors just below) is retryable on the
                # next click rather than silently stuck "skipped" forever.
                scanned_digests.add(digest)
                st.write(f"{uploaded_file.name}: recognized")
            progress.progress(i / n, text=f"Reading {uploaded_file.name} — file {i} of {n}")

        # Remove the progress bar once the loop finishes -- left drawn at
        # 100% with the last file's label, a completed scan reads as if the
        # app is stuck (screenshot-confirmed). On the normal completion path
        # (not a conditional branch) so it runs whether or not any file
        # failed; only the st.status summary and the per-file st.write
        # outcome lines above remain on screen.
        progress.empty()

        status.update(
            label=(
                f"Scanned {n} file(s) — {imported} imported, "
                f"{skipped_scanned} skipped, {failed} failed"
            ),
            state="error" if failed else "complete",
        )

    ledger = _apply_scan_result(
        combined,
        ledger=ledger,
        account_overrides=account_overrides,
        owner_map=owner_map,
        instance_owner=instance_owner,
        not_found_message="No importable financial PDFs found in the uploaded files.",
    )
    if is_pyodide():
        st.caption(
            "⚠️ This browser session's data is lost on reload — use "
            "**Import previous data ▸ 📦 Export my data** to keep it."
        )
    return ledger


def _render_ubs_csv_uploader(*, ledger: PdfLedger, identity_set: bool) -> None:
    """Upload UBS "ACTIVITY" CSV exports for NQO same-day-sale exercises --
    siblings with the PDF uploader above by design (same visual pattern,
    same in-session-only handling, no server round trip).

    Each file holds exactly ONE option-exercise order (see
    ``engine.ubs_activity_csv``'s module docstring). Parsed orders are
    MERGED into whatever is already persisted (``_merge_ubs_orders``,
    deduped on ``reference_number``), never a wholesale replace -- a
    browser's repeated re-downloads of the same order land as distinct
    filenames (``name.csv``, ``name (1).csv``, ...) that must all still
    count.

    After a successful save, immediately recomputes the same
    derive-correct-apply path a brokerage-statement scan uses
    (``_apply_ubs_correction``) against the CURRENT ledger, so the
    correction/exercise-income takes effect without requiring a separate
    "Scan folder" or "Apply to YTD snapshot" click.
    """
    st.markdown("##### Import UBS option-exercise CSVs")
    st.caption(
        'Upload UBS "ACTIVITY" CSV exports for NQO same-day-sale exercises -- '
        "corrects the double-counted short-term capital gain in your brokerage "
        "statement and records the ordinary exercise income separately. One "
        "order per file; re-uploading the same order is safe, it replaces "
        "itself rather than duplicating."
    )
    uploaded = st.file_uploader(
        "UBS ACTIVITY CSV exports",
        type=["csv"],
        accept_multiple_files=True,
        key="ubs_csv_upload",
    )
    if not identity_set:
        st.caption(
            "Importing is unavailable until this planner instance has an "
            "owner — set it in **Command Center**, above."
        )
    import_clicked = st.button(
        "Import UBS CSV(s)", key="import_ubs_csv_btn", disabled=not identity_set
    )
    if not (import_clicked and uploaded):
        return

    new_orders: list[UbsActivityOrder] = []
    failed = 0
    for f in uploaded:
        try:
            new_orders.append(parse_ubs_activity_text(f.getvalue().decode()))
        except UbsActivityParseError as exc:
            st.error(f"{f.name}: {exc}")
            failed += 1
        except UnicodeDecodeError as exc:
            st.error(f"{f.name}: could not read as text: {exc}")
            failed += 1

    if not new_orders:
        st.warning(f"No valid UBS CSV order parsed (0 of {len(uploaded)} file(s)).")
        return

    existing = _load_ubs_orders_safe()
    merged = _merge_ubs_orders(existing, new_orders)
    save_ubs_orders(merged)

    total_bargain = sum(o.bargain_element for o in merged)
    total_withholding = sum(federal_withholding(o) for o in merged)
    st.success(
        f"Imported {len(new_orders)} of {len(uploaded)} file(s) ({failed} failed). "
        f"Now storing {len(merged)} UBS exercise order(s): "
        f"bargain element {fmt_dollars(total_bargain)}, "
        f"federal withholding {fmt_dollars(total_withholding)}."
    )

    # Recompute immediately so the correction/exercise-income takes effect
    # without a separate scan/apply click -- see this function's docstring.
    snap = st.session_state.get("ytd_snapshot", YTDSnapshot())
    brokerage_totals = derive_brokerage_totals(ledger)
    ubs_warnings = _apply_ubs_correction(brokerage_totals, ledger, snap)
    snap.with_snapshot_date()
    st.session_state.ytd_snapshot = snap
    _manual_entry_kept_explicit = not auto_deselect_manual_entry(st.session_state)
    save_ytd_snapshot(snap)
    if _manual_entry_kept_explicit:
        st.caption(
            "Manual entry stayed ON — you turned it on yourself, so this "
            "import did not switch you back to synced-data display."
        )
    for warning in ubs_warnings:
        st.warning(warning)
    # WARN, NEVER BLOCK: the writes above already completed -- this only
    # withholds the immediate rerun so a fired warning survives to be seen,
    # same idiom as _warn_on_holder_name_mismatch / _apply_ubs_correction's
    # other call sites.
    if not ubs_warnings:
        st.rerun()


def render_sync_scan_partial(hh: Household) -> ScanRenderContext:
    """Render the ways to get statement data IN: FinExtract sync, the
    local-only folder scan, and the uploader.

    Returns the state its results half needs -- the caller must pass the
    returned context to ``render_sync_scan_results`` to render what a scan
    produced. Callers that only exercise the ingest controls can ignore the
    return value.
    """
    # Resolve once per render. "household" is a defensive last-resort only
    # (mirrors the old ad-hoc `or "household"` default this replaces) --
    # Command Center's identity gate (views/setup/command_center.py) is the
    # real prevention for instance_owner being unset by the time a scan runs.
    instance_owner = st.session_state.get("instance_owner")
    if not instance_owner:
        try:
            instance_owner = load_instance_owner()
        except CorruptInstanceOwnerError:
            instance_owner = None
    # identity_set is computed BEFORE the "household" fallback below -- after
    # it, the value is always truthy and the gate would never fire.
    identity_set = bool(instance_owner)
    instance_owner = instance_owner or "household"
    account_overrides = load_account_overrides()
    owner_map = load_owner_map()
    # Loaded unconditionally (like account_overrides/owner_map above) so it is
    # available to _render_scan_review below regardless of is_pyodide() --
    # moved out of the local-only `else:` branch it used to sit in (was
    # loaded once per render there too, just later; load_ledger() is a
    # pure Path.exists()-guarded read, identical either way).
    ledger = load_ledger()

    # --- Section 1: sync + folder/upload import ---
    # No heading here. This partial's only caller is domains_shell.py's Data
    # tab, which already titles the column "PDF Statements" (st.subheader, an
    # h3) immediately above; a "### YTD Income Entry" h3 right under it
    # rendered two same-weight headings back to back with nothing between --
    # the same defect the shell's duplicate "Command Center" subheader had.
    # The column title names the section; the sub-sections below sit a level
    # down.
    if is_pyodide():
        st.caption(
            "Live sync requires a local install. "
            "Use the **Import previous data** section to upload a snapshot."
        )
    else:
        col_sync, col_status = st.columns([1, 3])
        with col_sync:
            sync_ytd = st.button(
                "Sync from FinExtract",
                help="Pull NQO exercise income from ingestion server",
                key="ytd_sync_btn",
            )
        if sync_ytd:
            from engine.portfolio_sync import (
                apply_option_exercises,
                fetch_option_exercises,
                fetch_ytd_snapshot,
            )

            ytd_status = fetch_ytd_snapshot()
            # Overlay the freshly-fetched metadata (manually_entered,
            # snapshot_date) onto the PREVIOUSLY-PERSISTED snapshot instead of
            # a hand-rolled field-by-field preserve allowlist (audit-0805
            # C96) -- that allowlist omitted federal_withholding_ytd and will
            # rot again the next time a field is added. wages_ytd,
            # nec_income_ytd, qualified_dividends_ytd, ira_conversions_ytd,
            # spouse_ira_conversions_ytd, and ira_distributions_ytd are
            # manual-entry-only; interest_ytd, tax_exempt_interest_ytd,
            # ordinary_dividends_ytd, ltcg_ytd, stcg_ytd, and gain_events are
            # brokerage-statement-sourced (see the section below) — none of
            # these are touched by fetch_ytd_snapshot, so overlay() preserves
            # every one of them automatically.
            prev = st.session_state.get("ytd_snapshot") or YTDSnapshot()
            ytd_snap = prev.overlay(
                manually_entered=ytd_status.manually_entered,
                snapshot_date=ytd_status.snapshot_date,
            )
            exercises = fetch_option_exercises()
            if exercises.server_available:
                ytd_snap = apply_option_exercises(ytd_snap, exercises, hh)
            if ytd_status.snapshot_date:
                st.session_state.ytd_snapshot = ytd_snap
                save_ytd_snapshot(ytd_snap)
                with col_status:
                    st.success(
                        f"Synced NQO exercise data ({len(ytd_snap.gain_events)} gain events)"
                    )
                    # Auto-deselect manual entry so the page switches to
                    # synced-data display, UNLESS the user explicitly chose
                    # this setting themselves (see auto_deselect_manual_entry).
                    if not auto_deselect_manual_entry(st.session_state):
                        st.caption(
                            "Manual entry stayed ON — you turned it on yourself, so this "
                            "sync did not switch you back to synced-data display."
                        )
                st.rerun()
            else:
                with col_status:
                    st.warning("FinExtract unavailable — use manual entry below")

        from engine.brokerage_statement_pdf import (
            apply_account_type_overrides,
            load_account_type_overrides,
            load_statement_folder_path,
            load_statement_records,
            save_statement_folder_path,
            validate_local_folder,
        )

        # Collapsed by default. This path needs a real local filesystem -- it
        # sits inside the not-is_pyodide() branch and never renders on the
        # public site at all -- yet its heading, paragraph, folder input and
        # button were most of this column's height, which is why the column ran
        # far past its neighbour and left the page lopsided. The uploaded-PDF
        # section below stays open: that is the path the public site and a
        # first-time user actually take. An expander body still executes every
        # run, so collapsing changes nothing but what is painted.
        with st.expander("Import from PDF folder", expanded=False):
            st.caption(
                "Drop every statement in one folder — brokerage statements, your Koinly "
                "crypto tax report, and TurboTax 1040 exports. One scan identifies each file "
                "by its contents (filenames are ignored) and imports everything it recognizes: "
                "statement interest/dividends/gains, Koinly crypto figures, and prior-year "
                "1040 MAGI."
            )
            default_folder = load_statement_folder_path() or ""
            folder_input = st.text_input(
                "PDF folder",
                value=default_folder,
                key="statement_folder_path",
                help="Local folder holding your brokerage, Koinly, and 1040 PDFs.",
            )
            # ledger is loaded once per render up top (with account_overrides/
            # owner_map) so the per-owner breakdown expander reflects on-disk
            # ledger state even on renders where "Scan folder" was not clicked.
            if not identity_set:
                st.caption(
                    "Scanning is unavailable until this planner instance has an "
                    "owner — set it in **Command Center**, above."
                )
            # disabled=True (not hidden), same convention as Command Center's
            # "⟳ Sync everything" button in Task 5.
            if st.button("Scan folder", key="scan_pdf_folder_btn", disabled=not identity_set):
                # Local single-user desktop tool: path validation (under $HOME,
                # no '..') lives in validate_local_folder.
                folder_path, folder_err = validate_local_folder(folder_input)
                if folder_err:
                    st.error(folder_err)
                else:
                    save_statement_folder_path(str(folder_path))
                    # Single scan entry point + single _pdf_1040_scanned writer
                    # (W2 Part A) -- the actual scan_pdf_folder call, 1040-MAGI
                    # candidate recording, and pdf-tax-cache persist all live in
                    # run_folder_scan / scan_and_record now. The parse/apply/
                    # report step below (_apply_scan_result) is shared with the
                    # uploaded-PDF path so the two cannot silently drift apart.
                    result = run_folder_scan(folder_path).raw
                    ledger = _apply_scan_result(
                        result,
                        ledger=ledger,
                        account_overrides=account_overrides,
                        owner_map=owner_map,
                        instance_owner=instance_owner,
                        not_found_message=("No importable financial PDFs found in that folder."),
                    )

        if "statement_by_account" not in st.session_state:
            _cached_by_account = load_statement_records()
            if _cached_by_account:
                _cached_by_account = apply_account_type_overrides(
                    _cached_by_account, load_account_type_overrides()
                )
            st.session_state["statement_by_account"] = _cached_by_account

    # --- Uploader: renders in BOTH environments (see _render_pdf_uploader's
    # docstring) -- gives Pyodide a real writer of "statement_by_account",
    # not just the local-only folder scan above.
    ledger = _render_pdf_uploader(
        instance_owner=instance_owner,
        account_overrides=account_overrides,
        owner_map=owner_map,
        ledger=ledger,
        identity_set=identity_set,
    )

    # UBS ACTIVITY CSV uploader -- sibling of the PDF uploader above, its own
    # store (engine.ubs_activity_store), never touches "statement_by_account".
    _render_ubs_csv_uploader(ledger=ledger, identity_set=identity_set)

    # Section 2 (statement review + Apply-to-YTD-snapshot, Koinly summary) is
    # NOT rendered here -- it is returned to the caller to render, see
    # ScanRenderContext and render_sync_scan_results below.
    return ScanRenderContext(
        ledger=ledger,
        account_overrides=account_overrides,
        owner_map=owner_map,
        instance_owner=instance_owner,
        identity_set=identity_set,
    )


def render_sync_scan_results(ctx: ScanRenderContext) -> None:
    """Render what a scan PRODUCED: the per-account statement review, the
    Apply-to-YTD-snapshot flow, and the Koinly summary.

    Split out of ``render_sync_scan_partial`` (placement only -- same calls,
    same order, same gating) so the caller can render these full width while
    the ingest controls stay in their column. Nothing here paints unless
    there is something to show: ``_render_scan_review`` is called only for a
    non-empty ``statement_by_account`` exactly as before, and
    ``_render_koinly_summary`` emits nothing without a stored report or a
    non-empty ledger -- so a fresh page gets no empty block.

    Deliberately OUTSIDE any is_pyodide() gate on the review half -- reachable
    whenever scan results exist in session state, regardless of platform. Both
    the local-only folder scan and the uploader can populate
    "statement_by_account", so this renders identically either way. The Koinly
    half keeps its own ``not is_pyodide()`` gate, unchanged.
    """
    ledger = ctx.ledger
    statement_by_account = st.session_state.get("statement_by_account", {})
    if statement_by_account:
        ledger = _render_scan_review(
            statement_by_account,
            ledger,
            ctx.account_overrides,
            ctx.owner_map,
            ctx.instance_owner,
            ctx.identity_set,
        )

    if not is_pyodide():
        _render_koinly_summary(ledger)


def _render_scan_review(
    statement_by_account: dict[str, BrokerageStatementRecord],
    ledger: PdfLedger,
    account_overrides: dict[tuple[str, str], str],
    owner_map: dict[str, str],
    instance_owner: str,
    identity_set: bool,
) -> PdfLedger:
    """Render the per-account statement review table, owner-attribution
    widgets, and the Apply-to-YTD-snapshot flow; return the (possibly
    mutated) *ledger*.

    Pure move out of render_sync_scan_partial's local-only ``else`` branch
    (was the body of its ``if statement_by_account:``) so this can render
    regardless of is_pyodide() -- same widget keys, same write/rerun order,
    unchanged. Caller only invokes this when statement_by_account is
    non-empty, exactly as before the split.

    Imports below are LOCAL (not module-level) on purpose -- re-executed on
    every call so tests that ``patch("engine.brokerage_statement_pdf.X", ...)``
    still reach these call sites, exactly like the local import this body
    used to share with the rest of render_sync_scan_partial's ``else``
    branch before the split (a module-level import would freeze the
    original function object at collection time, unreachable by that
    patch target -- see this file's own TestM1UnconditionalSaveGuard-style
    lesson elsewhere in the suite).
    """
    from engine.brokerage_statement_pdf import (
        apply_account_type_overrides,
        load_account_type_overrides,
        partition_by_account_type,
        save_account_type_override,
    )

    stmt_taxable, stmt_excluded, stmt_unknown = partition_by_account_type(statement_by_account)

    # partition_by_account_type holds PARTIAL records (an incomplete
    # statement, e.g. no parseable period date) out of stmt_taxable, in
    # the same needs-confirmation bucket as accounts of unstated tax
    # status. Split the two apart: for a partial record whose type IS
    # known the tax-status selectbox below asks the wrong question, and
    # answering it would leave the account held anyway. An account that
    # is BOTH type-unknown and partial stays in stmt_unknown for now and
    # surfaces here on the next rerun, once its type is confirmed.
    stmt_partial = {
        acc: rec
        for acc, rec in stmt_unknown.items()
        if rec.missing_fields and rec.account_type != "unknown"
    }
    stmt_unknown = {acc: rec for acc, rec in stmt_unknown.items() if acc not in stmt_partial}

    # Opting a partial record in is an explicit, per-account act -- never
    # a default. Acknowledged ones join the Apply button's payload below.
    for account_number, rec in stmt_partial.items():
        gaps = ", ".join(f.replace("_", " ") for f in rec.missing_fields)
        st.info(
            f"**{rec.broker} {account_number}** parsed, but incomplete — "
            f"could not read: {gaps}. "
            f"Figures that DID parse: dividends ${rec.dividends_taxable_ytd:,.2f}, "
            f"interest ${rec.interest_taxable_ytd:,.2f}, "
            f"STCG ${rec.stcg_net_ytd:,.2f}, LTCG ${rec.ltcg_net_ytd:,.2f}. "
            "Without a statement period this record cannot be ordered against "
            "other statements for the same account, so a newer one will not "
            "supersede it automatically."
        )
        if st.checkbox(
            f"Use {account_number} anyway ({gaps} missing)",
            key=f"partial_ack_{account_number}",
        ):
            stmt_taxable[account_number] = rec

    if stmt_excluded:
        st.info(
            "Excluded (retirement account, never counted toward taxable YTD income): "
            + ", ".join(
                f"{acc} ({rec.broker}, {rec.account_type})" for acc, rec in stmt_excluded.items()
            )
        )

    if stmt_unknown:
        st.warning(
            f"{len(stmt_unknown)} account(s) have no stated tax status in their statement "
            "(this is normal for Schwab) — confirm each before its figures count:"
        )
        for account_number, rec in stmt_unknown.items():
            choice = st.selectbox(
                f"Account {account_number} ({rec.broker})",
                options=["-- confirm --", "taxable", "traditional_ira", "roth_ira"],
                key=f"account_type_confirm_{account_number}",
            )
            if choice != "-- confirm --":
                save_account_type_override(account_number, choice)
                # Refresh the cached statement_by_account in-place so the
                # confirmed classification sticks across the rerun below --
                # otherwise the stale session_state dict is reused on the
                # next run and the account is re-classified as unknown
                # until a fresh "Scan folder" click.
                st.session_state["statement_by_account"] = apply_account_type_overrides(
                    statement_by_account, load_account_type_overrides()
                )
                st.rerun()

    if stmt_taxable:
        st.caption(f"Counted toward YTD income: {', '.join(stmt_taxable.keys())}")
        if not identity_set:
            st.caption(
                "Applying is unavailable until this planner instance has an "
                "owner — set it in **Command Center**, above."
            )
        if is_pyodide():
            st.caption(
                "⚠️ This browser session's data is lost on reload — use "
                "**Import previous data ▸ 📦 Export my data** to keep it."
            )
        # disabled=True (not hidden), same convention as "Scan folder" above --
        # this button independently re-resolves owners from disk-loaded
        # records (resolve_account_owner below), so gating the scan alone
        # would leave this a live write path to "household" attribution.
        if st.button(
            "Apply to YTD snapshot",
            key="apply_statements_btn",
            disabled=not identity_set,
        ):
            _apply_had_mismatch = False
            for account_number, rec in stmt_taxable.items():
                resolved = resolve_account_owner(
                    rec.broker, account_number, account_overrides, instance_owner
                )
                if _warn_on_holder_name_mismatch(
                    rec.owner_key, resolved, owner_map, account_number
                ):
                    _apply_had_mismatch = True
                ledger = write_brokerage_contribution(ledger, resolved, rec)
            save_ledger(ledger)

            brokerage_totals = derive_brokerage_totals(ledger)
            prev_ytd = st.session_state.get("ytd_snapshot", YTDSnapshot())
            # Correct the UBS phantom-STCG double-count and land NQO exercise
            # income BEFORE recording this as applied below -- see
            # _apply_ubs_correction's docstring for why the totals dict must
            # be corrected before apply_brokerage_totals assigns it onto the
            # snapshot.
            ubs_warnings = _apply_ubs_correction(brokerage_totals, ledger, prev_ytd)
            prev_ytd.with_snapshot_date()
            st.session_state.ytd_snapshot = prev_ytd
            _manual_entry_kept_explicit = not auto_deselect_manual_entry(st.session_state)
            save_ytd_snapshot(prev_ytd)
            st.success(f"Applied {len(stmt_taxable)} taxable account(s) to YTD snapshot")
            if _manual_entry_kept_explicit:
                st.caption(
                    "Manual entry stayed ON — you turned it on yourself, so this "
                    "apply did not switch you back to synced-data display."
                )
            for warning in ubs_warnings:
                st.warning(warning)
            if ubs_warnings:
                _apply_had_mismatch = True
            # WARN, NEVER BLOCK: the write above already completed --
            # this only withholds the immediate rerun so a fired
            # mismatch warning survives to be seen, instead of being
            # wiped by a same-render st.rerun() (see
            # _warn_on_holder_name_mismatch's docstring). A fired UBS
            # correction warning withholds the rerun the same way.
            if not _apply_had_mismatch:
                st.rerun()

    return ledger


def _render_koinly_summary(ledger: PdfLedger) -> None:
    """Render the read-only last-scanned-Koinly-report display and the
    per-owner crypto/brokerage breakdown expanders.

    Pure move out of render_sync_scan_partial's local-only ``else`` branch.
    The caller wraps this call in ``if not is_pyodide():`` -- the same
    condition this block carried inline before the split (there it was
    nested a second time under an already-``not is_pyodide()`` else, so
    the inner check was always true and is dropped here as redundant, not
    as a behavior change).
    """
    from engine.koinly_report_pdf import load_koinly_report

    if "koinly_report" not in st.session_state:
        _cached_koinly = load_koinly_report()
        if _cached_koinly is not None:
            st.session_state["koinly_report"] = _cached_koinly

    koinly_report = st.session_state.get("koinly_report")
    if koinly_report is not None:
        # Read-only display of the most recently scanned Koinly report.
        # No "Apply" button: the ledger derive-sum (below) is now the sole
        # source of crypto_*_ytd, applied automatically during scan --  a
        # separate manual apply here would risk double-counting against
        # newer scans already folded into the ledger.
        st.write(f"**Last scanned Koinly report (tax year {koinly_report.tax_year}):**")
        kc1, kc2, kc3 = st.columns(3)
        kc1.metric("Short-term gains", fmt_dollars(koinly_report.crypto_stcg))
        kc2.metric("Long-term gains", fmt_dollars(koinly_report.crypto_ltcg))
        kc3.metric("Income (staking/DeFi)", fmt_dollars(koinly_report.crypto_income))
        _mismatch = koinly_report.provenance.get("income_total_mismatch")
        if _mismatch:
            st.warning(_mismatch)

    if ledger.get("koinly"):
        with st.expander("Per-owner crypto breakdown"):
            for owner, figures in sorted(ledger["koinly"].items()):
                st.caption(
                    f"{owner.title()}: STCG {fmt_dollars(figures['stcg'])}, "
                    f"LTCG {fmt_dollars(figures['ltcg'])}, "
                    f"Income {fmt_dollars(figures['income'])}"
                )

    if ledger.get("brokerage"):
        with st.expander("Per-owner brokerage breakdown"):
            for owner, accounts in sorted(ledger["brokerage"].items()):
                totals = derive_brokerage_totals({"koinly": {}, "brokerage": {owner: accounts}})
                st.caption(
                    f"{owner.title()} ({len(accounts)} account(s)): "
                    f"Interest {fmt_dollars(totals['interest_ytd'])}, "
                    f"Dividends {fmt_dollars(totals['ordinary_dividends_ytd'])}, "
                    f"STCG {fmt_dollars(totals['stcg_ytd'])}, "
                    f"LTCG {fmt_dollars(totals['ltcg_ytd'])}"
                )
