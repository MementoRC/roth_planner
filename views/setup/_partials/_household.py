"""Household/filing-status Setup-domain partial (Task 3 of the
ui-shell-theme-toggle plan).

Split out of the original flat ``views/setup/_partials.py`` when that module
grew to ~980 lines (pure mechanical reorganization, no behavior change).
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

import streamlit as st

from config.scalar_registry import scalar_default
from models.household import Household, default_rmd_age

from ._clamp import clamp as _clamp

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


_RMD_AUTO_LABEL = "Auto (from birth year)"


def _rmd_option_label(option: int | None) -> str:
    return _RMD_AUTO_LABEL if option is None else str(option)


def _rmd_option_index(state: MutableMapping[str, Any], person: str) -> int:
    """Selectbox index over ``[None, 73, 75]``: Auto unless the user chose one."""
    if not state.get(f"{person}_rmd_age_chosen", False):
        return 0
    return 1 if state.get(f"{person}_rmd_start_age") == 73 else 2


def rmd_choice_write_back(state: MutableMapping[str, Any], person: str, choice: int | None) -> None:
    """Record the RMD-start-age selectbox result (audit-1026 F-18).

    ``None`` is Auto: only the marker is cleared, so the derived age is never
    written into state (writing it froze a derived 73 after an age correction).
    """
    state[f"{person}_rmd_age_chosen"] = choice is not None
    if choice is not None:
        state[f"{person}_rmd_start_age"] = choice


def stable_age_input(
    container, label: str, state: MutableMapping[str, Any], attr: str, fallback: int, **kwargs: Any
) -> int:
    """Age ``number_input`` with a stable identity, so typed edits never lag a rerun.

    An unkeyed ``value=hh.<age>`` widget is re-identified whenever ``value`` changes,
    which discarded the NEXT edit after each one. The widget is keyed instead and
    re-seeded from ``state[attr]`` only when something other than this widget
    (import, reset, ...) changed it since the widget last wrote it.
    """
    widget_key = f"_hh_{attr}_input"
    marker = f"_hh_{attr}_synced"
    current = state.get(attr, fallback)
    if widget_key not in state or state.get(marker) != current:
        state[widget_key] = current
    value = container.number_input(label, step=1, format="%d", key=widget_key, **kwargs)
    state[marker] = value
    return int(value)


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
        earlier in the same script run) to disable spouse fields when Single
        — this one read deliberately stays on ``session_state`` rather than
        ``hh.filing_status``: ``hh`` is a snapshot built at the TOP of this
        script run (before this partial executes), so it would still show
        the PRE-interaction value, one render behind the "joint" branch's
        own write a few lines above it in this same run.

    Every widget keeps its EXACT current shape (unkeyed
    ``session_state.<attr> = st.<widget>(..., value=hh.<attr>)`` controlled
    pattern, or the one explicit ``key=``) per Owner decision 5. ``value=``/
    ``index=`` read from ``hh.<attr>`` (not raw ``session_state``) because
    ``hh`` is not always a pure passthrough: ``Household.__post_init__``
    derives ``your_rmd_start_age``/``spouse_rmd_start_age`` from birth
    cohort whenever the raw stored value isn't exactly 73, so
    ``hh.your_rmd_start_age`` can legitimately differ from
    ``session_state.get("your_rmd_start_age", 75)`` — reading raw
    session_state here would show a stale/sentinel value in the dropdown
    while the engine actually computes RMDs off the derived one. The
    "stored value is invalid" warning checks below are the one exception:
    they intentionally read raw ``session_state`` (not ``hh``, which is
    always self-correcting and would never show as invalid) to flag
    corrupted persisted data before ``__post_init__`` silently fixes it.
    """
    if owner == "joint":
        _filing_choice = container.radio(
            "Filing status",
            [_HH_FILING_LABEL_MFJ, _HH_FILING_LABEL_SINGLE],
            index=0 if hh.filing_status == "MFJ" else 1,
            horizontal=True,
            key="_hh_filing_status_choice",
            help=(
                "Single models a single-from-the-start household: spouse inputs are "
                "zeroed and single-filer brackets, standard deduction, IRMAA/NIIT "
                "thresholds, and ACA FPL apply. To model a spouse dying mid-projection, "
                "leave this on Married filing jointly and use the Survivor scenario "
                "on the Assumptions tab."
            ),
        )
        _is_single = filing_status_from_label(_filing_choice) == "Single"
        st.session_state["filing_status"] = "Single" if _is_single else "MFJ"
        return _is_single

    if owner == "your":
        _your_age = stable_age_input(
            container, "Your Age", st.session_state, "your_age", hh.your_age
        )
        st.session_state.your_age = _your_age
        st.session_state.your_has_workplace_plan = container.checkbox(
            "You have a workplace retirement plan (401k/403b)",
            value=hh.your_has_workplace_plan,
        )
        _your_rmd_stored = st.session_state.get("your_rmd_start_age")
        if _your_rmd_stored is not None and _your_rmd_stored not in {73, 75}:
            container.warning(
                f"Stored RMD start age {_your_rmd_stored} is not valid (must be 73 or 75); "
                "falling back to 75."
            )
        rmd_choice_write_back(
            st.session_state,
            "your",
            container.selectbox(
                "Your RMD start age",
                options=[None, 73, 75],
                index=_rmd_option_index(st.session_state, "your"),
                format_func=lambda o: (
                    f"{_RMD_AUTO_LABEL}: {default_rmd_age(hh.base_year - _your_age)}"
                    if o is None
                    else _rmd_option_label(o)
                ),
                help="Auto follows your birth year: 73 if born 1951-1959, 75 if born 1960+ "
                "(SECURE 2.0 §107). Pick 73 or 75 only to override.",
            ),
        )
        st.session_state.your_defer_first_rmd = container.checkbox(
            "Defer first RMD to April 1 (two RMDs in year 2)",
            value=hh.your_defer_first_rmd,
            help=(
                "IRC §401(a)(9)(C)(i): delay the first RMD to April 1 of the following year. "
                "The deferred RMD then stacks on year 2's RMD — may push a tax bracket or IRMAA tier."
            ),
        )
        st.session_state.your_fra_age = container.number_input(
            "Your FRA (Full Retirement Age)",
            min_value=65,
            max_value=70,
            value=_clamp(hh.your_fra_age, 65, 70),
            step=1,
            format="%d",
            help="67 for born 1960+ (SECURE/SS default); 66 or 66+N/12 for earlier cohorts",
        )
        st.session_state.your_aca = container.checkbox(
            "You on ACA Marketplace",
            value=hh.your_aca_enrolled,
            help="Check if you are enrolled in ACA marketplace (not employer plan)",
        )
        return None

    if owner == "spouse":
        _is_single = (
            st.session_state.get("filing_status", scalar_default("filing_status")) == "Single"
        )
        # audit-1008 F-32: a Single ``hh`` is apply_single_filer's DERIVED copy with
        # spouse_age / spouse_has_workplace_plan / spouse_aca_enrolled zeroed. That
        # includes the run in which the user flips back to MFJ (``hh`` was built at
        # the top of the run, before the radio wrote the new status), so seed those
        # three widgets from the user's stored values instead of the zeros.
        _zeroed = hh.filing_status == "Single"
        _ss = st.session_state
        _age_val = _ss.get("spouse_age", hh.spouse_age) if _zeroed else hh.spouse_age
        _wp_val = (
            _ss.get("spouse_has_workplace_plan", hh.spouse_has_workplace_plan)
            if _zeroed
            else hh.spouse_has_workplace_plan
        )
        _aca_val = (
            _ss.get("spouse_aca", hh.spouse_aca_enrolled) if _zeroed else hh.spouse_aca_enrolled
        )
        _spouse_age = stable_age_input(
            container, "Spouse Age", _ss, "spouse_age", _age_val, disabled=_is_single
        )
        _spouse_has_workplace_plan = container.checkbox(
            "Spouse has a workplace retirement plan (401k/403b)",
            value=_wp_val,
            disabled=_is_single,
        )
        _spouse_rmd_stored = st.session_state.get("spouse_rmd_start_age")
        if _spouse_rmd_stored is not None and _spouse_rmd_stored not in {73, 75}:
            container.warning(
                f"Stored spouse RMD start age {_spouse_rmd_stored} is not valid "
                "(must be 73 or 75); falling back to 75."
            )
        _spouse_rmd_choice = container.selectbox(
            "Spouse RMD start age",
            options=[None, 73, 75],
            index=_rmd_option_index(st.session_state, "spouse"),
            format_func=lambda o: (
                f"{_RMD_AUTO_LABEL}: {default_rmd_age(hh.base_year - _spouse_age)}"
                if o is None
                else _rmd_option_label(o)
            ),
            help="Auto follows spouse's birth year: 73 if born 1951-1959, 75 if born 1960+ "
            "(SECURE 2.0 §107). Pick 73 or 75 only to override.",
            disabled=_is_single,
        )
        _spouse_defer_first_rmd = container.checkbox(
            "Defer spouse's first RMD to April 1 (two RMDs in year 2)",
            value=hh.spouse_defer_first_rmd,
            help=(
                "IRC §401(a)(9)(C)(i): delay the spouse's first RMD to April 1 of the "
                "following year. The deferred RMD then stacks on year 2's RMD — may push "
                "a tax bracket or IRMAA tier."
            ),
            disabled=_is_single,
        )
        _spouse_is_sole_beneficiary = container.checkbox(
            "Spouse is sole IRA beneficiary and >10 yrs younger (use IRS Joint & "
            "Last Survivor Table for RMDs)",
            value=hh.spouse_is_sole_beneficiary,
            help=(
                "26 CFR §1.401(a)(9)-9 Table II: when your sole primary IRA "
                "beneficiary is a spouse more than 10 years younger, the IRS "
                "requires this larger-divisor table instead of the standard "
                "Uniform Lifetime Table — producing a smaller RMD. Only applies "
                "when the age gap qualifies; otherwise the standard table is used."
            ),
            disabled=_is_single,
        )
        _spouse_fra_age = container.number_input(
            "Spouse FRA (Full Retirement Age)",
            min_value=65,
            max_value=70,
            value=_clamp(hh.spouse_fra_age, 65, 70),
            step=1,
            format="%d",
            help="67 for born 1960+ (SECURE/SS default); 66 or 66+N/12 for earlier cohorts",
            disabled=_is_single,
        )
        _spouse_aca = container.checkbox(
            "Spouse on ACA Marketplace",
            value=_aca_val,
            help="Check if spouse is enrolled in ACA marketplace",
            disabled=_is_single,
        )
        # audit-1008 F-32: when Single, ``hh`` carries apply_single_filer's ZEROED
        # spouse fields, so these disabled widgets display zeros. Writing them back
        # would overwrite the user's stored spouse settings and lose them on a
        # toggle back to MFJ -- the zeroing belongs to the derived Household only
        # (views/setup/parameters.py:apply_single_filer).
        if not _is_single:
            st.session_state.spouse_age = _spouse_age
            st.session_state.spouse_has_workplace_plan = _spouse_has_workplace_plan
            rmd_choice_write_back(st.session_state, "spouse", _spouse_rmd_choice)
            st.session_state.spouse_defer_first_rmd = _spouse_defer_first_rmd
            st.session_state.spouse_is_sole_beneficiary = _spouse_is_sole_beneficiary
            st.session_state.spouse_fra_age = _spouse_fra_age
            st.session_state.spouse_aca = _spouse_aca
        return None

    raise ValueError(f"Unknown owner slice: {owner!r}")
