"""YTD Income Tracker & Conversion Headroom Calculator.

Shows real-world mid-year income events (stop-loss triggers, wages, etc.)
and computes remaining headroom for Roth conversions against bracket,
IRMAA, NIIT, and ACA thresholds.

Key insight: LTCG consumes IRMAA/NIIT room but NOT ordinary bracket room.
"""

from datetime import date as _date
from pathlib import Path

import pandas as pd
import streamlit as st

from engine.data_bridge_browser import is_pyodide
from engine.headroom import compute_headroom
from engine.ira import ss_benefit_at_age, ss_with_cola
from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE, _index_irmaa_tiers, irmaa_surcharge
from engine.niit import NIIT_RATE, NIIT_THRESHOLD_MFJ, NIIT_THRESHOLD_SINGLE
from engine.portfolio_sync import save_ytd_snapshot
from engine.tax import (
    LTCG_RATES_MFJ,
    SafeHarborGuidance,
    YTDTaxEstimate,
    estimate_ytd_federal_tax,
    load_prior_year_federal_tax,
    safe_harbor_payment,
)
from models.household import Household
from models.ytd_income import IncomeEvent, YTDSnapshot, sum_income_events
from views._format import fmt_dollars, fmt_dollars_short, fmt_pct


def _color_for_room(room: float) -> str:
    if room <= 0:
        return "inverse"  # red
    if room <= 50_000:
        return "off"  # orange-ish (streamlit uses "off" for warning-style)
    return "normal"  # green


def _metric_delta_color(room: float) -> str:
    if room <= 0:
        return "inverse"
    return "normal"


def render(hh: Household):
    st.title("YTD Income & Conversion Headroom")
    st.caption(
        "Track mid-year income events and see how much Roth conversion room remains. "
        "LTCG from stop-loss triggers consumes IRMAA room but leaves bracket room intact."
    )

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

            ytd_snap = fetch_ytd_snapshot()
            exercises = fetch_option_exercises()
            if exercises.server_available:
                ytd_snap = apply_option_exercises(ytd_snap, exercises, hh)
            if ytd_snap.snapshot_date:
                # wages_ytd, nec_income_ytd, qualified_dividends_ytd, ira_conversions_ytd,
                # spouse_ira_conversions_ytd, and ira_distributions_ytd are
                # manual-entry-only. interest_ytd, tax_exempt_interest_ytd,
                # ordinary_dividends_ytd, ltcg_ytd, stcg_ytd, and gain_events are now
                # sourced from brokerage statement PDFs (see the section below), not
                # FinExtract — fetch_ytd_snapshot no longer populates any of these.
                # Preserve whatever was already recorded instead of letting this
                # NQO-only sync silently zero them out.
                prev = st.session_state.get("ytd_snapshot")
                if prev is not None:
                    ytd_snap.wages_ytd = prev.wages_ytd
                    ytd_snap.nec_income_ytd = prev.nec_income_ytd
                    ytd_snap.qualified_dividends_ytd = prev.qualified_dividends_ytd
                    ytd_snap.ira_conversions_ytd = prev.ira_conversions_ytd
                    ytd_snap.spouse_ira_conversions_ytd = prev.spouse_ira_conversions_ytd
                    ytd_snap.ira_distributions_ytd = prev.ira_distributions_ytd
                    ytd_snap.interest_ytd = prev.interest_ytd
                    ytd_snap.tax_exempt_interest_ytd = prev.tax_exempt_interest_ytd
                    ytd_snap.ordinary_dividends_ytd = prev.ordinary_dividends_ytd
                    ytd_snap.ltcg_ytd = prev.ltcg_ytd
                    ytd_snap.stcg_ytd = prev.stcg_ytd
                    ytd_snap.gain_events = prev.gain_events
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

        st.markdown("##### Sync from Brokerage Statements (PDF)")
        st.caption(
            "Reads the latest monthly/quarterly statement per account for accurate YTD "
            "interest, dividends (with tax-exempt split), and realized gains. This is the "
            "authoritative source for these figures — FinExtract's live feed cannot "
            "distinguish tax-exempt income or separate taxable from Roth/IRA accounts."
        )
        from engine.brokerage_statement_pdf import (
            apply_account_type_overrides,
            load_account_type_overrides,
            load_statement_folder_path,
            partition_by_account_type,
            pick_latest_per_account,
            save_account_type_override,
            save_statement_folder_path,
            scan_statement_folder,
        )

        default_folder = load_statement_folder_path() or ""
        folder_input = st.text_input(
            "Statement folder",
            value=default_folder,
            key="statement_folder_path",
            help="Local folder containing Schwab/Vanguard statement PDFs.",
        )
        if st.button("Scan statement folder", key="scan_statements_btn"):
            # This is a local single-user desktop tool -- the account owner
            # supplies a folder path on their own trusted machine (e.g.
            # ~/Downloads or ~/Documents/Statements), so there is no attacker
            # controlling this input in this tool's threat model. The
            # mitigation is therefore normalization + directory-type
            # validation -- reject blank input, then resolve away ".." and
            # symlink traversal before the path is ever used in glob()/
            # read_bytes() -- not sandboxing against a hostile actor.
            if not folder_input or not folder_input.strip():
                st.error("Statement folder cannot be empty.")
                folder_path = None
            else:
                folder_path = Path(folder_input).expanduser().resolve()
            if folder_path is None:
                pass
            elif not folder_path.is_dir():
                st.error(f"Not a folder: {folder_path}")
            else:
                save_statement_folder_path(str(folder_path))
                records, errors = scan_statement_folder(folder_path)
                if errors:
                    st.warning(f"{len(errors)} file(s) could not be parsed: " + "; ".join(errors))
                by_account = pick_latest_per_account(records)
                overrides = load_account_type_overrides()
                by_account = apply_account_type_overrides(by_account, overrides)
                st.session_state["statement_by_account"] = by_account

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
                        st.rerun()

            if stmt_taxable:
                st.caption(f"Counted toward YTD income: {', '.join(stmt_taxable.keys())}")
                if st.button("Apply to YTD snapshot", key="apply_statements_btn"):
                    from engine.portfolio_sync.ytd import apply_brokerage_statement_records

                    prev_ytd = st.session_state.get("ytd_snapshot", YTDSnapshot())
                    updated_ytd = apply_brokerage_statement_records(prev_ytd, stmt_taxable)
                    updated_ytd.with_snapshot_date()
                    st.session_state.ytd_snapshot = updated_ytd
                    st.session_state["ytd_manual_entry"] = False
                    save_ytd_snapshot(updated_ytd)
                    st.success(f"Applied {len(stmt_taxable)} taxable account(s) to YTD snapshot")
                    st.rerun()

    manual = st.checkbox(
        "Manual entry",
        value=st.session_state.get("ytd_manual_entry", True),
        key="ytd_manual_entry",
    )

    # Get existing snapshot or create empty
    ytd: YTDSnapshot = st.session_state.get("ytd_snapshot", YTDSnapshot())

    if manual:
        col1, col2, col3 = st.columns(3)
        with col1:
            wages = st.number_input(
                "Wages YTD",
                value=int(ytd.wages_ytd),
                step=5_000,
                format="%d",
            )
            nec_income = st.number_input(
                "1099-NEC / Self-Employment Income YTD",
                value=int(ytd.nec_income_ytd),
                step=1_000,
                format="%d",
                help="Self-employment or contractor income year-to-date.",
            )
            ltcg = st.number_input(
                "Long-Term Capital Gains YTD",
                value=int(ytd.ltcg_ytd) if ytd.ltcg_ytd > 0 else 0,
                step=10_000,
                format="%d",
                help="From stop-loss triggers, mutual fund distributions, etc.",
            )
        with col2:
            stcg = st.number_input(
                "Short-Term Capital Gains YTD",
                value=int(ytd.stcg_ytd),
                step=5_000,
                format="%d",
            )
            div_col1, div_col2 = st.columns(2)
            with div_col1:
                qualified_dividends = st.number_input(
                    "Qualified dividends YTD",
                    value=int(ytd.qualified_dividends_ytd),
                    step=500,
                    format="%d",
                    help=f"Taxed at LTCG rates ({fmt_pct(LTCG_RATES_MFJ[0], 0)}/{fmt_pct(LTCG_RATES_MFJ[1], 0)}/{fmt_pct(LTCG_RATES_MFJ[2], 0)}); counts toward MAGI but not ordinary brackets.",
                )
            with div_col2:
                ordinary_dividends = st.number_input(
                    "Ordinary dividends YTD",
                    value=int(ytd.ordinary_dividends_ytd),
                    step=500,
                    format="%d",
                    help="Taxed as ordinary income; stacks into brackets and SS taxation.",
                )
        with col3:
            interest = st.number_input(
                "Interest YTD",
                value=int(ytd.interest_ytd),
                step=1_000,
                format="%d",
            )
            tax_exempt_interest = st.number_input(
                "Tax-exempt (muni) interest YTD",
                value=int(ytd.tax_exempt_interest_ytd),
                step=1_000,
                format="%d",
                help="Muni bond interest — counts toward MAGI/IRMAA and SS provisional income, not ordinary brackets.",
                key="ytd_tax_exempt_interest",
            )
            federal_withholding = st.number_input(
                "Federal Tax Withheld YTD",
                value=int(ytd.federal_withholding_ytd),
                step=1_000,
                format="%d",
                help="W-2 federal income tax withheld year-to-date; counts as 'Already paid' toward safe-harbor.",
            )

        st.markdown("##### Roth Conversions & IRA Distributions")
        st.caption(
            "Log each conversion or distribution as you execute it — custodian statements "
            "lag, so this is the most accurate running total."
        )

        income_events = st.session_state.get("income_events", list(ytd.income_events))

        with st.form("add_income_event", clear_on_submit=True):
            ie_col1, ie_col2, ie_col3, ie_col4 = st.columns(4)
            with ie_col1:
                ie_date = st.date_input("Date")
            with ie_col2:
                ie_kind = st.selectbox("Type", ["Conversion", "Distribution"])
            with ie_col3:
                owner_options = ["You"] if hh.filing_status == "Single" else ["You", "Spouse"]
                ie_owner = st.selectbox("Whose", owner_options)
            with ie_col4:
                ie_amount = st.number_input("Amount", min_value=0, step=1_000, format="%d")
            if st.form_submit_button("Add entry") and ie_amount > 0:
                income_events.append(
                    IncomeEvent(
                        date=ie_date.isoformat(),
                        amount=float(ie_amount),
                        kind=ie_kind.lower(),
                        owner=ie_owner.lower(),
                    )
                )
                st.session_state["income_events"] = income_events

        if income_events:
            ie_rows = [
                {
                    "Date": e.date,
                    "Type": e.kind.title(),
                    "Whose": e.owner.title(),
                    "Amount": fmt_dollars(e.amount),
                }
                for e in income_events
            ]
            st.dataframe(pd.DataFrame(ie_rows), width="stretch")
            del_idx = st.selectbox(
                "Remove an entry",
                options=list(range(len(income_events))),
                format_func=lambda i: (
                    f"{income_events[i].date} — {income_events[i].kind.title()} — "
                    f"{fmt_dollars(income_events[i].amount)}"
                ),
                index=None,
                placeholder="Select an entry to remove",
                key="income_event_delete_select",
            )
            if del_idx is not None and st.button("Remove selected entry"):
                income_events.pop(del_idx)
                st.session_state["income_events"] = income_events
                st.rerun()

        conversions_done = sum_income_events(income_events, kind="conversion", owner="you")
        spouse_conversions_done = sum_income_events(income_events, kind="conversion", owner="spouse")
        distributions_done = sum_income_events(income_events, kind="distribution")

        ytd = YTDSnapshot(
            tax_year=hh.base_year,
            wages_ytd=float(wages),
            nec_income_ytd=float(nec_income),
            ltcg_ytd=float(ltcg),
            stcg_ytd=float(stcg),
            qualified_dividends_ytd=float(qualified_dividends),
            ordinary_dividends_ytd=float(ordinary_dividends),
            interest_ytd=float(interest),
            tax_exempt_interest_ytd=float(tax_exempt_interest),
            ira_conversions_ytd=conversions_done,
            spouse_ira_conversions_ytd=spouse_conversions_done,
            ira_distributions_ytd=distributions_done,
            income_events=income_events,
            federal_withholding_ytd=float(federal_withholding),
            gain_events=ytd.gain_events,
            manually_entered=True,
        ).with_snapshot_date()

        st.session_state.ytd_snapshot = ytd

    # Gain events drill-down
    if ytd.gain_events:
        with st.expander(f"Realized Gain Events ({len(ytd.gain_events)})"):
            events_data = []
            for e in ytd.gain_events:
                events_data.append(
                    {
                        "Date": e.date,
                        "Description": e.description,
                        "Account": e.account_name,
                        "Proceeds": fmt_dollars(e.proceeds),
                        "Basis": fmt_dollars(e.cost_basis),
                        "Gain/Loss": fmt_dollars(e.gain_loss),
                        "Type": "LTCG" if e.is_ltcg else "STCG",
                    }
                )
            st.dataframe(pd.DataFrame(events_data), width="stretch")

    # --- Section 2: Conversion Headroom ---
    st.markdown("---")
    st.markdown("### Conversion Headroom")

    headroom = compute_headroom(hh, ytd, filing_status=hh.filing_status)

    # Summary metrics
    st.markdown("#### Current YTD Position (Locked In)")
    m1, m2, m3 = st.columns(3)
    m1.metric("Locked MAGI (YTD actuals)", fmt_dollars(headroom.locked_magi))
    m2.metric("of which LTCG", fmt_dollars(headroom.ytd_ltcg))
    m3.metric("Conversions Done", fmt_dollars(headroom.conversions_done))

    # Surface dividend/interest impact on conversion headroom (PR #95).
    # Qualified divs hit MAGI only (IRMAA/NIIT/ACA); ordinary divs + interest
    # hit BOTH ordinary brackets AND MAGI.
    if ytd.qualified_dividends_ytd or ytd.ordinary_dividends_ytd or ytd.interest_ytd:
        st.caption("Investment income impacting headroom")
        dq, do, di = st.columns(3)
        dq.metric(
            "Qualified dividends (YTD)",
            fmt_dollars(ytd.qualified_dividends_ytd),
            help="LTCG-rate taxed. Reduces MAGI room (IRMAA / NIIT / ACA) but NOT ordinary-bracket conversion room.",
        )
        do.metric(
            "Ordinary dividends (YTD)",
            fmt_dollars(ytd.ordinary_dividends_ytd),
            help="Ordinary-rate taxed. Reduces BOTH ordinary-bracket AND MAGI conversion room.",
        )
        di.metric(
            "Interest (YTD)",
            fmt_dollars(ytd.interest_ytd),
            help="Ordinary-rate taxed. Reduces BOTH ordinary-bracket AND MAGI conversion room.",
        )

    # NQO exercises YTD (FinExtract sync, PR3 of finextract-nqo-exercises)
    if ytd.nqo_exercise_ytd or getattr(ytd, "_option_exercises_by_grant", None):
        st.metric(
            "NQO exercises (YTD)",
            fmt_dollars(ytd.nqo_exercise_ytd),
            help=(
                "Realized NQO ordinary-income spread from FinExtract equity_compensation. "
                "Subtracted from planned option income in the conversion-room calc."
            ),
        )
        captured = st.session_state.get("exercises_captured_at", "")
        if captured:
            st.caption(f"Exercises last captured: {captured}")
        by_grant: dict[str, float] = getattr(ytd, "_option_exercises_by_grant", {}) or {}
        if by_grant:
            with st.expander("Per-grant breakdown"):
                rows = []
                # Build lookup: grant_id -> StockGrant
                grants_by_id = {
                    g.grant_id: g for g in (hh.grants or []) if getattr(g, "grant_id", "")
                }
                sale_info_map: dict = getattr(ytd, "_option_exercises_sale_info", {}) or {}
                for grant_id, spread in by_grant.items():
                    g = grants_by_id.get(grant_id)
                    if g:
                        rows.append(
                            {
                                "Grant #": grant_id,
                                "YTD spread": fmt_dollars(spread),
                                "Year": str(g.year),
                                "Strike": fmt_dollars(g.strike, decimals=2),
                                "Shares": str(g.shares),
                                "Expiry": str(g.expiry_year),
                            }
                        )
                    else:
                        sale_info = sale_info_map.get(grant_id, {})
                        rows.append(
                            {
                                "Grant #": grant_id,
                                "YTD spread": fmt_dollars(spread),
                                "Year": str(sale_info.get("grant_year") or "—"),
                                "Strike": fmt_dollars(sale_info["strike"], decimals=2)
                                if sale_info.get("strike")
                                else "—",
                                "Shares": str(sale_info.get("shares_ytd") or "—"),
                                "Expiry": "—",
                            }
                        )
                st.dataframe(rows, width="stretch", hide_index=True)
                unmatched = sum(1 for r in rows if r["Expiry"] == "—")
                if unmatched:
                    st.caption(
                        f"⚠️ {unmatched} grant(s) shown from sale data only; not joined to household "
                        "StockGrant (check .user_defaults.json grant strikes)."
                    )

    if headroom.planned_option_income > 0:
        st.caption(
            f"Option exercise ({hh.base_year}): **{fmt_dollars(headroom.planned_option_income)}** — "
            "this is a choice, not locked in. Headroom shown below excludes it."
        )
        if headroom.realized_option_income_ytd:
            st.caption(
                f"Planned reflects {fmt_dollars(headroom.realized_option_income_ytd)} already realized YTD "
                f"(of {fmt_dollars(headroom.planned_option_income + headroom.realized_option_income_ytd)} "
                "originally planned)."
            )

    # --- Section A: Realized Capital Gains ---
    st.markdown("---")
    st.subheader("Realized Capital Gains (YTD)")
    if not ytd.gain_events:
        st.caption("No realized gains synced yet. Sync from FinExtract to populate.")
    else:
        cg1, cg2 = st.columns(2)
        cg1.metric(
            "Long-term gains",
            fmt_dollars(ytd.ltcg_ytd),
            help="Preferential rate (0/15/20%)",
        )
        cg2.metric(
            "Short-term gains",
            fmt_dollars(ytd.stcg_ytd),
            help="Ordinary-income rate; stacks into brackets",
        )
        by_source: dict[str, dict[str, float]] = {}
        for e in ytd.gain_events:
            src = e.account_name or "unknown"
            by_source.setdefault(src, {"long": 0.0, "short": 0.0})
            if e.is_ltcg:
                by_source[src]["long"] += e.gain_loss
            else:
                by_source[src]["short"] += e.gain_loss
        if by_source:
            with st.expander("Breakdown by source"):
                gain_rows = [
                    {
                        "Source": str(src),
                        "Long-term": fmt_dollars(v["long"]),
                        "Short-term": fmt_dollars(v["short"]),
                    }
                    for src, v in sorted(by_source.items())
                ]
                st.dataframe(gain_rows, width="stretch", hide_index=True)

    # --- Section B: Tax Bracket Position ---
    # --- Section C: Estimated YTD Federal Tax ---
    # F20: pass combined annual SS so taxable_ss() applies IRC §86 cap.
    # Apply COLA growth for years already collecting so the YTD estimate reflects
    # the actual current-year benefit, not the bare at-claim-age amount.
    _your_ss = (
        ss_with_cola(
            ss_benefit_at_age(hh.your_ss_fra, min(hh.your_ss_start_age, 70), hh.your_fra_age),
            hh.your_age - hh.your_ss_start_age,
            hh.ss_cola,
        )
        if hh.your_age >= hh.your_ss_start_age
        else 0.0
    )
    _spouse_ss = (
        ss_with_cola(
            ss_benefit_at_age(hh.spouse_ss_fra, min(hh.spouse_ss_start_age, 70), hh.spouse_fra_age),
            hh.spouse_age - hh.spouse_ss_start_age,
            hh.ss_cola,
        )
        if hh.spouse_age >= hh.spouse_ss_start_age
        else 0.0
    )
    _combined_ss = _your_ss + _spouse_ss
    estimate: YTDTaxEstimate = estimate_ytd_federal_tax(ytd, hh, combined_ss=_combined_ss)

    st.markdown("---")
    st.subheader("Tax Bracket Position")
    b1, b2, b3 = st.columns(3)
    b1.metric(
        "Current bracket",
        fmt_pct(estimate.marginal_bracket_pct, 0),
        help=f"Marginal {hh.filing_status} tax bracket your next dollar of ordinary income falls into.",
    )
    b2.metric(
        "Room to next bracket",
        fmt_dollars(estimate.room_to_next_bracket),
        help="Additional ordinary income before pushing into the next bracket.",
    )
    b3.metric(
        "Effective rate (so far)",
        fmt_pct(estimate.effective_rate),
        help=(
            "Estimated total tax divided by MAGI. "
            "Lower than marginal because preferential LTCG rate is averaged in."
        ),
    )

    st.markdown("---")
    st.subheader("Estimated YTD Federal Tax")
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Ordinary bracket tax", fmt_dollars(estimate.ordinary_tax))
    t2.metric("LTCG / qualified div tax", fmt_dollars(estimate.ltcg_tax))
    t3.metric(f"NIIT ({fmt_pct(NIIT_RATE)})", fmt_dollars(estimate.niit))
    t4.metric("Total federal", fmt_dollars(estimate.total))
    st.caption(
        "Estimate assumes today were Dec 31 (current YTD income only — not annualized). "
        "Standard deduction and OBBBA senior bonus are applied. "
        "Excludes state tax, IRMAA, and quarterly underpayment penalties."
    )

    # --- Section D: Mid-Year Safe-Harbor Payment ---
    st.markdown("---")
    st.subheader("Mid-Year Safe-Harbor Payment Guidance")
    prior_year_tax = load_prior_year_federal_tax()
    already_paid = float(ytd.federal_withholding_ytd)
    # Prior-year AGI governs the 100% vs 110% safe-harbor rule (§6654). Use the household's cached
    # prior-year MAGI as the AGI proxy; None when unknown → the engine assumes 110% and labels it.
    prior_year = _date.today().year - 1
    prior_year_agi = hh.prior_year_magi.get(prior_year)
    guidance: SafeHarborGuidance = safe_harbor_payment(
        prior_year_tax=prior_year_tax,
        current_year_estimate=estimate.total,
        already_paid_ytd=already_paid,
        payment_date=_date.today().isoformat(),
        prior_year_agi=prior_year_agi,
        filing_status=hh.filing_status,
    )
    if prior_year_tax == 0:
        st.warning(
            "Prior year tax unknown — only current-year estimate path active. "
            "Upload your prior year 1040 PDF in ⚙️ Setup → 📊 Parameters → Joint to unlock "
            "the 110% safe-harbor rule."
        )
    g1, g2, g3 = st.columns(3)
    g1.metric(
        "Safe-harbor target",
        fmt_dollars(guidance.safe_harbor_target),
        help=guidance.rule_used,
    )
    g2.metric("Already paid YTD", fmt_dollars(guidance.already_paid_ytd))
    g3.metric(
        f"Remaining to pay by {guidance.next_quarterly_due}",
        fmt_dollars(guidance.remaining_to_pay),
        help="Pay this before the next quarterly deadline to maintain safe-harbor protection.",
    )

    st.markdown("#### Room for Conversions (from locked income only)")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Room to 12%",
        fmt_dollars(headroom.room_to_12pct),
        help="Ordinary bracket room — LTCG does NOT consume this",
    )
    c2.metric(
        "Room to 22%",
        fmt_dollars(headroom.room_to_22pct),
        help="Ordinary bracket room — LTCG does NOT consume this",
    )

    # Prior-year MAGI anchor for IRMAA 2-year lookback
    prior_magi = st.session_state.get("prior_year_magi") or {}
    if prior_magi:
        sorted_years = sorted(prior_magi.keys(), reverse=True)
        most_recent = sorted_years[0]
        st.caption(
            f"Prior-year MAGI anchor ({most_recent}): {fmt_dollars(prior_magi[most_recent])}"
            " — used for IRMAA 2-year lookback"
        )

    # IRMAA — show room but note if not yet relevant
    irmaa_label = "Room to IRMAA"
    if not headroom.irmaa_relevant:
        irmaa_label += f" (matters from {headroom.irmaa_first_relevant_year})"
    c3.metric(
        irmaa_label,
        fmt_dollars(headroom.room_to_irmaa_t1),
        delta="TRIGGERED" if headroom.irmaa_already_triggered else None,
        delta_color="inverse" if headroom.irmaa_already_triggered else "off",
        help="MAGI-based — LTCG DOES consume this. "
        + (
            f"Not relevant until {headroom.irmaa_first_relevant_year} income year "
            f"(Medicare starts at 65, 2-year lookback)."
            if not headroom.irmaa_relevant
            else ""
        ),
    )
    _niit_thr = NIIT_THRESHOLD_SINGLE if hh.filing_status == "Single" else NIIT_THRESHOLD_MFJ
    c4.metric(
        "Room to NIIT",
        fmt_dollars(headroom.room_to_niit),
        help=f"MAGI-based ({fmt_dollars_short(_niit_thr, decimals=0, suffix='K')}) — LTCG DOES consume this",
    )

    if not headroom.irmaa_relevant:
        st.info(
            f"**IRMAA does not apply to {hh.base_year} income.** "
            f"You are {hh.your_age} — Medicare starts at 65 with a 2-year lookback. "
            f"IRMAA first matters for income year **{headroom.irmaa_first_relevant_year}** "
            f"(age {hh.your_age + headroom.irmaa_first_relevant_year - hh.base_year})."
        )

    # Show with-planned comparison if there's option income
    if headroom.planned_option_income > 0:
        with st.expander("If you also exercise options this year"):
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Room to 12%", fmt_dollars(headroom.room_to_12pct_with_planned))
            p2.metric("Room to 22%", fmt_dollars(headroom.room_to_22pct_with_planned))
            p3.metric("Room to IRMAA", fmt_dollars(headroom.room_to_irmaa_t1_with_planned))
            p4.metric("Room to NIIT", fmt_dollars(headroom.room_to_niit_with_planned))

    # Visual explanation
    st.info(
        f"**Why bracket room differs from IRMAA/NIIT room**: Long-term capital gains are taxed at "
        f"preferential rates ({fmt_pct(LTCG_RATES_MFJ[1], 0)}) and do NOT stack into ordinary brackets. But they DO count "
        f"toward MAGI for IRMAA surcharges and NIIT. So $200K in LTCG can consume IRMAA/NIIT "
        f"room while leaving your 12%/22% bracket room completely untouched."
    )

    # --- Section 3: IRMAA Impact Warning ---
    if headroom.irmaa_already_triggered:
        st.markdown("---")
        st.markdown("### IRMAA Impact Warning")
        st.error(
            f"**IRMAA Tier {headroom.irmaa_tier_current} already triggered** "
            f"with projected MAGI of {fmt_dollars(headroom.projected_magi_base)}.\n\n"
            f"This means 2-year lookback will affect **{hh.base_year + 2} Medicare premiums**."
        )

        # IRMAA 2-year lookback: the surcharge shown is what will be charged in
        # base_year + 2 Medicare premiums, so index the MAGI thresholds to the
        # payment year (base_year + 2), not the income year.
        surcharge_1p = irmaa_surcharge(
            headroom.projected_magi_base,
            1,
            filing_status=hh.filing_status,
            year=hh.base_year + 2,
            cpi=hh.cpi_assumption,
        )
        surcharge_2p = irmaa_surcharge(
            headroom.projected_magi_base,
            2,
            filing_status=hh.filing_status,
            year=hh.base_year + 2,
            cpi=hh.cpi_assumption,
        )

        s1, s2 = st.columns(2)
        s1.metric(
            "Annual Surcharge (1 person on Medicare)",
            fmt_dollars(surcharge_1p),
        )
        s2.metric(
            "Annual Surcharge (2 people on Medicare)",
            fmt_dollars(surcharge_2p),
        )

        # Tier table
        with st.expander("IRMAA Tier Details"):
            _base_tiers = IRMAA_TIERS_SINGLE if hh.filing_status == "Single" else IRMAA_TIERS_MFJ
            _irmaa_tiers = _index_irmaa_tiers(
                _base_tiers, year=hh.base_year + 2, cpi=hh.cpi_assumption
            )
            tier_data = []
            for i, (threshold, part_b, part_d) in enumerate(_irmaa_tiers, 1):
                tier_data.append(
                    {
                        "Tier": i,
                        "MAGI Threshold": fmt_dollars(threshold),
                        "Part B (annual/person)": fmt_dollars(part_b),
                        "Part D Surcharge (annual/person)": fmt_dollars(part_d),
                    }
                )
            st.dataframe(pd.DataFrame(tier_data), width="stretch")

    # --- Section 4: Integration Toggle ---
    st.markdown("---")
    st.markdown("### Integration with Conversion Planner")

    apply_ytd = st.checkbox(
        f"Apply YTD actuals to {hh.base_year} projection",
        value=st.session_state.get("apply_ytd_to_projection", False),
        help="When enabled, the Conversion Planner page will use these YTD numbers "
        "for the base year instead of projecting from zero.",
    )
    st.session_state.apply_ytd_to_projection = apply_ytd

    if apply_ytd:
        st.success(
            f"YTD data will be used in the Conversion Planner. "
            f"Switch to that page to see the updated {hh.base_year} row."
        )
    else:
        st.info(
            "YTD data is NOT being applied to the Conversion Planner. Toggle above to integrate."
        )

    # Save snapshot for persistence
    save_ytd_snapshot(ytd)
