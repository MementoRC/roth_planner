"""Composable Setup-domain widget partials — shared by the Classic layout
(``views/setup/parameters.py`` et al.) and the alternate shells
(``views/shells/``, Task 8+).

Each ``render_X_partial(hh, container, ...)`` function renders a slice of
Setup's widgets into whatever Streamlit container the caller hands it (a
tab, an expander, or the top-level ``st`` module itself), so the SAME
widget code can be composed into different page layouts without forking
behavior. Widget shape — ``key=`` presence/absence, ``value=`` sourcing,
labels/help text — must be preserved EXACTLY when moving code here; see
Owner decision 5 in docs/superpowers/plans/2026-07-24-ui-shell-theme-toggle.md
(most widgets are intentionally unkeyed "controlled" widgets, and adding a
``key=`` to one would reintroduce a known Streamlit sync-override bug).
"""

from __future__ import annotations

import streamlit as st

from models.household import Household

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


def render_household_partial(hh: Household, container, owner: str) -> bool | None:
    """Render one owner slice of the Household/filing-status widgets.

    ``owner`` selects which slice to render:
      * ``"joint"`` — the filing-status radio (the one field that isn't
        per-person). Returns whether the resulting filing status is Single
        (``_is_single``), so the caller can gate its own not-yet-extracted
        spouse widgets with it.
      * ``"your"`` — your age, workplace-plan, RMD-start-age,
        defer-first-RMD, FRA-age, and ACA-eligible.
      * ``"spouse"`` — the same fields for spouse, plus
        ``spouse_is_sole_beneficiary`` (spouse-only). Reads
        ``st.session_state["filing_status"]`` (set by the ``"joint"`` call
        earlier in the same script run) to disable spouse fields when Single.

    Every widget keeps its EXACT current shape (unkeyed
    ``session_state.<attr> = st.<widget>(..., value=hh.<attr>)`` controlled
    pattern, or the one explicit ``key=``) per Owner decision 5.
    """
    if owner == "joint":
        _filing_choice = container.radio(
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
        return _is_single

    if owner == "your":
        st.session_state.your_age = container.number_input(
            "Your Age",
            value=st.session_state.your_age,
            step=1,
            format="%d",
        )
        st.session_state.your_has_workplace_plan = container.checkbox(
            "You have a workplace retirement plan (401k/403b)",
            value=st.session_state.your_has_workplace_plan,
        )
        _your_rmd_stored = st.session_state.get("your_rmd_start_age")
        if _your_rmd_stored is not None and _your_rmd_stored not in {73, 75}:
            container.warning(
                f"Stored RMD start age {_your_rmd_stored} is not valid (must be 73 or 75); "
                "falling back to 75."
            )
        st.session_state.your_rmd_start_age = container.selectbox(
            "Your RMD start age",
            options=[73, 75],
            index=0 if st.session_state.get("your_rmd_start_age", 75) == 73 else 1,
            help="73 if born 1951-1959 (SECURE 2.0 §107); 75 if born 1960+ (SECURE 2.0 §107)",
        )
        st.session_state.your_defer_first_rmd = container.checkbox(
            "Defer first RMD to April 1 (two RMDs in year 2)",
            value=st.session_state.get("your_defer_first_rmd", False),
            help=(
                "IRC §401(a)(9)(C)(ii): delay the first RMD to April 1 of the following year. "
                "The deferred RMD then stacks on year 2's RMD — may push a tax bracket or IRMAA tier."
            ),
        )
        st.session_state.your_fra_age = container.number_input(
            "Your FRA (Full Retirement Age)",
            min_value=65,
            max_value=70,
            value=st.session_state.get("your_fra_age", 67),
            step=1,
            format="%d",
            help="67 for born 1960+ (SECURE/SS default); 66 or 66+N/12 for earlier cohorts",
        )
        st.session_state.your_aca = container.checkbox(
            "You on ACA Marketplace",
            value=st.session_state.your_aca,
            help="Check if you are enrolled in ACA marketplace (not employer plan)",
        )
        return None

    if owner == "spouse":
        _is_single = st.session_state.get("filing_status", "MFJ") == "Single"
        st.session_state.spouse_age = container.number_input(
            "Spouse Age",
            value=st.session_state.spouse_age,
            step=1,
            format="%d",
            disabled=_is_single,
        )
        st.session_state.spouse_has_workplace_plan = container.checkbox(
            "Spouse has a workplace retirement plan (401k/403b)",
            value=st.session_state.spouse_has_workplace_plan,
            disabled=_is_single,
        )
        _spouse_rmd_stored = st.session_state.get("spouse_rmd_start_age")
        if _spouse_rmd_stored is not None and _spouse_rmd_stored not in {73, 75}:
            container.warning(
                f"Stored spouse RMD start age {_spouse_rmd_stored} is not valid "
                "(must be 73 or 75); falling back to 75."
            )
        st.session_state.spouse_rmd_start_age = container.selectbox(
            "Spouse RMD start age",
            options=[73, 75],
            index=0 if st.session_state.get("spouse_rmd_start_age", 75) == 73 else 1,
            help="73 if born 1951-1959 (SECURE 2.0 §107); 75 if born 1960+ (SECURE 2.0 §107)",
            disabled=_is_single,
        )
        st.session_state.spouse_defer_first_rmd = container.checkbox(
            "Defer spouse's first RMD to April 1 (two RMDs in year 2)",
            value=st.session_state.get("spouse_defer_first_rmd", False),
            help=(
                "IRC §401(a)(9)(C)(ii): delay the spouse's first RMD to April 1 of the "
                "following year. The deferred RMD then stacks on year 2's RMD — may push "
                "a tax bracket or IRMAA tier."
            ),
            disabled=_is_single,
        )
        st.session_state.spouse_is_sole_beneficiary = container.checkbox(
            "Spouse is sole IRA beneficiary and >10 yrs younger (use IRS Joint & "
            "Last Survivor Table for RMDs)",
            value=st.session_state.get("spouse_is_sole_beneficiary", False),
            help=(
                "26 CFR §1.401(a)(9)-9 Table II: when your sole primary IRA "
                "beneficiary is a spouse more than 10 years younger, the IRS "
                "requires this larger-divisor table instead of the standard "
                "Uniform Lifetime Table — producing a smaller RMD. Only applies "
                "when the age gap qualifies; otherwise the standard table is used."
            ),
            disabled=_is_single,
        )
        st.session_state.spouse_fra_age = container.number_input(
            "Spouse FRA (Full Retirement Age)",
            min_value=65,
            max_value=70,
            value=st.session_state.get("spouse_fra_age", 67),
            step=1,
            format="%d",
            help="67 for born 1960+ (SECURE/SS default); 66 or 66+N/12 for earlier cohorts",
            disabled=_is_single,
        )
        st.session_state.spouse_aca = container.checkbox(
            "Spouse on ACA Marketplace",
            value=st.session_state.spouse_aca,
            help="Check if spouse is enrolled in ACA marketplace",
            disabled=_is_single,
        )
        return None

    raise ValueError(f"Unknown owner slice: {owner!r}")
