"""Parameters tab — Me/Spouse/Joint sub-tabs (survivor, inherited IRAs, PDF 1040 import, MAGI anchor, filing-status pickers)."""

from __future__ import annotations

import streamlit as st

from engine.data_bridge_browser import (
    is_pyodide,
)
from engine.irmaa import BASE_PART_B
from engine.tax_return_pdf import (
    Form1040ParseError,
    load_pdf_tax_records,
    parse_form_1040_pdf,
    save_pdf_tax_records,
)
from models.household import Household
from views._format import fmt_dollars

_HH_FILING_LABEL_MFJ = "Married filing jointly"
_HH_FILING_LABEL_SINGLE = "Single"


def filing_status_from_label(label: str) -> str:
    """Map the household filing-status radio label to the engine's canonical value.

    The engine compares ``hh.filing_status`` against ``"MFJ"`` / ``"Single"``
    (capitalized). This is a DIFFERENT vocabulary from the lowercase
    ``_FILING_STATUS_OPTIONS`` used by the PDF-1040 import widget to tag an
    imported prior-year return — the two must not be conflated, or the engine's
    ``== "Single"`` branches stay dead code (R1 #6).
    """
    return _HH_FILING_LABEL_SINGLE if label == _HH_FILING_LABEL_SINGLE else "MFJ"


def spouse_single_overrides() -> dict[str, object]:
    """session_state overrides applied when the household files Single.

    Single models a single-from-the-start household (no spouse). Zeroing the
    spouse financial/age inputs and clearing the spouse ACA flag lets the
    (otherwise MFJ-shaped) engine non-survivor path produce single-filer income;
    ``filing_status="Single"`` separately selects the single brackets, standard
    deduction, IRMAA/NIIT thresholds, and ACA FPL.
    """
    return {
        "spouse_ira": 0,
        "spouse_roth": 0,
        "spouse_age": 0,
        "spouse_ss_fra": 0,
        "spouse_aca": False,
    }


def _render_survivor_scenario() -> None:
    """Render the Survivor scenario expander in the Joint sub-tab."""
    base_year: int = 2026
    current: dict = st.session_state.get("survivor") or {}

    with st.expander("Survivor scenario (advanced sensitivity)", expanded=False):
        st.caption(
            "Optional. Models death of one spouse mid-projection. "
            "Survivor switches to single-filer brackets, std deduction, and senior bonus "
            "starting death_year + 1. Deceased's IRA rolls to survivor (spousal rollover); "
            "deceased's SS ends. "
            "NOT YET MODELED: SS survivor benefit step-up; inherited-IRA stretch rules."
        )
        enabled = st.checkbox(
            "Enable survivor scenario",
            value=bool(current),
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
                max_value=base_year + 30,
                value=int(current.get("death_year", base_year + 5)),
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


def _render_inherited_iras() -> None:
    """Render the Inherited IRAs expander in the Joint sub-tab."""
    base_year: int = 2026

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
            col_bal, col_yr, col_owner, col_remove = st.columns([3, 2, 2, 1])
            new_bal = col_bal.number_input(
                "Balance ($)",
                min_value=0,
                max_value=10_000_000,
                value=int(entry.get("balance", 0)),
                step=10_000,
                format="%d",
                key=f"iira_balance_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            new_yr = col_yr.number_input(
                "Year inherited",
                min_value=base_year,
                max_value=base_year + 30,
                value=int(entry.get("inherited_year", base_year + 5)),
                step=1,
                format="%d",
                key=f"iira_year_{idx}",
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
                "growth_rate": float(entry.get("growth_rate", 0.07)),
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
    """Widget to import MAGI from a TurboTax-exported 1040 PDF.

    Gated behind ``is_pyodide()`` — pdfplumber is not available in the web build.
    Parses the PDF, shows a confirmation preview with the filing-status selectbox
    (parser leaves it None), then on confirm persists the record and writes MAGI
    into session_state["prior_year_magi"].
    """
    with st.expander("📄 Import 1040 PDF (TurboTax export)", expanded=False):
        if is_pyodide():
            st.caption("1040 PDF import requires a local install.")
            return

        st.caption(
            "Upload a TurboTax-exported 1040 PDF to back-fill prior-year MAGI. "
            "Supports tax years 2023 and 2024. "
            "Parsed values are shown for confirmation before saving."
        )
        pdf_file = st.file_uploader(
            "Form 1040 PDF (TurboTax export)",
            type=["pdf"],
            key="pdf_1040_upload",
        )

        if pdf_file is None:
            return

        # Parse on every render while a file is present; cache result in session_state
        # to avoid re-parsing on every widget interaction after the file is loaded.
        cache_key = f"_pdf_1040_parsed_{pdf_file.name}_{pdf_file.size}"
        if cache_key not in st.session_state:
            try:
                with st.spinner("Parsing 1040 PDF…"):
                    rec = parse_form_1040_pdf(pdf_file.read())
                st.session_state[cache_key] = rec
            except Form1040ParseError as exc:
                st.error(f"Could not parse {pdf_file.name}: {exc}")
                return

        rec = st.session_state[cache_key]

        st.write("**Parsed values — please confirm:**")
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
            # Direct write — user just confirmed; overrides any existing value
            prior_magi: dict[int, float] = dict(st.session_state.get("prior_year_magi") or {})
            prior_magi[rec.tax_year] = rec.magi
            st.session_state["prior_year_magi"] = prior_magi
            # Clear parse cache so a new upload starts fresh
            st.session_state.pop(cache_key, None)
            st.success(
                f"Saved {rec.tax_year} 1040 record "
                f"(MAGI {fmt_dollars(rec.magi)}, {_FILING_STATUS_LABELS.get(chosen_status, chosen_status)}). "
                "Rerunning…"
            )
            st.rerun()


def _render_prior_year_magi_anchor() -> None:
    """Render the Prior-year filed MAGI anchor expander in the Joint sub-tab."""
    base_year: int = 2026
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
            max_value=2_000_000,
            value=int(prior_magi.get(base_year - 2, 0)),
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
            max_value=2_000_000,
            value=int(prior_magi.get(base_year - 1, 0)),
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
    _synced = bool(st.session_state.get("portfolio_snapshot"))

    # Household filing status — gate that activates the engine's Single-filer paths.
    _filing_choice = st.radio(
        "Filing status",
        [_HH_FILING_LABEL_MFJ, _HH_FILING_LABEL_SINGLE],
        index=0 if st.session_state.get("filing_status", "MFJ") == "MFJ" else 1,
        horizontal=True,
        key="_hh_filing_status_choice",
        help=(
            "Single models a single-from-the-start household: spouse inputs are "
            "zeroed and single-filer brackets, standard deduction, IRMAA/NIIT "
            "thresholds, and ACA FPL apply. To model a spouse dying mid-projection, "
            "leave this on Married filing jointly and use the Survivor scenario "
            "(Joint sub-tab)."
        ),
    )
    _is_single = filing_status_from_label(_filing_choice) == "Single"
    st.session_state["filing_status"] = "Single" if _is_single else "MFJ"
    if _is_single:
        for _key, _val in spouse_single_overrides().items():
            st.session_state[_key] = _val

    me_sub, spouse_sub, joint_sub = st.tabs(["Me", "Spouse", "Joint"])

    with me_sub:
        st.session_state.your_ira = st.number_input(
            "Your Trad IRA" + (" (synced)" if _synced else ""),
            value=st.session_state.your_ira,
            step=50_000,
            format="%d",
            disabled=_synced,
            help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
        )
        st.session_state.your_roth = st.number_input(
            "Your Roth IRA" + (" (synced)" if _synced else ""),
            value=st.session_state.get("your_roth", 0),
            step=50_000,
            format="%d",
            disabled=_synced,
            help="Auto-synced from FinExtract (Roth IRA)" if _synced else None,
        )
        st.session_state.your_age = st.number_input(
            "Your Age",
            value=st.session_state.your_age,
            step=1,
            format="%d",
        )
        st.session_state.your_ss_fra = st.number_input(
            "Your SS at FRA 67 ($/mo)",
            value=st.session_state.your_ss_fra,
            step=100,
            format="%d",
        )
        st.session_state.your_ss_start_age = st.number_input(
            "Your SS claim age",
            min_value=62,
            max_value=70,
            value=st.session_state.get("your_ss_start_age", 70),
            step=1,
            format="%d",
        )
        st.session_state.your_rmd_start_age = st.number_input(
            "Your RMD start age",
            min_value=73,
            max_value=75,
            value=st.session_state.get("your_rmd_start_age", 75),
            step=1,
            format="%d",
            help="73 if born 1951-1959 (SECURE 1.0); 75 if born 1960+ (SECURE 2.0)",
        )
        st.session_state.your_defer_first_rmd = st.checkbox(
            "Defer first RMD to April 1 (two RMDs in year 2)",
            value=st.session_state.get("your_defer_first_rmd", False),
            help=(
                "IRC §401(a)(9)(C)(ii): delay the first RMD to April 1 of the following year. "
                "The deferred RMD then stacks on year 2's RMD — may push a tax bracket or IRMAA tier."
            ),
        )
        st.session_state.your_fra_age = st.number_input(
            "Your FRA (Full Retirement Age)",
            min_value=65,
            max_value=70,
            value=st.session_state.get("your_fra_age", 67),
            step=1,
            format="%d",
            help="67 for born 1960+ (SECURE/SS default); 66 or 66+N/12 for earlier cohorts",
        )
        st.session_state.your_aca = st.checkbox(
            "You on ACA Marketplace",
            value=st.session_state.your_aca,
            help="Check if you are enrolled in ACA marketplace (not employer plan)",
        )

    with spouse_sub:
        if _is_single:
            st.info(
                "Single filer — spouse inputs are disabled and treated as zero. "
                "Switch Filing status to Married filing jointly to re-enable."
            )
        st.session_state.spouse_ira = st.number_input(
            "Spouse Trad IRA" + (" (synced)" if _synced else ""),
            value=st.session_state.spouse_ira,
            step=50_000,
            format="%d",
            disabled=_synced or _is_single,
            help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
        )
        st.session_state.spouse_roth = st.number_input(
            "Spouse Roth IRA" + (" (synced)" if _synced else ""),
            value=st.session_state.get("spouse_roth", 0),
            step=50_000,
            format="%d",
            disabled=_synced or _is_single,
            help="Auto-synced from FinExtract (Roth IRA)" if _synced else None,
        )
        st.session_state.spouse_age = st.number_input(
            "Spouse Age",
            value=st.session_state.spouse_age,
            step=1,
            format="%d",
            disabled=_is_single,
        )
        st.session_state.spouse_ss_fra = st.number_input(
            "Spouse SS at FRA 67 ($/mo)",
            value=st.session_state.spouse_ss_fra,
            step=100,
            format="%d",
            disabled=_is_single,
        )
        st.session_state.spouse_ss_start_age = st.number_input(
            "Spouse SS claim age",
            min_value=62,
            max_value=70,
            value=st.session_state.get("spouse_ss_start_age", 70),
            step=1,
            format="%d",
            disabled=_is_single,
        )
        st.session_state.spouse_rmd_start_age = st.number_input(
            "Spouse RMD start age",
            min_value=73,
            max_value=75,
            value=st.session_state.get("spouse_rmd_start_age", 75),
            step=1,
            format="%d",
            help="73 if born 1951-1959 (SECURE 1.0); 75 if born 1960+ (SECURE 2.0)",
            disabled=_is_single,
        )
        st.session_state.spouse_defer_first_rmd = st.checkbox(
            "Defer spouse's first RMD to April 1 (two RMDs in year 2)",
            value=st.session_state.get("spouse_defer_first_rmd", False),
            help=(
                "IRC §401(a)(9)(C)(ii): delay the spouse's first RMD to April 1 of the following year. "
                "The deferred RMD then stacks on year 2's RMD — may push a tax bracket or IRMAA tier."
            ),
            disabled=_is_single,
        )
        st.session_state.spouse_fra_age = st.number_input(
            "Spouse FRA (Full Retirement Age)",
            min_value=65,
            max_value=70,
            value=st.session_state.get("spouse_fra_age", 67),
            step=1,
            format="%d",
            help="67 for born 1960+ (SECURE/SS default); 66 or 66+N/12 for earlier cohorts",
            disabled=_is_single,
        )
        st.session_state.spouse_aca = st.checkbox(
            "Spouse on ACA Marketplace",
            value=st.session_state.spouse_aca,
            help="Check if spouse is enrolled in ACA marketplace",
            disabled=_is_single,
        )

    with joint_sub:
        st.session_state.growth_rate = st.slider(
            "Growth Rate %", 3.0, 12.0, st.session_state.growth_rate, 0.5
        )
        st.session_state.living_expenses = st.number_input(
            "Annual Living Expenses",
            value=st.session_state.living_expenses,
            step=5_000,
            format="%d",
        )
        st.session_state.txn_price = st.number_input(
            f"{st.session_state.get('_stock_ticker', 'Stock')} Current Price",
            value=st.session_state.txn_price,
            step=5,
            format="%d",
        )
        st.session_state["aca_benchmark_premium_annual"] = st.number_input(
            "ACA Benchmark Premium ($/yr)",
            min_value=0,
            max_value=60_000,
            value=int(st.session_state.get("aca_benchmark_premium_annual", 21_600.0)),
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
            value=int(st.session_state.get("advance_aptc_annual", 0)),
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
            max_value=1000.0,
            value=float(st.session_state.get("medicare_part_b_base_monthly", BASE_PART_B / 12)),
            step=1.0,
            format="%.2f",
            help=(
                "Standard Medicare Part B monthly premium (CMS-published; $202.90 in 2026). "
                "IRMAA surcharges are computed on top of this base."
            ),
        )
        st.session_state["cpi_assumption"] = st.number_input(
            "Annual CPI Projection Rate",
            min_value=0.0,
            max_value=0.06,
            value=float(st.session_state.get("cpi_assumption", 0.025)),
            step=0.001,
            format="%.3f",
            help=(
                "Annual CPI projection rate (default 2.5%). Tax brackets, IRMAA tiers, "
                "FPL, etc. are projected forward from 2026 base values using this rate."
            ),
        )
        _render_prior_year_magi_anchor()
        _render_pdf_1040_import()
        _render_survivor_scenario()
        _render_inherited_iras()
