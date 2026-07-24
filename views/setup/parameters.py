"""Parameters tab — Me/Spouse/Joint sub-tabs (PDF 1040 import, filing-status pickers).

Growth-rate/living-expenses/ACA-benchmark/enhanced-subsidies/advance-APTC/
Medicare-Part-B/CPI/prior-year-MAGI-anchor widgets, plus the survivor-scenario
and inherited-IRAs expanders, moved into
``views/setup/_partials._assumptions:render_assumptions_partial`` as of Task 7
of the ui-shell-theme-toggle plan — this module's Joint sub-tab now just calls
that partial (see ``render_parameters_tab``).
"""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from config.loader import save_user_defaults
from engine.data_bridge_browser import (
    is_pyodide,
)
from engine.tax_return_pdf import (
    Form1040Record,
    load_pdf_tax_records,
    save_pdf_tax_records,
)
from models.household import Household
from views._format import fmt_dollars
from views.setup._partials import (
    render_accounts_partial,
    render_assumptions_partial,
    render_household_partial,
)
from views.setup._state import _user_defaults_from_session


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
        # growth_rate/living_expenses/ACA-benchmark/enhanced-subsidies/
        # advance-APTC/Medicare-Part-B/CPI/prior-year-MAGI-anchor (incl. its
        # governance card), survivor-scenario, and inherited-IRAs widgets
        # moved into render_assumptions_partial as of Task 7 of the
        # ui-shell-theme-toggle plan. _render_pdf_1040_import (not part of
        # that field list) now renders AFTER this call instead of between
        # the MAGI anchor and the survivor-scenario expander — a minor
        # same-tab reorder accepted under the plan's Task 3 exception (see
        # render_assumptions_partial's docstring).
        render_assumptions_partial(hh, joint_sub)
        _render_pdf_1040_import()

    if not st.session_state.get("_suppress_snapshot_autoload"):
        save_user_defaults(_user_defaults_from_session())
