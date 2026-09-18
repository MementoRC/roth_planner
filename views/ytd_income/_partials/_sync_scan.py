import streamlit as st

from engine.account_attribution import load_account_overrides, resolve_account_owner
from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.data_bridge_browser import is_pyodide
from engine.instance_identity import CorruptInstanceOwnerError, load_instance_owner
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
from models.household import Household
from models.ytd_income import YTDSnapshot
from views._format import fmt_dollars
from views._shared import run_folder_scan


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
            "Double check the account attribution table on Setup ▸ Command Center."
        )
        return True
    return False


def render_sync_scan_partial(hh: Household) -> None:
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

    # --- Section 1: YTD Income Entry ---
    st.markdown("### YTD Income Entry")

    if is_pyodide():
        st.caption(
            "Live sync requires a local install. "
            "Use the **⚙️ Setup → 🔗 Data bridge** tab to upload a snapshot."
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
                # Auto-deselect manual entry so the page switches to synced-data display
                st.session_state["ytd_manual_entry"] = False
                st.rerun()
            else:
                with col_status:
                    st.warning("FinExtract unavailable — use manual entry below")

        st.markdown("##### Import from PDF folder")
        st.caption(
            "Drop every statement in one folder — brokerage statements, your Koinly "
            "crypto tax report, and TurboTax 1040 exports. One scan identifies each file "
            "by its contents (filenames are ignored) and imports everything it recognizes: "
            "statement interest/dividends/gains, Koinly crypto figures, and prior-year 1040 MAGI."
        )
        from engine.brokerage_statement_pdf import (
            apply_account_type_overrides,
            load_account_type_overrides,
            load_statement_folder_path,
            load_statement_records,
            partition_by_account_type,
            pick_latest_per_account,
            save_statement_folder_path,
            save_statement_records,
            validate_local_folder,
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
                "owner — set it on **⚙️ Setup ▸ 🎛️ Command Center**."
            )
        # disabled=True (not hidden), same convention as Command Center's
        # "⟳ Sync everything" button in Task 5.
        if st.button("Scan folder", key="scan_pdf_folder_btn", disabled=not identity_set):
            # Local single-user desktop tool: path validation (under $HOME, no
            # '..') lives in validate_local_folder.
            folder_path, folder_err = validate_local_folder(folder_input)
            if folder_err:
                st.error(folder_err)
            else:
                save_statement_folder_path(str(folder_path))
                # Single scan entry point + single _pdf_1040_scanned writer
                # (W2 Part A) -- the actual scan_pdf_folder call, 1040-MAGI
                # candidate recording, and pdf-tax-cache persist all live in
                # run_folder_scan / scan_and_record now.
                result = run_folder_scan(folder_path).raw

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
                        _warn_on_holder_name_mismatch(
                            rec.owner_key, resolved, owner_map, account_number
                        )
                        ledger = write_brokerage_contribution(ledger, resolved, rec)

                    save_ledger(ledger)

                    brokerage_totals = derive_brokerage_totals(ledger)
                    apply_brokerage_totals(_snap, brokerage_totals)
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

                if applied_bits:
                    _snap.with_snapshot_date()
                    st.session_state.ytd_snapshot = _snap
                    st.session_state["ytd_manual_entry"] = False
                    save_ytd_snapshot(_snap)

                # Prior-year 1040 exports: cache merge + candidate record + the
                # single canonical _pdf_1040_scanned write already happened
                # inside run_folder_scan() above (single scan entry point,
                # single writer -- W2 Part A, audit defect #3). The
                # Parameters-tab confirm preview reads that same session key.

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
                    st.info("No importable financial PDFs found in that folder.")
                if applied_bits:
                    st.success("Applied to YTD snapshot: " + "; ".join(applied_bits))
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
                    st.info("Form 1040 MAGI saved — set filing status on Setup → Parameters.")
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

        if "statement_by_account" not in st.session_state:
            _cached_by_account = load_statement_records()
            if _cached_by_account:
                _cached_by_account = apply_account_type_overrides(
                    _cached_by_account, load_account_type_overrides()
                )
            st.session_state["statement_by_account"] = _cached_by_account

    # --- Section 2: statement review + Apply-to-YTD-snapshot ---
    # Deliberately OUTSIDE the is_pyodide() gate above -- reachable whenever
    # scan results exist in session state, regardless of platform. Today,
    # under Pyodide, "statement_by_account" is never populated: both writers
    # (the "Scan folder" button handler and the on-disk cache fallback just
    # above) live inside the local-only `else:` branch, so this is a no-op
    # there -- identical to before the split. Step 2's uploader will give
    # Pyodide a third writer of that same key, which is the whole point of
    # this move.
    statement_by_account = st.session_state.get("statement_by_account", {})
    if statement_by_account:
        ledger = _render_scan_review(
            statement_by_account, ledger, account_overrides, owner_map, instance_owner, identity_set
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
                "owner — set it on **⚙️ Setup ▸ 🎛️ Command Center**."
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
            apply_brokerage_totals(prev_ytd, brokerage_totals)
            prev_ytd.with_snapshot_date()
            st.session_state.ytd_snapshot = prev_ytd
            st.session_state["ytd_manual_entry"] = False
            save_ytd_snapshot(prev_ytd)
            st.success(f"Applied {len(stmt_taxable)} taxable account(s) to YTD snapshot")
            # WARN, NEVER BLOCK: the write above already completed --
            # this only withholds the immediate rerun so a fired
            # mismatch warning survives to be seen, instead of being
            # wiped by a same-render st.rerun() (see
            # _warn_on_holder_name_mismatch's docstring).
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
