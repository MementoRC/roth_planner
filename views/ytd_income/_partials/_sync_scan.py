import streamlit as st

from engine.account_attribution import load_account_overrides, resolve_account_owner
from engine.data_bridge_browser import is_pyodide
from engine.instance_identity import CorruptInstanceOwnerError, load_instance_owner
from engine.pdf_ledger import (
    derive_brokerage_totals,
    derive_koinly_totals,
    load_ledger,
    save_ledger,
    write_brokerage_contribution,
    write_koinly_contribution,
)
from engine.pdf_owner import load_owner_map
from engine.portfolio_sync import save_ytd_snapshot
from models.household import Household
from models.ytd_income import YTDSnapshot
from views._format import fmt_dollars
from views._shared import run_folder_scan


def _warn_on_holder_name_mismatch(
    owner_key: str | None, resolved: str, owner_map: dict[str, str], account_label: str
) -> None:
    """WARN, never block. A name absent from owner_map (or no name at all --
    IBKR/Fidelity/UBS return owner_key=None) is silent: silence never means
    agreement, only that there was nothing to check against.
    """
    from engine.pdf_owner import resolve_owner

    named_owner = resolve_owner(owner_key, owner_map)
    if named_owner is not None and named_owner != resolved:
        st.warning(
            f"Account {account_label}: the statement's holder name maps to "
            f"'{named_owner}' but this instance attributes it to '{resolved}'. "
            "Double check the account attribution table on Setup ▸ Command Center."
        )


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
                    st.success(f"Synced NQO exercise data ({len(ytd_snap.gain_events)} gain events)")
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
            save_account_type_override,
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
        # Loaded once per render (not only inside the button branch below) so the
        # per-owner breakdown expander reflects on-disk ledger state even on
        # renders where "Scan folder" was not clicked this run.
        ledger = load_ledger()
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
                        _warn_on_holder_name_mismatch(rec.owner_key, resolved, owner_map, account_number)
                        ledger = write_brokerage_contribution(ledger, resolved, rec)

                    save_ledger(ledger)

                    brokerage_totals = derive_brokerage_totals(ledger)
                    _snap.interest_ytd = brokerage_totals["interest_ytd"]
                    _snap.tax_exempt_interest_ytd = brokerage_totals["tax_exempt_interest_ytd"]
                    _snap.ordinary_dividends_ytd = brokerage_totals["ordinary_dividends_ytd"]
                    _snap.stcg_ytd = brokerage_totals["stcg_ytd"]
                    _snap.ltcg_ytd = brokerage_totals["ltcg_ytd"]
                    applied_bits.append(
                        f"{len(stmt_taxable_now)} taxable brokerage account(s) "
                        f"({sum(len(v) for v in ledger['brokerage'].values())} total ledgered)"
                    )

                if result.koinly_reports:
                    from engine.koinly_report_pdf import save_koinly_report

                    for report in result.koinly_reports:
                        resolved = resolve_account_owner(
                            "koinly", report.owner_key or "unknown", account_overrides, instance_owner
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
                    st.info(
                        f"{len(stmt_unknown_now)} account(s) need a tax-status confirmation "
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

        statement_by_account = st.session_state.get("statement_by_account", {})
        if statement_by_account:
            stmt_taxable, stmt_excluded, stmt_unknown = partition_by_account_type(statement_by_account)

            if stmt_excluded:
                st.info(
                    "Excluded (retirement account, never counted toward taxable YTD income): "
                    + ", ".join(f"{acc} ({rec.broker}, {rec.account_type})" for acc, rec in stmt_excluded.items())
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
                    for account_number, rec in stmt_taxable.items():
                        resolved = resolve_account_owner(
                            rec.broker, account_number, account_overrides, instance_owner
                        )
                        ledger = write_brokerage_contribution(ledger, resolved, rec)
                    save_ledger(ledger)

                    brokerage_totals = derive_brokerage_totals(ledger)
                    prev_ytd = st.session_state.get("ytd_snapshot", YTDSnapshot())
                    prev_ytd.interest_ytd = brokerage_totals["interest_ytd"]
                    prev_ytd.tax_exempt_interest_ytd = brokerage_totals["tax_exempt_interest_ytd"]
                    prev_ytd.ordinary_dividends_ytd = brokerage_totals["ordinary_dividends_ytd"]
                    prev_ytd.stcg_ytd = brokerage_totals["stcg_ytd"]
                    prev_ytd.ltcg_ytd = brokerage_totals["ltcg_ytd"]
                    prev_ytd.with_snapshot_date()
                    st.session_state.ytd_snapshot = prev_ytd
                    st.session_state["ytd_manual_entry"] = False
                    save_ytd_snapshot(prev_ytd)
                    st.success(f"Applied {len(stmt_taxable)} taxable account(s) to YTD snapshot")
                    st.rerun()

        if not is_pyodide():
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
