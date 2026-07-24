"""Parameters tab — Me/Spouse/Joint sub-tabs (survivor, inherited IRAs, PDF 1040 import, MAGI anchor, filing-status pickers)."""

from __future__ import annotations

from dataclasses import replace
from typing import TypeVar

import streamlit as st

from config.loader import save_user_defaults
from engine.data_bridge_browser import (
    is_pyodide,
)
from engine.irmaa import BASE_PART_B
from engine.tax_return_pdf import (
    Form1040Record,
    load_pdf_tax_records,
    save_pdf_tax_records,
)
from models.household import Household
from views._format import fmt_dollars
from views.setup._partials import render_accounts_partial, render_household_partial
from views.setup._state import _user_defaults_from_session

_Num = TypeVar("_Num", int, float)


def _clamp(value: _Num, lo: _Num, hi: _Num) -> _Num:
    """Clamp ``value`` into ``[lo, hi]``.

    Cached/uploaded JSON (.user_defaults.json, .tax_pdf_cache.json) can seed a
    widget ``value`` outside its ``[min_value, max_value]`` bounds, and Streamlit
    raises ``StreamlitAPIException`` at render time — crashing the Joint sub-tab on
    load with no user interaction (audit C4). The widget bounds are widened to
    generous limits so no legitimate value is ever out of range; this clamp is a
    final backstop so genuinely corrupt data still cannot crash the render.
    """
    return min(max(value, lo), hi)


def apply_single_filer(hh: Household) -> Household:
    """Return a copy of ``hh`` with spouse inputs zeroed when filing Single.

    Single models a single-from-the-start household. The zeroing is applied to the
    DERIVED Household (never to session_state) so toggling back to MFJ restores the
    user's real spouse balances (audit C9 / ui-streamlit-4). Session-state key
    ``spouse_aca`` maps to the Household field ``spouse_aca_enrolled``.
    """
    if hh.filing_status != "Single":
        return hh
    return replace(
        hh,
        spouse_ira=0,
        spouse_roth=0,
        spouse_age=0,
        spouse_ss_fra=0.0,
        spouse_aca_enrolled=False,
        spouse_has_workplace_plan=False,
    )


def _render_survivor_scenario(base_year: int) -> None:
    """Render the Survivor scenario expander in the Joint sub-tab."""
    current: dict = st.session_state.get("survivor") or {}

    with st.expander("Survivor scenario (advanced sensitivity)", expanded=False):
        st.caption(
            "Optional. Models death of one spouse mid-projection. "
            "Survivor switches to single-filer brackets, std deduction, and senior bonus "
            "starting death_year + 1. Deceased's IRA rolls to survivor (spousal rollover); "
            "deceased's SS ends. "
            "NOT YET MODELED: SS survivor benefit step-up; inherited-IRA stretch rules."
        )
        # Seed the Enable flag once from any persisted/uploaded survivor scenario. Do NOT
        # pass value= alongside the persistent key: Streamlit ignores value= once the key
        # exists, so after an uncheck a mid-session upload that sets "survivor" would be
        # re-nulled by the else-branch below. The upload path sets "_survivor_enabled" too
        # (audit C9 / ui-streamlit-5).
        st.session_state.setdefault("_survivor_enabled", bool(current))
        enabled = st.checkbox(
            "Enable survivor scenario",
            key="_survivor_enabled",
        )
        if enabled:
            who_options = ["Me", "Spouse"]
            who_default = 0 if current.get("who_dies", "you") == "you" else 1
            who_choice = st.radio(
                "Who dies?",
                who_options,
                index=who_default,
                horizontal=True,
                key="_survivor_who_dies",
            )
            who_dies = "you" if who_choice == "Me" else "spouse"
            death_year = st.number_input(
                "Year of death",
                min_value=base_year,
                max_value=base_year + 50,
                value=_clamp(
                    int(current.get("death_year", base_year + 5)), base_year, base_year + 50
                ),
                step=1,
                format="%d",
                help=(
                    "Calendar year in which the spouse dies. "
                    "MFJ filing applies for that year; Single filing begins the following year."
                ),
                key="_survivor_death_year",
            )
            st.session_state["survivor"] = {"who_dies": who_dies, "death_year": int(death_year)}
        else:
            st.session_state["survivor"] = None


def _render_inherited_iras(base_year: int) -> None:
    """Render the Inherited IRAs expander in the Joint sub-tab."""

    with st.expander("Inherited IRAs (non-spousal, 10-year rule)", expanded=False):
        st.caption(
            "Model non-spousal inherited IRAs subject to the SECURE Act 10-year rule. "
            "The beneficiary must fully distribute the balance within 10 years of inheritance. "
            "Distributions add to ordinary income (MAGI). "
            "Leave empty if no inheritances are modeled."
        )

        iiras: list[dict] = list(st.session_state.get("inherited_iras") or [])
        to_remove: int | None = None

        for idx, entry in enumerate(iiras):
            col_bal, col_yr, col_rate, col_owner, col_remove = st.columns([3, 2, 2, 2, 1])
            new_bal = col_bal.number_input(
                "Balance ($)",
                min_value=0,
                max_value=100_000_000,
                value=_clamp(int(entry.get("balance", 0)), 0, 100_000_000),
                step=10_000,
                format="%d",
                key=f"iira_balance_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            new_yr = col_yr.number_input(
                "Year inherited",
                min_value=base_year - 15,
                max_value=base_year + 30,
                value=_clamp(
                    int(entry.get("inherited_year", base_year + 5)), base_year - 15, base_year + 30
                ),
                step=1,
                format="%d",
                key=f"iira_year_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            new_rate = col_rate.number_input(
                "Growth Rate (%)",
                min_value=0.0,
                max_value=15.0,
                value=float(entry.get("growth_rate", 0.07)) * 100,
                step=0.5,
                format="%.1f",
                key=f"iira_rate_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            owner_options = ["Me", "Spouse"]
            owner_val = entry.get("owner", "you")
            owner_idx_sel = 0 if owner_val == "you" else 1
            owner_choice = col_owner.radio(
                "Owner",
                owner_options,
                index=owner_idx_sel,
                horizontal=True,
                key=f"iira_owner_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            if col_remove.button("Remove", key=f"iira_remove_{idx}"):
                to_remove = idx
            iiras[idx] = {
                "balance": float(new_bal),
                "inherited_year": int(new_yr),
                "owner": "you" if owner_choice == "Me" else "spouse",
                "growth_rate": new_rate / 100.0,
            }

        if to_remove is not None:
            iiras.pop(to_remove)
            st.session_state["inherited_iras"] = iiras
            st.rerun()

        if st.button("Add inherited IRA", key="iira_add"):
            iiras.append(
                {
                    "balance": 0.0,
                    "inherited_year": base_year + 5,
                    "owner": "you",
                    "growth_rate": 0.07,
                }
            )
            st.session_state["inherited_iras"] = iiras
            st.rerun()

        st.session_state["inherited_iras"] = iiras


_FILING_STATUS_OPTIONS = [
    "married_filing_jointly",
    "single",
    "married_filing_separately",
    "head_of_household",
]


_FILING_STATUS_LABELS = {
    "married_filing_jointly": "Married Filing Jointly",
    "single": "Single",
    "married_filing_separately": "Married Filing Separately",
    "head_of_household": "Head of Household",
}


def _render_pdf_1040_import() -> None:
    """Confirm-and-save UI for Form 1040 records already scanned elsewhere.

    Gated behind ``is_pyodide()`` — pdfplumber is not available in the web
    build. The scan itself (folder input, "Scan folder" button, MAGI
    candidate recording, pdf-tax-cache persist) lives on the YTD Income page
    (``views/ytd_income.py``, via ``views._shared.run_folder_scan``) — this is
    the single scan entry point (W2 Part A killed the duplicate folder/scan/
    writer that used to live here, audit defect #3). This block only reads
    the shared ``st.session_state["_pdf_1040_scanned"]`` result (single
    canonical shape) and shows a confirmation preview with the filing-status
    selectbox (parser leaves it None); on confirm, persists the
    Form1040Record (with the chosen filing status). MAGI itself was already
    recorded as a candidate by the scan — this loop never re-records it.
    """
    with st.expander("📄 Import 1040 PDF (TurboTax export)", expanded=False):
        if is_pyodide():
            st.caption("1040 PDF import requires a local install.")
            return

        scanned_records: dict[int, Form1040Record] = st.session_state.get("_pdf_1040_scanned", {})
        if not scanned_records:
            st.caption(
                "Scan your statement folder on the YTD Income page ('Scan folder') to "
                "import a Form 1040 PDF — parsed years appear here for confirmation."
            )
            return
        for _year in sorted(scanned_records):
            rec = scanned_records[_year]
            st.write(f"**Parsed {rec.tax_year} 1040 — please confirm:**")
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Tax Year", str(rec.tax_year))
            col_b.metric("AGI", fmt_dollars(rec.agi))
            col_c.metric("MAGI", fmt_dollars(rec.magi))
            col_d, col_e = st.columns(2)
            col_d.metric("Tax-Exempt Interest", fmt_dollars(rec.tax_exempt_interest))
            col_e.metric("FEIE", fmt_dollars(rec.feie))

            status_idx = (
                _FILING_STATUS_OPTIONS.index(rec.filing_status)
                if rec.filing_status in _FILING_STATUS_OPTIONS
                else 0
            )
            chosen_status = st.selectbox(
                "Filing Status",
                options=_FILING_STATUS_OPTIONS,
                index=status_idx,
                format_func=lambda s: _FILING_STATUS_LABELS.get(s, s),
                key=f"_pdf_1040_filing_status_{rec.tax_year}",
                help="Select the filing status for this return (parser cannot auto-detect checkboxes).",
            )

            if st.button("Save 1040 record", key=f"_pdf_1040_save_{rec.tax_year}"):
                rec.filing_status = chosen_status
                records = load_pdf_tax_records()
                records[rec.tax_year] = rec
                with st.spinner("Saving…"):
                    save_pdf_tax_records(records)
                st.info(
                    "1 prior-year MAGI value detected — review & confirm it in the "
                    "🎛️ Command Center tab."
                )
                # Drop the confirmed year so it doesn't re-prompt on rerun
                scanned_records.pop(_year, None)
                st.session_state["_pdf_1040_scanned"] = scanned_records
                st.success(
                    f"Saved {rec.tax_year} 1040 record "
                    f"(MAGI {fmt_dollars(rec.magi)}, {_FILING_STATUS_LABELS.get(chosen_status, chosen_status)}). "
                    "Rerunning…"
                )
                st.rerun()


def _render_prior_year_magi_anchor(base_year: int) -> None:
    """Render the Prior-year filed MAGI anchor expander in the Joint sub-tab."""
    with st.expander("Prior-year filed MAGI anchor (IRMAA lookback)", expanded=False):
        st.caption(
            "Optional. Enter actual filed MAGI from your tax return. "
            "The engine will use these values instead of projecting MAGI for the "
            "IRMAA 2-year-lookback "
            f"(years {base_year} and {base_year + 1} IRMAA will be anchored to these). "
            "Leave 0 to use projected MAGI."
        )
        prior_magi: dict[int, float] = dict(st.session_state.get("prior_year_magi") or {})

        v1 = st.number_input(
            f"{base_year - 2} filed MAGI",
            min_value=0,
            max_value=100_000_000,
            value=_clamp(int(prior_magi.get(base_year - 2, 0)), 0, 100_000_000),
            step=1_000,
            format="%d",
            help=(
                f"Filed MAGI from your {base_year - 2} tax return. "
                f"Anchors {base_year} IRMAA via the 2-year lookback."
            ),
        )
        v2 = st.number_input(
            f"{base_year - 1} filed MAGI",
            min_value=0,
            max_value=100_000_000,
            value=_clamp(int(prior_magi.get(base_year - 1, 0)), 0, 100_000_000),
            step=1_000,
            format="%d",
            help=(
                f"Filed MAGI from your {base_year - 1} tax return. "
                f"Anchors {base_year + 1} IRMAA via the 2-year lookback."
            ),
        )

        if v1 > 0:
            prior_magi[base_year - 2] = float(v1)
        else:
            prior_magi.pop(base_year - 2, None)

        if v2 > 0:
            prior_magi[base_year - 1] = float(v2)
        else:
            prior_magi.pop(base_year - 1, None)

        st.session_state["prior_year_magi"] = prior_magi


def render_parameters_tab(hh: Household) -> None:
    """Extracted from setup.py render() — parameters tab body."""
    # Household filing status — gate that activates the engine's Single-filer paths.
    _is_single = bool(render_household_partial(hh, st, "joint"))
    # NOTE: spouse inputs are intentionally NOT zeroed in session_state here — doing so
    # permanently destroyed the user's real spouse balances on a Single→MFJ round-trip
    # (audit C9 / ui-streamlit-4). The spouse widgets are disabled while Single, and the
    # single-filer zeroing is applied to the DERIVED Household in app.get_household()
    # via apply_single_filer().

    me_sub, spouse_sub, joint_sub = st.tabs(["Me", "Spouse", "Joint"])

    with me_sub:
        render_household_partial(hh, me_sub, "your")
        render_accounts_partial(hh, me_sub, "your")

    with spouse_sub:
        if _is_single:
            st.info(
                "Single filer — spouse inputs are disabled and treated as zero. "
                "Switch Filing status to Married filing jointly to re-enable."
            )
        render_household_partial(hh, spouse_sub, "spouse")
        render_accounts_partial(hh, spouse_sub, "spouse")

    with joint_sub:
        st.session_state.growth_rate = st.slider(
            "Growth Rate %",
            3.0,
            12.0,
            _clamp(st.session_state.growth_rate, 3.0, 12.0),
            0.5,
            format="%.1f%%",
        )
        st.session_state.living_expenses = st.number_input(
            "Annual Living Expenses",
            min_value=0,
            value=st.session_state.living_expenses,
            step=5_000,
            format="%d",
        )
        # txn_price / txn_price_now moved into
        # views/setup/_partials.py:render_options_partial (called once from
        # views/setup/portfolio.py's Portfolio tab) as of Task 5 of the
        # ui-shell-theme-toggle plan — co-located with the stock-grants table
        # it prices, alongside its own trust/manual/confirm governance card.
        st.session_state["aca_benchmark_premium_annual"] = st.number_input(
            "ACA Benchmark Premium ($/yr)",
            min_value=0,
            max_value=60_000,
            value=_clamp(
                int(st.session_state.get("aca_benchmark_premium_annual", 21_600.0)), 0, 60_000
            ),
            step=100,
            format="%d",
            help=(
                "Annual cost of the 2nd-lowest-cost Silver plan in your state/county "
                "for your age group. Used to calculate ACA subsidy loss from conversions. "
                "Varies widely by geography — check healthcare.gov for your area."
            ),
        )
        st.session_state["aca_enhanced_subsidies_active"] = st.checkbox(
            "ACA enhanced subsidies active (ARP/IRA-style)",
            value=st.session_state.get("aca_enhanced_subsidies_active", False),
            help=(
                "Toggle for sensitivity analysis. Default OFF matches current law "
                "(ARP enhanced subsidies expired Dec 31, 2025). Turn ON to model "
                "what-if ARP gets extended."
            ),
        )
        st.session_state["advance_aptc_annual"] = st.number_input(
            "Advance APTC ($/yr)",
            min_value=0,
            max_value=60_000,
            value=_clamp(int(st.session_state.get("advance_aptc_annual", 0)), 0, 60_000),
            step=100,
            format="%d",
            help=(
                "Annual advance APTC (total IRS pre-payments to your insurer). "
                "Set 0 if not on marketplace insurance. Reconciled on Form 8962 at "
                "year-end — conversions that raise MAGI may trigger clawback; per "
                "P.L. 119-21, no repayment cap applies for TY 2026+."
            ),
        )
        st.session_state["medicare_part_b_base_monthly"] = st.number_input(
            "Medicare Part B Base Premium ($/mo)",
            min_value=0.0,
            max_value=5000.0,
            value=_clamp(
                float(st.session_state.get("medicare_part_b_base_monthly", BASE_PART_B / 12)),
                0.0,
                5000.0,
            ),
            step=1.0,
            format="%.2f",
            help=(
                "Standard Medicare Part B monthly premium (CMS-published; $202.90 in 2026). "
                "IRMAA surcharges are computed on top of this base."
            ),
        )
        st.session_state["cpi_assumption"] = st.number_input(
            "Annual CPI Projection Rate (0.025 = 2.5%)",
            min_value=0.0,
            max_value=0.06,
            value=_clamp(float(st.session_state.get("cpi_assumption", 0.025)), 0.0, 0.06),
            step=0.001,
            format="%.3f",
            help=(
                "Annual CPI projection rate (default 2.5%). Tax brackets, IRMAA tiers, "
                "FPL, etc. are projected forward from 2026 base values using this rate."
            ),
        )
        _render_prior_year_magi_anchor(hh.base_year)
        _render_pdf_1040_import()
        _render_survivor_scenario(hh.base_year)
        _render_inherited_iras(hh.base_year)

    if not st.session_state.get("_suppress_snapshot_autoload"):
        save_user_defaults(_user_defaults_from_session())
