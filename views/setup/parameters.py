"""Parameters tab — Me/Spouse/Joint sub-tabs (survivor, inherited IRAs, PDF 1040 import, MAGI anchor, filing-status pickers)."""

from __future__ import annotations

from dataclasses import replace
from typing import TypeVar

import streamlit as st

from engine.data_bridge_browser import (
    is_pyodide,
)
from engine.irmaa import BASE_PART_B
from engine.portfolio_sync import fetch_ssa_snapshot, match_fra_estimate, save_ssa_snapshot
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


def apply_single_filer(hh: Household) -> Household:
    """Return a copy of ``hh`` with spouse inputs zeroed when filing Single.

    Single models a single-from-the-start household. The zeroing is applied to the
    DERIVED Household (never to session_state) so toggling back to MFJ restores the
    user's real spouse balances (audit C9 / ui-streamlit-4). Mirrors the fields in
    ``spouse_single_overrides`` (session-state key ``spouse_aca`` maps to the Household
    field ``spouse_aca_enrolled``).
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


def _sync_ssa_for(owner: str, fra_age: int) -> str | None:
    """Fetch, match, and apply the FRA SSA benefit for *owner* ('you' or 'spouse').

    Writes the matched monthly benefit into session_state and caches the raw
    snapshot. Returns a warning message on failure/no-match, or None on success.
    """
    snap = fetch_ssa_snapshot()
    if snap.error:
        return f"SSA sync failed: {snap.error}"
    match = match_fra_estimate(snap.estimates, fra_age)
    if match is None:
        return "No SSA benefit estimate found near the configured FRA age; sync skipped."
    session_key = "your_ss_fra" if owner == "you" else "spouse_ss_fra"
    st.session_state[session_key] = match.monthly_amount
    st.session_state[f"ssa_snapshot_{owner}"] = snap
    save_ssa_snapshot(snap, owner=owner)
    return None


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
    # NOTE: spouse inputs are intentionally NOT zeroed in session_state here — doing so
    # permanently destroyed the user's real spouse balances on a Single→MFJ round-trip
    # (audit C9 / ui-streamlit-4). The spouse widgets are disabled while Single, and the
    # single-filer zeroing is applied to the DERIVED Household in app.get_household()
    # via apply_single_filer().

    me_sub, spouse_sub, joint_sub = st.tabs(["Me", "Spouse", "Joint"])

    with me_sub:
        st.session_state.your_ira = st.number_input(
            "Your Trad IRA" + (" (synced)" if _synced else ""),
            min_value=0,
            value=st.session_state.your_ira,
            step=50_000,
            format="%d",
            disabled=_synced,
            help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
        )
        st.session_state.your_roth = st.number_input(
            "Your Roth IRA" + (" (synced)" if _synced else ""),
            min_value=0,
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
        _ssa_synced_you = bool(st.session_state.get("ssa_snapshot_you"))
        your_fra_age = st.session_state.get("your_fra_age", 67)
        st.session_state.your_ss_fra = st.number_input(
            f"Your SS at FRA {your_fra_age} ($/mo)" + (" (synced)" if _ssa_synced_you else ""),
            min_value=0,  # UU2-UI-06
            value=st.session_state.your_ss_fra,
            step=100,
            format="%d",
            disabled=_ssa_synced_you,
            help="Auto-synced from FinExtract (SSA benefit estimate)" if _ssa_synced_you else None,
        )
        if st.button("Sync SS from FinExtract", key="_sync_ssa_you_btn"):
            _warning = _sync_ssa_for("you", your_fra_age)
            if _warning:
                st.warning(_warning)
            else:
                st.rerun()
        st.session_state.your_ss_start_age = st.number_input(
            "Your SS claim age",
            min_value=62,
            max_value=70,
            value=st.session_state.get("your_ss_start_age", 70),
            step=1,
            format="%d",
        )
        _your_rmd_stored = st.session_state.get("your_rmd_start_age")
        if _your_rmd_stored is not None and _your_rmd_stored not in {73, 75}:
            st.warning(
                f"Stored RMD start age {_your_rmd_stored} is not valid (must be 73 or 75); "
                "falling back to 75."
            )
        st.session_state.your_rmd_start_age = st.selectbox(
            "Your RMD start age",
            options=[73, 75],
            index=0 if st.session_state.get("your_rmd_start_age", 75) == 73 else 1,
            help="73 if born 1951-1959 (SECURE 2.0 §107); 75 if born 1960+ (SECURE 2.0 §107)",
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
            min_value=0,
            value=st.session_state.spouse_ira,
            step=50_000,
            format="%d",
            disabled=_synced or _is_single,
            help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
        )
        st.session_state.spouse_roth = st.number_input(
            "Spouse Roth IRA" + (" (synced)" if _synced else ""),
            min_value=0,
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
        _ssa_synced_spouse = bool(st.session_state.get("ssa_snapshot_spouse"))
        spouse_fra_age = st.session_state.get("spouse_fra_age", 67)
        st.session_state.spouse_ss_fra = st.number_input(
            f"Spouse SS at FRA {spouse_fra_age} ($/mo)"
            + (" (synced)" if _ssa_synced_spouse else ""),
            min_value=0,  # UU2-UI-06
            value=st.session_state.spouse_ss_fra,
            step=100,
            format="%d",
            disabled=_is_single or _ssa_synced_spouse,
            help="Auto-synced from FinExtract (SSA benefit estimate)"
            if _ssa_synced_spouse
            else None,
        )
        if st.button("Sync SS from FinExtract", key="_sync_ssa_spouse_btn", disabled=_is_single):
            _warning = _sync_ssa_for("spouse", spouse_fra_age)
            if _warning:
                st.warning(_warning)
            else:
                st.rerun()
        st.session_state.spouse_ss_start_age = st.number_input(
            "Spouse SS claim age",
            min_value=62,
            max_value=70,
            value=st.session_state.get("spouse_ss_start_age", 70),
            step=1,
            format="%d",
            disabled=_is_single,
        )
        _spouse_rmd_stored = st.session_state.get("spouse_rmd_start_age")
        if _spouse_rmd_stored is not None and _spouse_rmd_stored not in {73, 75}:
            st.warning(
                f"Stored spouse RMD start age {_spouse_rmd_stored} is not valid (must be 73 or 75); "
                "falling back to 75."
            )
        st.session_state.spouse_rmd_start_age = st.selectbox(
            "Spouse RMD start age",
            options=[73, 75],
            index=0 if st.session_state.get("spouse_rmd_start_age", 75) == 73 else 1,
            help="73 if born 1951-1959 (SECURE 2.0 §107); 75 if born 1960+ (SECURE 2.0 §107)",
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
            "Growth Rate %", 3.0, 12.0, st.session_state.growth_rate, 0.5, format="%.1f%%"
        )
        st.session_state.living_expenses = st.number_input(
            "Annual Living Expenses",
            min_value=0,
            value=st.session_state.living_expenses,
            step=5_000,
            format="%d",
        )
        st.session_state.txn_price = st.number_input(
            f"{st.session_state.get('_stock_ticker', 'Stock')} Current Price",
            min_value=0,
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
            value=float(st.session_state.get("cpi_assumption", 0.025)),
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
