"""Parameters — single-filer derivation, filing-status vocabulary, and the
PDF 1040 import helper.

The Me/Spouse/Joint sub-tab composition (``render_parameters_tab``) that
used to live in this module was deleted (zero production callers — the
Classic shell that routed through it was already removed; every surviving
Setup shell, e.g. ``views/shells/domains_shell.py``, composes
``views/setup/_partials/`` directly, including
``views/setup/_partials._assumptions:render_assumptions_partial`` for the
growth-rate/living-expenses/ACA-benchmark/enhanced-subsidies/advance-APTC/
Medicare-Part-B/CPI/prior-year-MAGI-anchor widgets plus the survivor-scenario
and inherited-IRAs expanders). ``_render_pdf_1040_import`` below is still
called directly by those shells.
"""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

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

    No longer gated behind ``is_pyodide()`` — the public site can now scan a
    1040 too, via the uploader on Setup ▸ Data (``views/ytd_income/_partials/
    _sync_scan.py:_render_pdf_uploader``, which installs pdfplumber at
    runtime through ``views._pdf_runtime.ensure_pdf_backend``). The scan
    itself (folder input + "Scan folder" button locally, the uploader
    everywhere, MAGI candidate recording, pdf-tax-cache persist) lives on
    that same Data tab — this is the single scan entry point (W2 Part A
    killed the duplicate folder/scan/writer that used to live here, audit
    defect #3). This block only reads the shared
    ``st.session_state["_pdf_1040_scanned"]`` result (single canonical
    shape) and shows a confirmation preview with the filing-status selectbox
    (parser leaves it None); on confirm, persists the Form1040Record (with
    the chosen filing status). MAGI itself was already recorded as a
    candidate by the scan — this loop never re-records it.
    """
    with st.expander("📄 Import 1040 PDF (TurboTax export)", expanded=False):
        scanned_records: dict[int, Form1040Record] = st.session_state.get("_pdf_1040_scanned", {})
        if not scanned_records:
            st.caption(
                "Scan your statements in **PDF Statements**, above ('Scan folder' locally, "
                "or upload PDFs directly) to import a Form 1040 PDF — parsed years "
                "appear here for confirmation."
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

            if is_pyodide():
                st.caption(
                    "⚠️ This browser session's data is lost on reload — use "
                    "**Import previous data ▸ 📦 Export my data**, above, to keep it."
                )
            if st.button("Save 1040 record", key=f"_pdf_1040_save_{rec.tax_year}"):
                rec.filing_status = chosen_status
                records = load_pdf_tax_records()
                records[rec.tax_year] = rec
                with st.spinner("Saving…"):
                    save_pdf_tax_records(records)
                st.info(
                    "1 prior-year MAGI value detected — review & confirm it in "
                    "**Command Center**, above."
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
