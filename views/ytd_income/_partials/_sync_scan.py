import hashlib
from collections.abc import MutableMapping, Sequence
from datetime import datetime
from typing import Any, NamedTuple

import streamlit as st

from engine.account_attribution import load_account_overrides, resolve_account_owner
from engine.account_link import (
    NEW,
    LinkMap,
    link_candidates,
    load_link_map,
    remember_link,
    save_link_map,
    suggest_link,
)
from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.data_bridge_browser import is_pyodide
from engine.data_sources.record import (
    record_ira_balance_candidate,
    record_roth_balance_candidate,
    record_ss_fra_candidate,
)
from engine.instance_identity import CorruptInstanceOwnerError, load_instance_owner
from engine.ira_statement_balance import (
    apply_ira_statement,
    default_ira_owner,
    describe_ira_record,
    pick_latest_ira_records,
    to_ira_account_summary,
)
from engine.koinly_report_pdf import KoinlyReport
from engine.netbenefits_statement_pdf import (
    NetBenefitsStatement,
    apply_netbenefits_statement,
    default_netbenefits_owner,
    matching_403bs,
    pick_latest_netbenefits,
    renamed_plan_candidates,
    to_account_summary,
)
from engine.pdf_import import PdfImportResult
from engine.pdf_ledger import (
    PdfLedger,
    apply_koinly_totals,
    derive_brokerage_totals,
    latest_brokerage_period_end,
    load_ledger,
    off_year_contributions,
    off_year_statement_note,
    save_ledger,
    statement_in_tax_year,
    write_brokerage_contribution,
    write_koinly_contribution,
)
from engine.pdf_owner import learn_owner, load_owner_map, resolve_owner, save_owner_map
from engine.portfolio_sync import PortfolioSnapshot, load_snapshot, save_snapshot, save_ytd_snapshot
from engine.portfolio_sync.as_of import AccountKey, account_key, as_of_label
from engine.portfolio_sync.portfolio import CorruptPortfolioCacheError
from engine.portfolio_sync.shapes import AccountSummary
from engine.portfolio_sync.ytd import apply_brokerage_totals
from engine.ssa_statement_pdf import (
    SsaStatement,
    benefit_comparison,
    default_ssa_owner,
    pick_latest_ssa,
    save_ssa_pdf_estimates,
    statement_age_notice,
)
from engine.ubs_activity_csv import (
    UbsActivityOrder,
    UbsActivityParseError,
    federal_withholding,
    parse_ubs_activity_text,
)
from engine.ubs_activity_store import UbsActivityStoreError, load_ubs_orders, save_ubs_orders
from engine.ubs_exercise_ytd import (
    apply_ubs_exercise_income,
    correct_ubs_option_basis,
    split_legacy_ubs_withholding,
)
from models.household import Household
from models.sourced import Source
from models.ytd_income import YTDSnapshot, describe_kept_manual
from views._format import fmt_dollars, md_dollars, ssa_comparison_table
from views._pdf_runtime import ensure_pdf_backend, pdf_backend_error
from views._shared import (
    auto_deselect_manual_entry,
    operator_slot,
    run_folder_scan,
    run_uploaded_scan,
)
from views._store_transaction import store_transaction


def _save_snapshot_or_report(snap: PortfolioSnapshot) -> bool:
    """Persist *snap*; on an unreadable cache file show an error and return False."""
    try:
        save_snapshot(snap)
    except CorruptPortfolioCacheError:
        st.error(
            "The portfolio cache file is unreadable; it was left untouched and this "
            "statement was not applied."
        )
        return False
    return True


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
    owner_key: str | None,
    resolved: str,
    owner_map: dict[str, str],
    account_label: str,
    override_key: tuple[str, str],
) -> bool:
    """RECORD, never block. A name absent from owner_map (or no name at all --
    IBKR/Fidelity/UBS return owner_key=None) is silent: silence never means
    agreement, only that there was nothing to check against.

    A detected mismatch is written into the fixed
    ``st.session_state["holder_name_mismatches"]`` dict (keyed by a stable id
    derived from the normalized *owner_key* plus *override_key*, the
    ``(broker, account_number)``-shaped tuple ``save_account_override`` takes)
    rather than only warned about here -- ``_render_holder_name_mismatches``,
    below, is the persistent panel that re-checks and offers the actual
    remedy on every render, so the entry survives on its own across a rerun
    and is never lost.

    Returns whether a mismatch was recorded. The scan-loop call sites ignore
    this; the Apply call site (``_render_scan_review``) still uses it to
    withhold its own immediate ``st.rerun()`` -- this keeps BOTH that call
    site's "Applied to YTD snapshot" confirmation AND the panel's
    just-recorded row on screen for this same render (``render_sync_scan_
    results`` reserves the panel's on-screen position with ``st.container()``
    before the Apply block runs, then fills it last, so a same-render entry
    is visible immediately -- see that function's docstring). Withholding the
    rerun still matters: a same-render ``st.rerun()`` would abort before
    either the confirmation or the panel slot is ever flushed to the browser
    (see the SSA-sync buttons in ``views/setup/_partials/_accounts.py`` for
    the same idiom).
    """
    from engine.pdf_owner import normalize_owner_key, resolve_owner

    named_owner = resolve_owner(owner_key, owner_map)
    if named_owner is None or named_owner == resolved:
        return False

    entry_id = f"{normalize_owner_key(owner_key)}|{override_key[0]}|{override_key[1]}"
    mismatches = st.session_state.get("holder_name_mismatches")
    if mismatches is None:
        mismatches = {}
        st.session_state["holder_name_mismatches"] = mismatches
    mismatches[entry_id] = {
        "owner_key": owner_key,
        "named_owner": named_owner,
        "resolved": resolved,
        "account_label": account_label,
        "override_key": override_key,
    }
    return True


def _brokerage_owner(
    rec: BrokerageStatementRecord,
    account_overrides: dict[tuple[str, str], str],
    instance_owner: str,
    owner_map: dict[str, str],
) -> str:
    """Whose brokerage statement this is: an override, else the learned
    holder name, else the instance owner -- the Koinly loop's order, so a
    statement with no override lands on its named holder instead of the
    instance owner (issue #38). The account number, never the
    ``"<broker>:"`` dict key, is the override key."""
    return resolve_account_owner(
        rec.broker,
        rec.account_number,
        account_overrides,
        instance_owner,
        holder_key=rec.owner_key,
        owner_map=owner_map,
    )


_LEGACY_KOINLY_KEY = "unknown"


def _koinly_override_key(
    report: KoinlyReport, account_overrides: dict[tuple[str, str], str]
) -> str:
    """The override key a Koinly report resolves under.

    A nameless report's key is its own fingerprint, so an override can tell
    two nameless reports apart (issue #38). Before that every nameless report
    shared ``("koinly", "unknown")``; an override saved under that key (no
    code path writes one, but a hand-edited store may hold it) still applies
    to a nameless report with no override of its own, so upgrading never
    re-attributes data.
    """
    key = report.override_key
    if (
        report.owner_key is None
        and ("koinly", key) not in account_overrides
        and ("koinly", _LEGACY_KOINLY_KEY) in account_overrides
    ):
        return _LEGACY_KOINLY_KEY
    return key


def _warn_on_brokerage_holder_mismatch(
    rec: BrokerageStatementRecord,
    account_overrides: dict[tuple[str, str], str],
    instance_owner: str,
    owner_map: dict[str, str],
) -> bool:
    """Record a mismatch when the holder name's learned owner differs from
    the account's SET owner (its override, else this instance's default).

    Compared against the set owner, not :func:`_brokerage_owner`'s answer:
    that answer already follows the name, so comparing with it would never
    warn and the user would lose the signal that an account with no
    confirmed owner was filed by its holder name alone. Confirming either
    way in the panel (learn the name, or save an override) clears it.
    """
    set_owner = resolve_account_owner(
        rec.broker, rec.account_number, account_overrides, instance_owner
    )
    return _warn_on_holder_name_mismatch(
        rec.owner_key, set_owner, owner_map, rec.account_number, (rec.broker, rec.account_number)
    )


def _render_holder_name_mismatches(instance_owner: str) -> None:
    """Persistent panel for every holder-name mismatch recorded by
    ``_warn_on_holder_name_mismatch``. Called on EVERY render (never nested
    inside a one-shot button-click branch) -- a button created only on the
    render that first detects a mismatch would never get the chance to fire
    on the NEXT render, which is when its own click actually lands.

    Each entry is RE-CHECKED against freshly-loaded stores before being
    shown: it may have self-healed since it was recorded (a hand edit, a
    future bulk-accept), and a self-healed entry is dropped silently rather
    than shown as still pending. For each entry that still mismatches, two
    one-click remedies are offered:

    - "Holder name is <resolved>" -- writes the name map (owner_key ->
      resolved), restoring a production writer for ``learn_owner``/
      ``save_owner_map`` (PR #452 deleted the last one).
    - "Account is actually <named_owner>" -- writes the account override via
      the existing ``save_account_override`` path.

    Either write removes the entry and calls ``st.rerun()`` OUTSIDE any
    try/except, so a caught write failure can never suppress the rerun that
    would otherwise wipe the ``st.error`` reporting it.
    """
    mismatches: dict[str, dict] = st.session_state.get("holder_name_mismatches") or {}
    if not mismatches:
        return

    from engine.account_attribution import CorruptAccountAttributionError, save_account_override
    from engine.pdf_owner import learn_owner, resolve_owner, save_owner_map

    fresh_owner_map = load_owner_map()
    fresh_overrides = load_account_overrides()

    for entry_id, entry in list(mismatches.items()):
        broker, account_key = entry["override_key"]
        resolved_now = resolve_account_owner(broker, account_key, fresh_overrides, instance_owner)
        named_now = resolve_owner(entry["owner_key"], fresh_owner_map)
        if named_now is None or named_now == resolved_now:
            del mismatches[entry_id]
            continue

        if (broker, account_key) in fresh_overrides:
            filed = f"but this account is set to '{resolved_now}'"
        elif broker == "koinly":
            filed = f"but this instance attributes it to '{resolved_now}'"
        else:
            # Brokerage with no override: filed under the named holder
            # (issue #38), which differs from this instance's default owner.
            filed = (
                f"so it was filed under '{named_now}', not this instance's "
                f"default owner '{resolved_now}'"
            )
        st.warning(
            f"Account {entry['account_label']}: holder name '{entry['owner_key']}' "
            f"maps to '{named_now}' {filed}. Which is correct?"
        )
        if st.button(f"Holder name is {resolved_now}", key=f"holder_mismatch_name_{entry_id}"):
            save_owner_map(learn_owner(entry["owner_key"], resolved_now, fresh_owner_map))
            del mismatches[entry_id]
            st.rerun()
        if st.button(f"Account is actually {named_now}", key=f"holder_mismatch_account_{entry_id}"):
            try:
                save_account_override(broker, account_key, named_now)
            except CorruptAccountAttributionError:
                st.error(
                    "⚠️ Account attribution store is unreadable (corrupt or truncated); "
                    "this override was NOT saved."
                )
            else:
                del mismatches[entry_id]
                st.rerun()


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
    brokerage_totals: dict[str, float],
    ledger: PdfLedger,
    snap: YTDSnapshot,
    *,
    applied_orders: Sequence[UbsActivityOrder] | None = None,
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

    *applied_orders* is the UBS order list LAST applied, for the one-time
    pre-1b withholding migration (split_legacy_ubs_withholding). Defaults to
    the store as loaded, which equals the last apply on every path except the
    CSV upload, which merges new orders first and passes its pre-merge list.
    """
    ubs_orders = _load_ubs_orders_safe()
    corrected_totals, warnings = correct_ubs_option_basis(
        brokerage_totals, ledger, ubs_orders, tax_year=snap.tax_year
    )
    apply_brokerage_totals(snap, corrected_totals)
    kept = snap.settle_manual_marks(
        as_of=latest_brokerage_period_end(ledger, tax_year=snap.tax_year)
    )
    owner = operator_slot()
    warnings += split_legacy_ubs_withholding(
        snap, ubs_orders if applied_orders is None else applied_orders, ubs_orders, owner=owner
    )
    warnings += apply_ubs_exercise_income(snap, ubs_orders, owner=owner)
    warnings += describe_kept_manual(snap, kept)
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


# Session keys _write_scan_result changes, put back if its transaction rolls back.
_SCAN_SESSION_KEYS: tuple[str, ...] = (
    "statement_by_account",
    "ytd_snapshot",
    "ira_statements",
    "netbenefits_statements",
    "ssa_statements",
    "holder_name_mismatches",
)


def _apply_scan_result(
    result: PdfImportResult,
    *,
    ledger: PdfLedger,
    account_overrides: dict[tuple[str, str], str],
    owner_map: dict[str, str],
    instance_owner: str,
    not_found_message: str = "No importable financial PDFs found.",
) -> PdfLedger:
    """Apply one scanned ``PdfImportResult`` (see :func:`_write_scan_result`).

    Every store write of the apply -- statement cache, ledger, Koinly report,
    YTD snapshot -- runs in ONE store transaction (spec §2): a failure saves
    none of them and puts back the session keys the apply changed. Parsing
    already happened in the caller, outside the transaction.
    """
    with store_transaction(st.session_state, *_SCAN_SESSION_KEYS):
        return _write_scan_result(
            result,
            ledger=ledger,
            account_overrides=account_overrides,
            owner_map=owner_map,
            instance_owner=instance_owner,
            not_found_message=not_found_message,
        )


def _write_scan_result(
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

    Run by :func:`_apply_scan_result` (shared by the folder-scan and
    uploaded-PDF handlers) so
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
        load_statement_records,
        merge_latest_per_account,
        partition_by_account_type,
        pick_latest_per_account,
        save_statement_records,
    )

    # Brokerage statements -> newest record per account.
    by_account = pick_latest_per_account(result.brokerage_records)
    overrides = load_account_type_overrides()
    by_account = apply_account_type_overrides(by_account, overrides)
    # MERGE into the session's accounts, never replace: a scan that holds no
    # statement for an account (an SSA-only upload, a digest-skipped file)
    # must not drop that account's pending tax-status confirmation
    # (audit-1008 F-69). Before the first scan of a session the session has
    # no copy yet, so start from the disk cache the review panel would load.
    if "statement_by_account" in st.session_state:
        held = st.session_state.get("statement_by_account") or {}
    else:
        held = apply_account_type_overrides(load_statement_records(), overrides)
    st.session_state["statement_by_account"] = apply_account_type_overrides(
        merge_latest_per_account(held, by_account.values()), overrides
    )
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
        for rec in stmt_taxable_now.values():
            # rec.account_number, not the dict key: a second broker's
            # same-number account is keyed "<broker>:<number>".
            resolved = _brokerage_owner(rec, account_overrides, instance_owner, owner_map)
            _warn_on_brokerage_holder_mismatch(rec, account_overrides, instance_owner, owner_map)
            ledger = write_brokerage_contribution(ledger, resolved, rec)

        save_ledger(ledger)

        brokerage_totals = derive_brokerage_totals(ledger, tax_year=_snap.tax_year)
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
            override_key = _koinly_override_key(report, account_overrides)
            resolved = resolve_account_owner(
                "koinly",
                override_key,
                account_overrides,
                instance_owner,
                holder_key=report.owner_key,
                owner_map=owner_map,
            )
            _warn_on_holder_name_mismatch(
                report.owner_key,
                resolved,
                owner_map,
                f"Koinly {report.tax_year}",
                ("koinly", override_key),
            )
            ledger = write_koinly_contribution(ledger, resolved, report)

        save_ledger(ledger)
        save_koinly_report(result.koinly_reports[-1])

        for warning in describe_kept_manual(_snap, apply_koinly_totals(_snap, ledger)):
            st.warning(warning)
        applied_bits.append(
            f"Koinly crypto ({len(result.koinly_reports)} report(s), "
            f"{len(ledger['koinly'])} owner(s))"
        )

    # Documents from another tax year stay in the ledger but are skipped by
    # the year-gated totals above (audit-1008 F-10): say so, never silently.
    # Statement notes are drawn by _render_scan_review (below the scan, and
    # on every later render), so they are not repeated here.
    if result.koinly_reports:
        for note in off_year_contributions(ledger, _snap.tax_year, brokerage=False):
            st.info(note)

    # NetBenefits 403(b) statements are a BALANCE source, never YTD income:
    # they are only queued here, and applied (to the portfolio snapshot and a
    # your_ira/spouse_ira candidate) from the review panel once the user
    # confirms whose plan it is -- see _render_netbenefits_review.
    if result.netbenefits_statements:
        st.session_state["netbenefits_statements"] = pick_latest_netbenefits(
            [
                *(st.session_state.get("netbenefits_statements") or []),
                *result.netbenefits_statements,
            ]
        )

    # IRA statements (Vanguard/Fidelity/IBKR) keep feeding YTD exactly as above;
    # their stated account value is ALSO a balance source, queued here and
    # applied from the review panel once the user confirms whose IRA it is --
    # see _render_ira_statement_review.
    ira_queued = pick_latest_ira_records(
        [*(st.session_state.get("ira_statements") or []), *by_account.values()]
    )
    if ira_queued:
        st.session_state["ira_statements"] = ira_queued
    new_ira_count = len(pick_latest_ira_records(by_account.values()))

    # SSA statements are an SS-at-FRA source, never YTD income: queued here,
    # applied (as a your_ss_fra/spouse_ss_fra candidate) from the review panel
    # once the user confirms whose statement it is -- see _render_ssa_review.
    if result.ssa_statements:
        st.session_state["ssa_statements"] = pick_latest_ssa(
            [*(st.session_state.get("ssa_statements") or []), *result.ssa_statements]
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
    if result.netbenefits_statements:
        parsed_bits.append(
            f"{len(result.netbenefits_statements)} NetBenefits 403(b) statement(s) "
            "— review below to apply the balance"
        )
    if new_ira_count:
        parsed_bits.append(f"{new_ira_count} IRA statement(s) — review below to apply the balance")
    if result.ssa_statements:
        parsed_bits.append(
            f"{len(result.ssa_statements)} Social Security statement(s) "
            "— review below to apply the benefit estimate"
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
        st.info(
            "Form 1040 MAGI saved — confirm the return's filing status in "
            "**1040 Import**, below on this page."
        )
    if result.skipped:
        st.info(
            "Skipped (recognized, nothing to import): "
            + "; ".join(f"{name} — {why}" for name, why in result.skipped)
        )
    if result.unrecognized:
        st.warning("Unrecognized (no known format): " + ", ".join(result.unrecognized))
    if result.errors:
        st.warning(f"{len(result.errors)} file(s) could not be parsed:")
        # A preformatted block keeps a multi-line diagnostic (e.g. the
        # redacted header lines from a Fidelity statement) intact and
        # copyable -- markdown would fold its newlines into one line.
        st.code("\n\n".join(f"{name}: {msg}" for name, msg in result.errors), language=None)

    return ledger


# Session keys holding scan results still awaiting review, plus the scanned-file
# digest bookkeeping. Applied data (portfolio/YTD snapshots, ledger, owner map,
# on-disk caches) is deliberately NOT listed.
_QUEUED_SCAN_KEYS: tuple[str, ...] = (
    "ssa_statements",
    "netbenefits_statements",
    "ira_statements",
    "_pdf_scanned_digests",
    "_pdf_1040_scanned",
)


def clear_queued_statements(state: MutableMapping[str, Any]) -> None:
    """Drop every queued scan result and reset the uploader's selected files."""
    for key in _QUEUED_SCAN_KEYS:
        state.pop(key, None)
    state["pdf_upload_nonce"] = state.get("pdf_upload_nonce", 0) + 1


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
        "Upload brokerage statements, Fidelity NetBenefits 403(b) statements, "
        "Social Security statements, your Koinly crypto tax report, or a TurboTax 1040 export directly — "
        "the same content-based recognition "
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

    # The nonce in the key is bumped by clear_queued_statements -- a widget
    # with a new key starts empty, which is the only way to drop its files.
    uploaded = st.file_uploader(
        "PDFs to import",
        type=["pdf"],
        accept_multiple_files=True,
        key=f"pdf_upload_{st.session_state.get('pdf_upload_nonce', 0)}",
    )
    if not identity_set:
        st.caption(
            "Importing is unavailable until this planner instance has an "
            "owner — set it in **Review Changes**."
        )
    scan_clicked = st.button(
        "Scan uploaded PDFs", key="scan_uploaded_pdfs_btn", disabled=not identity_set
    )
    # Reserved here but filled only after any scan below, so a scan that
    # queues statements shows the button in the same run, not the next one.
    clear_slot = st.empty()
    if not (scan_clicked and uploaded):
        _render_clear_queue_button(clear_slot)
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
            combined.netbenefits_statements.extend(single.netbenefits_statements)
            combined.ssa_statements.extend(single.ssa_statements)
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
    _render_clear_queue_button(clear_slot)
    if is_pyodide():
        st.caption(
            "⚠️ This browser session's data is lost on reload — use "
            "**Data › Backup & Transfer ▸ 📦 Export my data** to keep it."
        )
    return ledger


def _render_clear_queue_button(slot: Any) -> None:
    """Draw "Clear queued statements" into *slot* when anything is queued."""
    if not any(st.session_state.get(k) for k in _QUEUED_SCAN_KEYS):
        return
    if slot.button(
        "Clear queued statements",
        key="pdf_clear_queue",
        help=(
            "Empties the statements waiting for review and the uploaded files. "
            "Nothing already applied is changed."
        ),
    ):
        clear_queued_statements(st.session_state)
        st.rerun()


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
            "owner — set it in **Review Changes**."
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

    # One transaction (spec §2): the merged orders and the recomputed YTD
    # snapshot are saved together or not at all. A corrupt store must stop the
    # import: merging into [] and saving would replace orders this page never
    # read (audit-1008 F-70).
    try:
        with store_transaction(st.session_state, "ytd_snapshot"):
            existing = load_ubs_orders()
            merged = _merge_ubs_orders(existing, new_orders)
            save_ubs_orders(merged)
            # Recompute immediately so the correction/exercise-income takes
            # effect without a separate scan/apply click -- see this
            # function's docstring.
            snap = st.session_state.get("ytd_snapshot", YTDSnapshot())
            brokerage_totals = derive_brokerage_totals(ledger, tax_year=snap.tax_year)
            ubs_warnings = _apply_ubs_correction(
                brokerage_totals, ledger, snap, applied_orders=existing
            )
            snap.with_snapshot_date()
            st.session_state.ytd_snapshot = snap
            _manual_entry_kept_explicit = not auto_deselect_manual_entry(st.session_state)
            save_ytd_snapshot(snap)
    except UbsActivityStoreError as exc:
        st.error(
            f"UBS CSV(s) not imported: the stored UBS exercise data is unreadable ({exc}). "
            "Importing now would overwrite the orders already stored. Repair or move "
            "that file, then import again."
        )
        return

    total_bargain = sum(o.bargain_element for o in merged)
    total_withholding = sum(federal_withholding(o) for o in merged)
    st.success(
        md_dollars(
            f"Imported {len(new_orders)} of {len(uploaded)} file(s) ({failed} failed). "
            f"Now storing {len(merged)} UBS exercise order(s): "
            f"bargain element {fmt_dollars(total_bargain)}, "
            f"federal withholding {fmt_dollars(total_withholding)}."
        )
    )

    if _manual_entry_kept_explicit:
        st.caption(
            "Manual entry stayed ON — you turned it on yourself, so this "
            "import did not switch you back to synced-data display."
        )
    for warning in ubs_warnings:
        st.warning(warning)
    # WARN, NEVER BLOCK: the writes above already completed -- this only
    # withholds the immediate rerun so a fired warning survives to be seen,
    # same idiom as _apply_ubs_correction's other call sites.
    if not ubs_warnings:
        st.rerun()


def render_ytd_finextract_sync(hh: Household) -> None:
    """Render the YTD "Sync from FinExtract" button and its handler.

    Lifted out of ``render_sync_scan_partial`` into Import Statements' Legacy
    expander (redesign PR 3b), unchanged. The handler writes
    ``st.session_state.ytd_snapshot`` and reruns; nothing in the scan context
    depends on its result, so it returns nothing.
    """
    if is_pyodide():
        st.caption(
            "Live sync requires a local install. "
            "Use **Data › Backup & Transfer** to upload a snapshot."
        )
        return
    col_sync, col_status = st.columns([1, 3])
    with col_sync:
        sync_ytd = st.button(
            "Sync from FinExtract",
            help="Pull NQO exercise income from ingestion server",
            key="ytd_sync_btn",
        )
    if not sync_ytd:
        return
    from engine.portfolio_sync import (
        apply_option_exercises,
        fetch_option_exercises,
        fetch_ytd_snapshot,
    )

    # Neither fetch call was guarded -- and fetch_option_exercises()
    # runs BEFORE the snapshot_date check below -- so a network error,
    # malformed payload, or non-200 from either one crashed the whole
    # page render (issue #490). Both calls (and the
    # overlay/apply that depend on their results) are wrapped so an
    # exception is reported through col_status like the "unavailable"
    # case below, instead of escaping -- and NONE of the success
    # side-effects (session_state write, save, rerun) run when it does.
    try:
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
        # brokerage-statement-sourced -- none of these are touched by
        # fetch_ytd_snapshot, so overlay() preserves every one of them.
        prev = st.session_state.get("ytd_snapshot") or YTDSnapshot()
        ytd_snap = prev.overlay(
            manually_entered=ytd_status.manually_entered,
            snapshot_date=ytd_status.snapshot_date,
        )
        exercises = fetch_option_exercises(tax_year=ytd_snap.tax_year)
        if exercises.server_available:
            ytd_snap = apply_option_exercises(ytd_snap, exercises, hh, owner=operator_slot())
    except Exception as exc:  # noqa: BLE001 -- one bad sync must not crash the page
        with col_status:
            st.warning(f"FinExtract sync failed: {exc}")
        return
    if not ytd_status.snapshot_date:
        with col_status:
            st.warning(
                "FinExtract unavailable — use **Add an entry by hand** on This Year's Income"
            )
        return
    st.session_state.ytd_snapshot = ytd_snap
    save_ytd_snapshot(ytd_snap)
    with col_status:
        st.success(f"Synced NQO exercise data ({len(ytd_snap.gain_events)} gain events)")
        # Auto-deselect manual entry so the page switches to synced-data
        # display, UNLESS the user explicitly chose this setting themselves
        # (see auto_deselect_manual_entry).
        if not auto_deselect_manual_entry(st.session_state):
            st.caption(
                "Manual entry stayed ON — you turned it on yourself, so this "
                "sync did not switch you back to synced-data display."
            )
    st.rerun()


def render_sync_scan_partial(hh: Household) -> ScanRenderContext:
    """Render the ways to get statement data IN: the
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
    # No heading here. This partial's only caller is views/data_pages.py's
    # Import page, which already titles the card "PDF Statements" (st.subheader, an
    # h3) immediately above; a "### YTD Income Entry" h3 right under it
    # rendered two same-weight headings back to back with nothing between --
    # the same defect the shell's duplicate "Command Center" subheader had.
    # The column title names the section; the sub-sections below sit a level
    # down.
    if not is_pyodide():
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
                    "owner — set it in **Review Changes**."
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

    ``_render_holder_name_mismatches`` (the persistent panel) must appear
    FIRST on screen -- above the review table -- but ``_render_scan_review``
    below is what actually RECORDS a fresh mismatch (its Apply block calls
    ``_warn_on_holder_name_mismatch``), and that happens AFTER this function
    starts. Rendering the panel immediately would miss any entry recorded
    later in this very call. Instead an empty ``st.container()`` reserves the
    panel's on-screen position FIRST; the panel itself is filled into that
    reserved slot as the LAST statement below, once every recorder for this
    render has already run -- so it sees same-render entries while still
    drawing at the top. Deliberately NOT wrapped in try/finally: a
    ``st.rerun()`` raised from ``_render_scan_review`` (the withheld-rerun
    path aside, a UBS-warning path still reruns) must unwind straight out of
    this function without the slot being filled -- rendering more widgets
    during that unwind is unsafe.
    """
    panel_slot = st.container()

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

    if st.session_state.get("netbenefits_statements"):
        _render_netbenefits_review(
            owner_map=ctx.owner_map,
            instance_owner=ctx.instance_owner,
            identity_set=ctx.identity_set,
        )

    if st.session_state.get("ira_statements"):
        _render_ira_statement_review(
            owner_map=ctx.owner_map,
            instance_owner=ctx.instance_owner,
            identity_set=ctx.identity_set,
        )

    if st.session_state.get("ssa_statements"):
        _render_ssa_review(
            owner_map=ctx.owner_map,
            instance_owner=ctx.instance_owner,
            identity_set=ctx.identity_set,
        )

    if not is_pyodide():
        _render_koinly_summary(ledger)

    with panel_slot:
        _render_holder_name_mismatches(ctx.instance_owner)


def _queue_ids(items: Sequence[object]) -> list[str]:
    """A stable widget-key id per queued statement, from its own content.

    Review-queue widgets that hold a choice (owner radio, link selectbox)
    must be keyed by WHICH statement they belong to, not by queue position:
    after an Apply or Dismiss the remaining statements move up a slot, and a
    position-keyed widget would hand the removed statement's stored choice
    to the next one (audit-1008 F-37). Identical statements get a suffix so
    keys stay unique.
    """
    ids: list[str] = []
    seen: dict[str, int] = {}
    for item in items:
        digest = hashlib.sha256(repr(item).encode()).hexdigest()[:12]
        n = seen.get(digest, 0)
        seen[digest] = n + 1
        ids.append(digest if n == 0 else f"{digest}_{n}")
    return ids


def _render_netbenefits_review(
    *, owner_map: dict[str, str], instance_owner: str, identity_set: bool
) -> None:
    """Review + apply each queued NetBenefits 403(b) statement.

    Applying REPLACES the chosen owner's 403(b) account(s) in the portfolio
    snapshot with the statement's balance and holdings -- the Rollover IRA,
    Roth and brokerage accounts are untouched -- then records that owner's
    new pre-tax total as a ``Source.PDF`` ``your_ira``/``spouse_ira``
    candidate, so the Command Center's pending-review gate decides whether it
    replaces the committed balance. Nothing here writes YTD income.

    Whose plan it is comes from the learned holder-name map, else this
    instance's owner; the radio lets the user correct it, and a holder name
    that was not already mapped to the chosen owner is learned on Apply so the
    next statement pre-selects correctly.
    """
    pending: list[NetBenefitsStatement] = list(st.session_state.get("netbenefits_statements") or [])
    st.markdown("##### NetBenefits 403(b) statements")
    snap: PortfolioSnapshot = (
        st.session_state.get("portfolio_snapshot") or load_snapshot() or PortfolioSnapshot()
    )
    for i, (stmt, qid) in enumerate(zip(pending, _queue_ids(pending), strict=True)):
        st.write(
            md_dollars(
                f"**{stmt.plan_name}** — balance {fmt_dollars(stmt.ending_balance)} "
                f"(vested {fmt_dollars(stmt.vested_balance)}) as of {stmt.period_end}"
            )
        )
        if stmt.holdings:
            st.caption(
                md_dollars(
                    "Holdings: "
                    + "; ".join(f"{h.description} {fmt_dollars(h.value)}" for h in stmt.holdings)
                )
            )
        for warning in stmt.warnings:
            st.warning(warning)

        options = ["you", "spouse"]
        default = default_netbenefits_owner(stmt, owner_map, instance_owner)
        owner = st.radio(
            "Whose plan is this?",
            options,
            index=options.index(default),
            key=f"netbenefits_owner_{qid}",
            horizontal=True,
        )
        replaces = _render_renamed_plan_choice(snap, stmt, owner, qid)
        replaced = matching_403bs(snap, to_account_summary(stmt, owner), replaces=replaces)
        if replaced:
            st.caption(
                md_dollars(
                    "Replaces: "
                    + "; ".join(
                        f"{a.account_name or '403(b)'} {fmt_dollars(a.total_value)} "
                        f"({as_of_label(a)})"
                        for a in replaced
                    )
                )
            )
        else:
            st.caption("Adds a 403(b) account — no plan on file for this person matches it.")

        preview = apply_netbenefits_statement(snap, stmt, owner, replaces=replaces)
        if preview.kept_newer is not None:
            st.warning(
                f"403(b) ({owner}) kept: cache is {as_of_label(preview.kept_newer)}, newer than "
                f"this statement (as of {stmt.period_end})."
            )
        apply_label = "Use anyway" if preview.kept_newer is not None else "Apply 403(b) balance"
        if st.button(
            apply_label,
            key=f"netbenefits_apply_{i}",
            disabled=not identity_set,
        ):
            outcome = apply_netbenefits_statement(snap, stmt, owner, force=True, replaces=replaces)
            # One transaction (spec §2): the snapshot, the balance candidate and
            # the learned holder name are saved together or not at all.
            with store_transaction(st.session_state, "portfolio_snapshot"):
                if not _save_snapshot_or_report(outcome.snapshot):
                    continue
                st.session_state["portfolio_snapshot"] = outcome.snapshot
                record_ira_balance_candidate(
                    outcome.field_key, outcome.ira_total, outcome.detail, datetime.now()
                )
                if stmt.owner_key and resolve_owner(stmt.owner_key, owner_map) != owner:
                    save_owner_map(learn_owner(stmt.owner_key, owner, owner_map))
            st.session_state["netbenefits_statements"] = [s for s in pending if s is not stmt]
            st.rerun()
        if st.button("Dismiss", key=f"netbenefits_dismiss_{i}"):
            st.session_state["netbenefits_statements"] = [s for s in pending if s is not stmt]
            st.rerun()

    if not identity_set:
        st.caption(
            "Applying is unavailable until this planner instance has an "
            "owner — set it in **Review Changes**."
        )


_SEPARATE_PLAN_LABEL = "No — it's a separate plan"


def _render_renamed_plan_choice(
    snap: PortfolioSnapshot, stmt: NetBenefitsStatement, owner: str, qid: str
) -> str | None:
    """Ask whether a statement matching no plan on file renames one of them.

    NetBenefits prints no plan or account number, so a plan renamed between
    statements looks like a new plan and would sit beside the old one as a
    double count (issue #38). Only the user can tell, so this asks, and the
    default is "separate plan": a wrong replace loses a plan, a wrong add is a
    visible double count. Returns the chosen plan's name, or ``None``.
    """
    candidates = renamed_plan_candidates(snap, to_account_summary(stmt, owner))
    if not candidates:
        return None
    names = [a.account_name for a in candidates]
    cand_sig = hashlib.sha256(repr(names).encode()).hexdigest()[:8]
    chosen = st.selectbox(
        "Is this a renamed plan already on file?",
        [None, *names],
        index=0,
        format_func=lambda n: _SEPARATE_PLAN_LABEL if n is None else n,
        key=f"netbenefits_renamed_{qid}_{owner}_{cand_sig}",
    )
    return chosen if isinstance(chosen, str) else None


_NO_LINK_LABEL = "None — it's a separate account"


def _render_account_link_choice(
    rec: BrokerageStatementRecord,
    snap: PortfolioSnapshot,
    owner: str,
    link_map: LinkMap,
    qid: str,
) -> tuple[AccountKey | None, list[AccountSummary]]:
    """Ask which account on file the statement replaces.

    Returns ``(selected account key or None, candidates not selected)``. The
    pre-selection comes from ``suggest_link``; with candidates but no
    suggestion it defaults to "separate account" and the user is told so.
    """
    candidates = link_candidates(rec, snap, owner, link_map)
    if not candidates:
        return None, []
    suggestion = suggest_link(rec, candidates, link_map)
    none_idx = len(candidates)
    if suggestion is None or suggestion == NEW:
        # Wrongly replacing an account loses data; wrongly keeping both is a
        # visible double count the user can fix -- so never guess a target.
        default = none_idx
    else:
        default = [account_key(a) for a in candidates].index(suggestion)

    def _fmt(idx: int) -> str:
        if idx == none_idx:
            return _NO_LINK_LABEL
        a = candidates[idx]
        return md_dollars(f"{a.account_name or 'unnamed account'} — {fmt_dollars(a.total_value)}")

    # The options are candidate POSITIONS, so the key also carries the
    # candidate list: once another statement takes a candidate (or an
    # account is added), a stored position would name a different account.
    # A new candidate list starts a fresh widget at its computed default
    # (audit-1008 F-37).
    cand_sig = hashlib.sha256(repr([account_key(a) for a in candidates]).encode()).hexdigest()[:8]
    chosen = st.selectbox(
        "Replaces which account on file?",
        list(range(none_idx + 1)),
        index=default,
        format_func=_fmt,
        key=f"ira_statement_link_{qid}_{owner}_{cand_sig}",
    )
    if suggestion is None:
        st.caption(
            "No account on file matched this statement's number. If it is the same IRA "
            "as one listed, pick it here; otherwise leave it as a separate account."
        )
    if chosen == none_idx:
        return None, candidates
    return account_key(candidates[chosen]), [a for j, a in enumerate(candidates) if j != chosen]


def _render_ira_statement_review(
    *, owner_map: dict[str, str], instance_owner: str, identity_set: bool
) -> None:
    """Review + apply each queued IRA statement (Vanguard, Fidelity, IBKR).

    Applying sets that statement account in the portfolio snapshot (newer
    as-of wins; "Use anyway" forces it) and records the owner's new pre-tax
    total as a ``Source.PDF`` ``your_ira``/``spouse_ira`` candidate -- or, for a
    Roth IRA, the owner's Roth total as ``your_roth``/``spouse_roth`` -- so the
    Command Center's pending-review gate decides whether it replaces the
    committed balance. The same statement keeps feeding YTD income through the
    brokerage flow; nothing here writes YTD.

    An account already in the snapshot under a different name (a FinExtract sync
    labels it its own way) can be chosen as the one the statement replaces;
    the value is then written into it, keeping its name, and the choice is
    remembered. Unselected same-type accounts are listed as counted
    separately. Whose IRA it is comes from the learned holder-name map,
    else this instance's owner; the radio lets the user correct it, and a
    holder name not already mapped to the chosen owner is learned on Apply.
    """
    pending: list[BrokerageStatementRecord] = list(st.session_state.get("ira_statements") or [])
    st.markdown("##### IRA statements")
    snap: PortfolioSnapshot = (
        st.session_state.get("portfolio_snapshot") or load_snapshot() or PortfolioSnapshot()
    )
    link_map = load_link_map()
    for i, (rec, qid) in enumerate(zip(pending, _queue_ids(pending), strict=True)):
        label = describe_ira_record(rec)
        st.write(
            md_dollars(
                f"**{label}** — value {fmt_dollars(rec.ending_value or 0.0)} "
                f"as of {rec.statement_period_end}"
            )
        )

        options = ["you", "spouse"]
        default = default_ira_owner(rec, owner_map, instance_owner)
        owner = st.radio(
            "Whose IRA is this?",
            options,
            index=options.index(default),
            key=f"ira_statement_owner_{qid}",
            horizontal=True,
        )
        new_acct = to_ira_account_summary(rec, owner)
        link_key, others = _render_account_link_choice(rec, snap, owner, link_map, qid)
        other_owner = "spouse" if owner == "you" else "you"
        stale_key = (new_acct.account_type, other_owner, new_acct.account_name)
        replaced = [
            a
            for a in snap.accounts
            if account_key(a) in (account_key(new_acct), link_key, stale_key)
        ]
        if replaced:
            st.caption(
                md_dollars(
                    "Replaces: "
                    + "; ".join(
                        f"{a.account_name} {fmt_dollars(a.total_value)} ({as_of_label(a)})"
                        for a in replaced
                    )
                )
            )
        else:
            st.caption("Adds this account — it is not on file for this person yet.")
        if others:
            st.caption(
                md_dollars(
                    "Also on file (not replaced, counted separately): "
                    + "; ".join(
                        f"{a.account_name or 'unnamed account'} {fmt_dollars(a.total_value)}"
                        for a in others
                    )
                )
            )

        preview = apply_ira_statement(snap, rec, owner, replaces=link_key)
        if preview.kept_newer is not None:
            st.warning(
                f"{label} ({owner}) kept: cache is {as_of_label(preview.kept_newer)}, newer than "
                f"this statement (as of {rec.statement_period_end})."
            )
        apply_label = "Use anyway" if preview.kept_newer is not None else "Apply IRA balance"
        if st.button(apply_label, key=f"ira_statement_apply_{i}", disabled=not identity_set):
            outcome = apply_ira_statement(snap, rec, owner, force=True, replaces=link_key)
            # One transaction (spec §2): the snapshot, the remembered link, the
            # balance candidate(s) and the learned holder name are saved together
            # or not at all -- a later failure no longer leaves the link map written.
            with store_transaction(st.session_state, "portfolio_snapshot"):
                if not _save_snapshot_or_report(outcome.snapshot):
                    continue
                save_link_map(remember_link(rec, link_key or NEW, link_map))
                st.session_state["portfolio_snapshot"] = outcome.snapshot
                record = (
                    record_roth_balance_candidate
                    if rec.account_type == "roth_ira"
                    else record_ira_balance_candidate
                )
                if outcome.other_owner_total is not None:
                    # The owner was corrected: the wrong-owner copy is gone, so
                    # that person's candidate is re-recorded at their corrected total.
                    other_field, other_total = outcome.other_owner_total
                    record(other_field, other_total, outcome.detail, datetime.now())
                record(outcome.field_key, outcome.total, outcome.detail, datetime.now())
                if rec.owner_key and resolve_owner(rec.owner_key, owner_map) != owner:
                    save_owner_map(learn_owner(rec.owner_key, owner, owner_map))
            st.session_state["ira_statements"] = [r for r in pending if r is not rec]
            st.rerun()
        if st.button("Dismiss", key=f"ira_statement_dismiss_{i}"):
            st.session_state["ira_statements"] = [r for r in pending if r is not rec]
            st.rerun()

    if not identity_set:
        st.caption(
            "Applying is unavailable until this planner instance has an "
            "owner — set it in **Review Changes**."
        )


def _render_ssa_review(
    *, owner_map: dict[str, str], instance_owner: str, identity_set: bool
) -> None:
    """Review + apply each queued SSA statement.

    Applying records the statement's full-retirement-age estimate as a
    ``Source.PDF`` ``your_ss_fra``/``spouse_ss_fra`` candidate -- the
    resolver ladder ranks it above the FinExtract-synced estimate, and the
    Command Center's pending-review gate decides whether it replaces the
    committed value. The per-age estimates are saved for the comparison shown
    under the SS-at-FRA field; they never drive the model. Nothing here writes
    YTD income.

    Whose statement it is comes from the learned holder-name map, else this
    instance's owner; the radio lets the user correct it, and a holder name
    not already mapped to the chosen owner is learned on Apply.
    """
    pending: list[SsaStatement] = list(st.session_state.get("ssa_statements") or [])
    st.markdown("##### Social Security statements")
    for i, (stmt, qid) in enumerate(zip(pending, _queue_ids(pending), strict=True)):
        st.write(
            md_dollars(
                f"**Social Security statement** dated {stmt.statement_date} — "
                f"{fmt_dollars(stmt.fra_monthly)}/mo at full retirement age {stmt.fra_age}"
            )
        )
        notice = statement_age_notice(stmt, today=datetime.now().date())
        if notice:
            st.warning(notice)
        st.caption(
            "SSA's estimates assume you keep earning until you claim; the planner "
            "derives other claim ages from the full-retirement-age amount. "
            "Shown for comparison only."
        )
        st.dataframe(
            ssa_comparison_table(
                benefit_comparison(
                    stmt.estimates, fra_monthly=stmt.fra_monthly, fra_age=stmt.fra_age
                )
            ),
            hide_index=True,
        )

        options = ["you", "spouse"]
        default = default_ssa_owner(stmt, owner_map, instance_owner)
        owner = st.radio(
            "Whose statement is this?",
            options,
            index=options.index(default),
            key=f"ssa_owner_{qid}",
            horizontal=True,
        )

        if st.button(
            "Apply Social Security estimate",
            key=f"ssa_apply_{i}",
            disabled=not identity_set,
        ):
            # One transaction (spec §2): the candidate, the saved estimates and
            # the learned holder name are saved together or not at all.
            with store_transaction(st.session_state):
                record_ss_fra_candidate(
                    "your_ss_fra" if owner == "you" else "spouse_ss_fra",
                    stmt.fra_monthly,
                    Source.PDF,
                    f"SSA statement PDF, dated {stmt.statement_date}",
                    datetime.now(),
                )
                save_ssa_pdf_estimates(owner, stmt)
                if stmt.owner_key and resolve_owner(stmt.owner_key, owner_map) != owner:
                    save_owner_map(learn_owner(stmt.owner_key, owner, owner_map))
            st.session_state["ssa_statements"] = [s for s in pending if s is not stmt]
            st.rerun()
        if st.button("Dismiss", key=f"ssa_dismiss_{i}"):
            st.session_state["ssa_statements"] = [s for s in pending if s is not stmt]
            st.rerun()

    if not identity_set:
        st.caption(
            "Applying is unavailable until this planner instance has an "
            "owner — set it in **Review Changes**."
        )


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
        notes = []
        if "statement_period_end" in rec.missing_fields:
            notes.append(
                "Without a statement period this record cannot be ordered against "
                "other statements for the same account, so a newer one will not "
                "supersede it automatically."
            )
        if rec.broker == "fidelity" and "ltcg_net_ytd" in rec.missing_fields:
            notes.append(
                "Realized gains are not read from Fidelity statements; enter them manually."
            )
        off_year = off_year_statement_note(
            rec,
            _brokerage_owner(rec, account_overrides, instance_owner, owner_map),
            st.session_state.get("ytd_snapshot", YTDSnapshot()).tax_year,
        )
        if off_year:
            notes.append(off_year)
        parsed = [
            f"dividends ${rec.dividends_taxable_ytd:,.2f}",
            f"interest ${rec.interest_taxable_ytd:,.2f}",
        ]
        if "stcg_net_ytd" not in rec.missing_fields:
            parsed.append(f"STCG ${rec.stcg_net_ytd:,.2f}")
        if "ltcg_net_ytd" not in rec.missing_fields:
            parsed.append(f"LTCG ${rec.ltcg_net_ytd:,.2f}")
        st.info(
            md_dollars(
                f"**{rec.broker} {account_number}** parsed, but incomplete — "
                f"could not read: {gaps}. "
                f"Figures that DID parse: {', '.join(parsed)}. " + " ".join(notes)
            )
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
                save_account_type_override(rec.account_number, choice, broker=rec.broker)
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
        # Only statements for the snapshot's tax year count; the rest stay in
        # the ledger for their own year and are explained, never listed as
        # counted (issue #12).
        snap_year = st.session_state.get("ytd_snapshot", YTDSnapshot()).tax_year
        counted = [
            key for key, rec in stmt_taxable.items() if statement_in_tax_year(rec, snap_year)
        ]
        if counted:
            st.caption(f"Counted toward YTD income: {', '.join(counted)}")
        for rec in stmt_taxable.values():
            note = off_year_statement_note(
                rec,
                _brokerage_owner(rec, account_overrides, instance_owner, owner_map),
                snap_year,
            )
            if note:
                st.info(note)
        if not identity_set:
            st.caption(
                "Applying is unavailable until this planner instance has an "
                "owner — set it in **Review Changes**."
            )
        if is_pyodide():
            st.caption(
                "⚠️ This browser session's data is lost on reload — use "
                "**Data › Backup & Transfer ▸ 📦 Export my data** to keep it."
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
            # One transaction (spec §2): the ledger and the YTD snapshot are saved
            # together or not at all.
            with store_transaction(st.session_state, "ytd_snapshot", "holder_name_mismatches"):
                for rec in stmt_taxable.values():
                    resolved = _brokerage_owner(rec, account_overrides, instance_owner, owner_map)
                    if _warn_on_brokerage_holder_mismatch(
                        rec, account_overrides, instance_owner, owner_map
                    ):
                        _apply_had_mismatch = True
                    ledger = write_brokerage_contribution(ledger, resolved, rec)
                save_ledger(ledger)

                prev_ytd = st.session_state.get("ytd_snapshot", YTDSnapshot())
                brokerage_totals = derive_brokerage_totals(ledger, tax_year=prev_ytd.tax_year)
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
            st.success(
                f"Applied {len(counted)} taxable account(s) to YTD snapshot"
                + (
                    f" ({len(stmt_taxable) - len(counted)} from another tax year not counted)"
                    if len(counted) < len(stmt_taxable)
                    else ""
                )
            )
            if _manual_entry_kept_explicit:
                st.caption(
                    "Manual entry stayed ON — you turned it on yourself, so this "
                    "apply did not switch you back to synced-data display."
                )
            for warning in ubs_warnings:
                st.warning(warning)
            if ubs_warnings:
                _apply_had_mismatch = True
            # WARN, NEVER BLOCK: the write(s) above already completed --
            # this only withholds the immediate rerun so BOTH the "Applied"
            # confirmation just emitted above AND the freshly-recorded
            # mismatch row survive to be seen, instead of being wiped by a
            # same-render st.rerun() before either ever reaches the browser
            # (see _apply_ubs_correction's docstring for the UBS half of
            # this). The mismatch row itself is drawn into the reserved
            # panel slot as the LAST statement of render_sync_scan_results
            # (see that function's docstring) -- it is visible on THIS
            # render precisely because the rerun below did not fire.
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
                    md_dollars(
                        f"{owner.title()}: STCG {fmt_dollars(figures['stcg'])}, "
                        f"LTCG {fmt_dollars(figures['ltcg'])}, "
                        f"Income {fmt_dollars(figures['income'])}"
                    )
                )

    if ledger.get("brokerage"):
        tax_year = st.session_state.get("ytd_snapshot", YTDSnapshot()).tax_year
        with st.expander(f"Per-owner brokerage breakdown ({tax_year})"):
            for owner, accounts in sorted(ledger["brokerage"].items()):
                totals = derive_brokerage_totals(
                    {"koinly": {}, "brokerage": {owner: accounts}}, tax_year=tax_year
                )
                st.caption(
                    md_dollars(
                        f"{owner.title()} ({len(accounts)} account(s)): "
                        f"Interest {fmt_dollars(totals['interest_ytd'])}, "
                        f"Dividends {fmt_dollars(totals['ordinary_dividends_ytd'])}, "
                        f"STCG {fmt_dollars(totals['stcg_ytd'])}, "
                        f"LTCG {fmt_dollars(totals['ltcg_ytd'])}"
                    )
                )
