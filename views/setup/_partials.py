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

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from engine.data_bridge_browser import is_pyodide
from engine.data_sources.candidate_store import Candidate, CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed, save_committed
from engine.data_sources.confirm import confirm_field
from engine.data_sources.orchestrator import session_keys_for_writeback
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from engine.data_sources.record import record_ss_fra_candidate
from engine.data_sources.resolver import GRANTS_KEY, HOUSEHOLD_SCALAR_FIELDS
from engine.portfolio_sync import (
    AccountSummary,
    PortfolioSnapshot,
    fetch_ssa_snapshot,
    match_fra_estimate,
    save_ssa_snapshot,
)
from models.household import Household
from models.sourced import Source

_HH_FILING_LABEL_MFJ = "Married filing jointly"
_HH_FILING_LABEL_SINGLE = "Single"

_MAGI_PREFIX = "prior_year_magi."

_FIELD_LABELS: dict[str, str] = {
    "your_ira": "Your IRA balance",
    "spouse_ira": "Spouse IRA balance",
    "your_roth": "Your Roth balance",
    "spouse_roth": "Spouse Roth balance",
    "txn_price_now": "Stock price",
    "your_ss_fra": "Your SS at FRA ($/mo)",
    "spouse_ss_fra": "Spouse SS at FRA ($/mo)",
    GRANTS_KEY: "Option grants",
}


def _field_label(field_key: str) -> str:
    """Human-readable label for a sourced field key."""
    if field_key.startswith(_MAGI_PREFIX):
        return f"Prior-year MAGI ({field_key[len(_MAGI_PREFIX) :]})"
    return _FIELD_LABELS.get(field_key, field_key)


def _format_value(field_key: str, value: Any) -> str:
    """Format a candidate/committed value for display."""
    if field_key == GRANTS_KEY:
        return "no data" if value is None else f"{len(value)} grants"
    if value is None:
        return "no data"
    return f"${float(value):,.2f}"


def _committed_value_and_source(committed_json: dict, field_key: str) -> tuple[Any, str | None]:
    """Return (current committed value, source label) for ``field_key``."""
    if field_key.startswith(_MAGI_PREFIX):
        year = field_key[len(_MAGI_PREFIX) :]
        payload = committed_json.get("prior_year_magi") or {}
        value = payload.get("data", {}).get(year)
        source = payload.get("prov", {}).get(year, {}).get("source")
        return value, source
    if field_key == GRANTS_KEY:
        payload = committed_json.get(GRANTS_KEY) or {}
        data = payload.get("data")
        prov_list = payload.get("prov") or []
        source = prov_list[0].get("source") if prov_list else None
        return data, source
    payload = committed_json.get(field_key) or {}
    return payload.get("value"), payload.get("source")


def _render_candidate_row(candidate: Candidate, field_key: str) -> None:
    """Render one candidate's source/value/detail row; never raises."""
    try:
        value_str = _format_value(field_key, candidate.value)
        recorded = candidate.prov.recorded_at.isoformat(timespec="seconds")
        st.write(
            f"- **{candidate.prov.source}**: {value_str} "
            f"— {candidate.prov.detail or '(no detail)'} — recorded {recorded}"
        )
    except (TypeError, ValueError, AttributeError) as exc:
        source = getattr(getattr(candidate, "prov", None), "source", "?")
        st.caption(f"⚠️ rejected: candidate from {source} — {exc}")


def _resolve_confirm_choice(
    candidates: list[Candidate], chosen_source: Source | None, manual_value: float
) -> tuple[Any, Source, str] | None:
    """Return (value, source, detail) to confirm, or None if nothing was chosen."""
    if manual_value:
        return manual_value, Source.MANUAL, "manual entry"
    if chosen_source is not None:
        for candidate in candidates:
            if candidate.prov.source == chosen_source:
                return candidate.value, candidate.prov.source, candidate.prov.detail
    return None


def _apply_confirm_to_session(field_key: str, value: Any) -> None:
    """Keep session_state in sync so reconcile_manual_edits doesn't revert the confirm.

    Uses the shared field->session_key alias map (txn_price_now is aliased to
    "txn_price") — writing under the raw field_key here previously left
    session_state["txn_price"] stale, so the next reconcile saw a diff and
    reverted the confirm. Session-mirror values are int, not float: every
    Setup number_input bound to these keys uses format="%d"/int
    min_value/step, so a float here would raise
    StreamlitMixedNumericTypesError on the next Setup render.
    """
    if field_key in HOUSEHOLD_SCALAR_FIELDS:
        session_key = session_keys_for_writeback().get(field_key, field_key)
        st.session_state[session_key] = int(round(value))
    elif field_key.startswith(_MAGI_PREFIX):
        year = int(field_key[len(_MAGI_PREFIX) :])
        magi = dict(st.session_state.get("prior_year_magi") or {})
        magi[year] = float(value)
        st.session_state["prior_year_magi"] = magi
    # grants: no direct session_state representation exists today — grants are
    # re-derived live from portfolio_snapshot + strikes on every render — so
    # this is a best-effort no-op. Revisit in a future wave if that changes.
    st.session_state.get("_pending_review", set()).discard(field_key)


def _handle_confirm_click(
    field_key: str,
    committed_json: dict,
    choices: ChoiceMap,
    candidates: list[Candidate],
    chosen_source: Source | None,
    manual_value: float,
) -> bool:
    """Process a Confirm click; returns True if a value was actually confirmed."""
    picked = _resolve_confirm_choice(candidates, chosen_source, manual_value)
    if picked is None:
        st.warning("No candidate or manual value to confirm.")
        return False
    value, source, detail = picked

    confirm_field(committed_json, choices, field_key, value, source, datetime.now(), detail=detail)
    save_committed(COMMITTED_PATH, committed_json)
    choices.save(TRUST_CHOICES_PATH)
    _apply_confirm_to_session(field_key, value)
    st.success(f"Confirmed {_field_label(field_key)} from {source}.")
    return True


def _render_field_card(
    field_key: str, committed_json: dict, store: CandidateStore, choices: ChoiceMap
) -> None:
    """Render one pending-review card; defensive — never crashes the gate.

    Shared by every owning partial's inline sourced-field governance UI
    (Accounts here; Options in Task 5; Assumptions in Task 7) — moved from
    ``views/setup/command_center.py``'s old generic per-pending-field loop
    (removed in Task 4; see that module's docstring for why).
    """
    try:
        candidates = store.candidates_for(field_key)
        committed_value, committed_source = _committed_value_and_source(committed_json, field_key)

        st.subheader(_field_label(field_key))
        st.caption(
            f"Currently committed: {_format_value(field_key, committed_value)} "
            f"(source: {committed_source or 'none'})"
        )
        for candidate in candidates:
            _render_candidate_row(candidate, field_key)

        source_options = [c.prov.source for c in candidates]
        chosen_source = (
            st.radio(
                "Trust which source?",
                source_options,
                key=f"trust_{field_key}",
                format_func=str,
                horizontal=True,
            )
            if source_options
            else None
        )
        manual_value = 0.0
        if field_key != GRANTS_KEY:
            manual_value = st.number_input(
                "Or enter manually (0 = use the selected source above)",
                key=f"manual_{field_key}",
                value=0.0,
                step=1000.0,
            )

        if st.button("Confirm", key=f"confirm_{field_key}"):
            confirmed = _handle_confirm_click(
                field_key, committed_json, choices, candidates, chosen_source, manual_value
            )
            if confirmed:
                st.rerun()
    except Exception as exc:  # noqa: BLE001 - defensive UI guard; a malformed card must not crash
        st.warning(f"⚠️ rejected: {field_key} — {exc}")


def _sync_ssa_for(owner: str, fra_age: int) -> str | None:
    """Fetch, match, and record the FRA SSA benefit for *owner* ('you' or 'spouse').

    Records the matched monthly benefit as a FINEXTRACT_LIVE candidate
    (engine.data_sources.record.record_ss_fra_candidate) instead of writing
    directly to session_state — the value sits pending until confirmed via
    the Setup / Command Center review gate (same freeze-until-confirm seam
    as your_ira/your_roth/txn_price_now). Also caches the raw snapshot.
    Returns a warning message on failure/no-match, or None on success.
    """
    snap = fetch_ssa_snapshot()
    if snap.error:
        return f"SSA sync failed: {snap.error}"
    match = match_fra_estimate(snap.estimates, fra_age)
    if match is None:
        return "No SSA benefit estimate found near the configured FRA age; sync skipped."
    field_key = "your_ss_fra" if owner == "you" else "spouse_ss_fra"
    record_ss_fra_candidate(
        field_key, match.monthly_amount, Source.FINEXTRACT_LIVE, "SSA statement", datetime.now()
    )
    st.session_state[f"ssa_snapshot_{owner}"] = snap
    save_ssa_snapshot(snap, owner=owner)
    return None


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
                "(Joint sub-tab)."
            ),
        )
        _is_single = filing_status_from_label(_filing_choice) == "Single"
        st.session_state["filing_status"] = "Single" if _is_single else "MFJ"
        return _is_single

    if owner == "your":
        st.session_state.your_age = container.number_input(
            "Your Age",
            value=hh.your_age,
            step=1,
            format="%d",
        )
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
        st.session_state.your_rmd_start_age = container.selectbox(
            "Your RMD start age",
            options=[73, 75],
            index=0 if hh.your_rmd_start_age == 73 else 1,
            help="73 if born 1951-1959 (SECURE 2.0 §107); 75 if born 1960+ (SECURE 2.0 §107)",
        )
        st.session_state.your_defer_first_rmd = container.checkbox(
            "Defer first RMD to April 1 (two RMDs in year 2)",
            value=hh.your_defer_first_rmd,
            help=(
                "IRC §401(a)(9)(C)(ii): delay the first RMD to April 1 of the following year. "
                "The deferred RMD then stacks on year 2's RMD — may push a tax bracket or IRMAA tier."
            ),
        )
        st.session_state.your_fra_age = container.number_input(
            "Your FRA (Full Retirement Age)",
            min_value=65,
            max_value=70,
            value=hh.your_fra_age,
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
        _is_single = st.session_state.get("filing_status", "MFJ") == "Single"
        st.session_state.spouse_age = container.number_input(
            "Spouse Age",
            value=hh.spouse_age,
            step=1,
            format="%d",
            disabled=_is_single,
        )
        st.session_state.spouse_has_workplace_plan = container.checkbox(
            "Spouse has a workplace retirement plan (401k/403b)",
            value=hh.spouse_has_workplace_plan,
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
            index=0 if hh.spouse_rmd_start_age == 73 else 1,
            help="73 if born 1951-1959 (SECURE 2.0 §107); 75 if born 1960+ (SECURE 2.0 §107)",
            disabled=_is_single,
        )
        st.session_state.spouse_defer_first_rmd = container.checkbox(
            "Defer spouse's first RMD to April 1 (two RMDs in year 2)",
            value=hh.spouse_defer_first_rmd,
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
        st.session_state.spouse_fra_age = container.number_input(
            "Spouse FRA (Full Retirement Age)",
            min_value=65,
            max_value=70,
            value=hh.spouse_fra_age,
            step=1,
            format="%d",
            help="67 for born 1960+ (SECURE/SS default); 66 or 66+N/12 for earlier cohorts",
            disabled=_is_single,
        )
        st.session_state.spouse_aca = container.checkbox(
            "Spouse on ACA Marketplace",
            value=hh.spouse_aca_enrolled,
            help="Check if spouse is enrolled in ACA marketplace",
            disabled=_is_single,
        )
        return None

    raise ValueError(f"Unknown owner slice: {owner!r}")


def render_accounts_partial(hh: Household, container, owner: str) -> None:
    """Render one owner's IRA/Roth/SS-FRA accounts widgets plus, inline right
    after each field's own balance widget, that field's trust/manual-
    override/confirm governance card if a sourced candidate is pending.

    ``owner`` is ``"your"`` or ``"spouse"`` — every field here is per-person
    (unlike ``render_household_partial``, there is no ``"joint"`` case).

    ``your_ira``/``your_roth``/``your_ss_fra`` (and the spouse equivalents)
    are all in ``HOUSEHOLD_SCALAR_FIELDS`` — Command Center's governed
    sourced fields — so each renders its card here instead of the old
    generic per-pending-field loop that used to live in
    ``views/setup/command_center.py``. That loop was REMOVED (not filtered)
    in this same task: Classic mode's ``st.tabs()`` executes every tab's
    body every script run regardless of which tab is visually selected, so
    rendering the same ``trust_<field>``/``manual_<field>``/
    ``confirm_<field>`` widget key from both that loop AND here in one run
    would raise ``DuplicateWidgetID`` (see
    ``tests/test_setup_shell_characterization.py``'s Task-4 regression
    test). SS-start-age (``your_ss_start_age``/``spouse_ss_start_age``) is a
    plain scalar, not governed/sourced, but stays co-located with SS-FRA
    per the plan.

    ``value=`` for SS-start-age reads from ``hh.<attr>`` (see
    ``render_household_partial``'s docstring for the general rule) —
    ``Household.__post_init__`` never derives it, so ``hh.<attr>`` always
    equals the live ``session_state.<attr>``. The 6 governed IRA/Roth/SS-FRA
    fields are the DOCUMENTED EXCEPTION to that rule: by the time this
    partial runs, ``hh`` is ``app.py get_household()``'s POST-RESOLVE
    household (``app_res.result.household``), whose governed fields are
    ``SourcedValue`` (a ``float`` subclass carrying provenance) rather than
    a plain ``float``/``int`` — Streamlit's ``number_input`` does an exact
    ``type(value) in (int, float)`` check, so passing ``hh.your_ira``
    directly raises ``StreamlitMixedNumericTypesError`` even though it's
    numerically a float. ``get_household()`` mirrors the resolved value back
    into ``session_state`` (int-coerced) before this partial ever runs, so
    these 6 widgets read ``st.session_state.<attr>`` instead — matching
    their exact pre-Task-4 shape.
    """
    pending: set[str] = st.session_state.get("_pending_review", set())
    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    committed_json = load_committed(COMMITTED_PATH) or {}

    def _maybe_card(field_key: str) -> None:
        if field_key not in pending:
            return
        with container.container(border=True):
            _render_field_card(field_key, committed_json, store, choices)

    if owner == "your":
        _synced = bool(st.session_state.get("portfolio_snapshot"))
        st.session_state.your_ira = container.number_input(
            "Your Trad IRA" + (" (synced)" if _synced else ""),
            min_value=0,
            value=st.session_state.your_ira,
            step=50_000,
            format="%d",
            disabled=_synced,
            help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
        )
        _maybe_card("your_ira")

        st.session_state.your_roth = container.number_input(
            "Your Roth IRA" + (" (synced)" if _synced else ""),
            min_value=0,
            value=st.session_state.get("your_roth", 0),
            step=50_000,
            format="%d",
            disabled=_synced,
            help="Auto-synced from FinExtract (Roth IRA)" if _synced else None,
        )
        _maybe_card("your_roth")

        _ssa_synced_you = bool(st.session_state.get("ssa_snapshot_you"))
        your_fra_age = st.session_state.get("your_fra_age", 67)
        st.session_state.your_ss_fra = container.number_input(
            f"Your SS at FRA {your_fra_age} ($/mo)" + (" (synced)" if _ssa_synced_you else ""),
            min_value=0,  # UU2-UI-06
            value=int(round(st.session_state.your_ss_fra)),
            step=100,
            format="%d",
            disabled=_ssa_synced_you,
            help="Auto-synced from FinExtract (SSA benefit estimate)" if _ssa_synced_you else None,
        )
        _maybe_card("your_ss_fra")
        if container.button("Sync SS from FinExtract", key="_sync_ssa_you_btn"):
            _warning = _sync_ssa_for("you", your_fra_age)
            if _warning:
                container.warning(_warning)
            else:
                st.rerun()
        st.session_state.your_ss_start_age = container.number_input(
            "Your SS claim age",
            min_value=62,
            max_value=70,
            value=hh.your_ss_start_age,
            step=1,
            format="%d",
        )
        return

    if owner == "spouse":
        _is_single = st.session_state.get("filing_status", "MFJ") == "Single"
        _synced = bool(st.session_state.get("portfolio_snapshot"))
        st.session_state.spouse_ira = container.number_input(
            "Spouse Trad IRA" + (" (synced)" if _synced else ""),
            min_value=0,
            value=st.session_state.spouse_ira,
            step=50_000,
            format="%d",
            disabled=_synced or _is_single,
            help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
        )
        _maybe_card("spouse_ira")

        st.session_state.spouse_roth = container.number_input(
            "Spouse Roth IRA" + (" (synced)" if _synced else ""),
            min_value=0,
            value=st.session_state.get("spouse_roth", 0),
            step=50_000,
            format="%d",
            disabled=_synced or _is_single,
            help="Auto-synced from FinExtract (Roth IRA)" if _synced else None,
        )
        _maybe_card("spouse_roth")

        _ssa_synced_spouse = bool(st.session_state.get("ssa_snapshot_spouse"))
        spouse_fra_age = st.session_state.get("spouse_fra_age", 67)
        st.session_state.spouse_ss_fra = container.number_input(
            f"Spouse SS at FRA {spouse_fra_age} ($/mo)"
            + (" (synced)" if _ssa_synced_spouse else ""),
            min_value=0,  # UU2-UI-06
            value=int(round(st.session_state.spouse_ss_fra)),
            step=100,
            format="%d",
            disabled=_is_single or _ssa_synced_spouse,
            help="Auto-synced from FinExtract (SSA benefit estimate)"
            if _ssa_synced_spouse
            else None,
        )
        _maybe_card("spouse_ss_fra")
        if container.button("Sync SS from FinExtract", key="_sync_ssa_spouse_btn", disabled=_is_single):
            _warning = _sync_ssa_for("spouse", spouse_fra_age)
            if _warning:
                container.warning(_warning)
            else:
                st.rerun()
        st.session_state.spouse_ss_start_age = container.number_input(
            "Spouse SS claim age",
            min_value=62,
            max_value=70,
            value=hh.spouse_ss_start_age,
            step=1,
            format="%d",
            disabled=_is_single,
        )
        return

    raise ValueError(f"Unknown owner slice: {owner!r}")


def _no_data_msg(noun: str) -> str:
    """Return an empty-state message adapted for the current runtime environment."""
    if is_pyodide():
        return f"No {noun} loaded — upload a data file in ⚙️ Setup → \U0001f517 Data Bridge."
    return f"No {noun} loaded — use the Sync button below (local install) or upload a data file."


def _render_accounts_table(accounts: list[AccountSummary], container, *, show_owner: bool) -> None:
    """Render a read-only accounts dataframe, or an info banner when empty.

    ``container`` is threaded through (not hard-coded to ``st``) so callers
    can pass a tab/expander sub-container — see ``_render_portfolio_sub_tabs``.
    """
    if not accounts:
        container.info(_no_data_msg("accounts"))
        return
    rows = [
        {
            "account_name": a.account_name,
            "type": a.account_type,
            "market_value": a.total_value,
            **({"owner": a.owner} if show_owner else {}),
        }
        for a in accounts
    ]
    container.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "market_value": st.column_config.NumberColumn("Market Value", format="$%,.0f"),
        },
    )


def _render_holdings_table(accounts: list[AccountSummary], container) -> None:
    """Render a read-only holdings dataframe across the given accounts.

    ``container`` is threaded through (not hard-coded to ``st``) so callers
    can pass a tab/expander sub-container — see ``_render_portfolio_sub_tabs``.
    """
    rows = [
        {
            "symbol": h.symbol,
            "account": h.account_name,
            "asset_class": h.asset_class,
            "quantity": h.quantity,
            "market_value": h.market_value,
        }
        for a in accounts
        for h in a.holdings
    ]
    if not rows:
        container.info(_no_data_msg("holdings"))
        return
    container.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "quantity": st.column_config.NumberColumn("Quantity", format="%,.0f"),
            "market_value": st.column_config.NumberColumn("Market Value", format="$%,.0f"),
        },
    )


def _render_portfolio_sub_tabs(
    snap: PortfolioSnapshot | None,
    container,
) -> None:
    """Render Me / Spouse / All sub-tabs for the Portfolio tab.

    ``container.tabs(...)`` (not ``st.tabs(...)``) so the tabs themselves
    nest correctly inside whatever container the caller hands us (Task 8
    may drop ``render_portfolio_partial`` inside an outer expander/tab).
    Content inside each returned tab uses that tab object directly (not
    bare ``st.``) for the same reason.
    """
    me_tab, spouse_tab, all_tab = container.tabs(["Me", "Spouse", "All"])

    if snap is None:
        for tab in (me_tab, spouse_tab, all_tab):
            tab.info(_no_data_msg("accounts"))
        return

    me_accounts = [a for a in snap.accounts if a.owner == "you"]
    spouse_accounts = [a for a in snap.accounts if a.owner == "spouse"]

    me_tab.subheader("Accounts")
    _render_accounts_table(me_accounts, me_tab, show_owner=False)
    me_tab.subheader("Holdings")
    _render_holdings_table(me_accounts, me_tab)

    spouse_tab.subheader("Accounts")
    _render_accounts_table(spouse_accounts, spouse_tab, show_owner=False)
    spouse_tab.subheader("Holdings")
    _render_holdings_table(spouse_accounts, spouse_tab)

    all_tab.subheader("Accounts")
    _render_accounts_table(snap.accounts, all_tab, show_owner=True)
    all_tab.subheader("Holdings")
    _render_holdings_table(snap.accounts, all_tab)


def _render_account_type_overrides(snap: PortfolioSnapshot | None, container) -> None:
    """Render the Account Type Overrides expander.

    ``container.expander(...)`` (not ``st.expander(...)``) so the expander
    itself nests correctly inside whatever container the caller hands us;
    content inside uses the expander object directly (not bare ``st.``).
    """
    from engine.portfolio_sync import (
        _classify_account,
        _resolve_override,
    )

    expander = container.expander("🏷️ Account Type Overrides")
    if snap is None or not snap.accounts:
        expander.info("No accounts loaded — sync first to see detected acctIds.")
        return

    expander.caption("Changes take effect on next sync.")
    _type_options = ["trad_ira", "roth_ira", "brokerage", "hsa", "403b"]
    _owner_options = ["you", "spouse"]
    overrides: dict[str, str | dict[str, str]] = st.session_state.get("account_type_overrides") or {}

    seen: set[str] = set()
    for acct in snap.accounts:
        acct_id = acct.account_name
        if acct_id in seen:
            continue
        seen.add(acct_id)

        auto_type, _ = _classify_account(acct_id)
        existing = overrides.get(acct_id)
        if existing is not None:
            current_type, current_owner = _resolve_override(existing)
            if not current_type:
                current_type = auto_type
        else:
            current_type, current_owner = auto_type, "you"
        try:
            type_idx = _type_options.index(current_type)
        except ValueError:
            type_idx = 0
        try:
            owner_idx = _owner_options.index(current_owner)
        except ValueError:
            owner_idx = 0

        col_id, col_auto, col_type, col_owner = expander.columns([3, 2, 2, 2])
        col_id.text(acct_id)
        col_auto.caption(f"auto: {auto_type}")
        chosen_type = col_type.selectbox(
            "Type",
            _type_options,
            index=type_idx,
            key=f"_override_type_{acct_id}",
            label_visibility="collapsed",
        )
        chosen_owner = col_owner.selectbox(
            "Owner",
            _owner_options,
            index=owner_idx,
            key=f"_override_owner_{acct_id}",
            label_visibility="collapsed",
        )
        # Write through the nested form so owner is persisted alongside type.
        if "account_type_overrides" not in st.session_state:
            st.session_state["account_type_overrides"] = {}
        st.session_state["account_type_overrides"][acct_id] = {
            "type": chosen_type,
            "owner": chosen_owner,
        }


def render_portfolio_partial(hh: Household, container) -> None:
    """Render the Portfolio tab's FinExtract sync button, read-only
    accounts/holdings tables, and the Account Type Overrides expander.

    Moved verbatim from ``views/setup/portfolio.py:render_portfolio_tab``
    (Task 6 of the ui-shell-theme-toggle plan) — the rest of that function's
    body (the equity-grants table + stock-price widget) was already
    extracted into ``render_options_partial`` in Task 5.

    None of these fields are Command-Center-governed sourced fields (no
    ``HOUSEHOLD_SCALAR_FIELDS`` entry), so unlike ``render_accounts_partial``/
    ``render_options_partial`` there is no inline trust/manual/confirm
    governance card here: the accounts/holdings tables are pure read-only
    display, and the balances the sync button fetches get their own
    governance cards elsewhere (``render_accounts_partial``/
    ``render_options_partial``) on the NEXT render, once
    ``app.get_household()`` records the freshly-saved snapshot as
    FINEXTRACT_LIVE candidates — unchanged from pre-Task-6 behavior.

    ``sync_portfolio_from_finextract`` is imported locally (deferred, not at
    module level) to avoid a circular import: ``views/setup/portfolio.py``
    imports ``render_portfolio_partial``/``render_options_partial`` from this
    module at module level, so a module-level import back from here would
    form an import cycle. Mirrors the same deferred-import pattern already
    used by ``views/_shared.py:_sync_portfolio_source``.
    """
    snap: PortfolioSnapshot | None = st.session_state.get("portfolio_snapshot")

    # FinExtract availability note — local install only (not available on Pyodide/stlite)
    if is_pyodide():
        container.info(
            "FinExtract sync isn't available on the public site — browsers block "
            "the HTTPS page from reaching your local FinExtract server "
            "(http://127.0.0.1:7890), no matter how it's running. "
            "Bring in real data instead via ⚙️ Setup → \U0001f517 Data Bridge "
            "(encrypted upload from a local install)."
        )
    else:
        container.caption(
            "FinExtract sync works here because the app is running locally "
            "(`pixi run app`), with your local FinExtract server running."
        )

    if not is_pyodide():
        from views.setup.portfolio import sync_portfolio_from_finextract

        _sync = container.button(
            "Sync from FinExtract", help="Pull live holdings from ingestion server"
        )
        if snap is not None:
            container.caption(
                f"Loaded: {len(snap.accounts)} accounts, {len(snap.equity_grants)} grants"
            )

        if _sync:
            # NOTE: synced balances (your_ira/spouse_ira/your_roth/spouse_roth)
            # are deliberately NOT written to session_state here. get_household()
            # records this snapshot as FINEXTRACT_LIVE candidates and arbitrates
            # them through the freeze-until-confirm gate (Setup ▸ Command
            # Center) — a direct write here bypassed that gate (audit defect).
            outcome = sync_portfolio_from_finextract(hh)
            snap = outcome.snap
            if snap.server_available:
                container.success(
                    f"Synced: {len(snap.accounts)} accounts, "
                    f"{len(snap.equity_grants)} active grants"
                    + (", YTD income" if outcome.ytd_synced else "")
                    + (", dividend history" if outcome.dividend_history_synced else "")
                    + (", option exercises" if outcome.option_exercises_synced else "")
                )
            else:
                container.error(f"Server unavailable: {snap.error}")
                snap = st.session_state.get("portfolio_snapshot")

    _render_portfolio_sub_tabs(snap, container)
    _render_account_type_overrides(snap, container)


def render_options_partial(hh: Household, container) -> None:
    """Render the Options (Stock Grants) partial: the read-only equity-grants
    table plus the ``txn_price_now`` stock-price input, each with its own
    inline trust/manual-override/confirm governance card when a candidate is
    pending.

    Note: ``hh`` parameter is unused in this function's body; it is retained
    for interface parity with ``render_household_partial`` and
    ``render_accounts_partial``, which do use their ``hh`` argument. This
    consistency enables uniform ``(hh, container)`` call signatures across
    all Setup-domain partials for Task 8's shell composition.

    Unlike ``render_household_partial``/``render_accounts_partial``, this
    partial takes no ``owner`` argument — grants and the stock price are
    household-level, not per-person.

    The equity-grants table (moved from ``views/setup/portfolio.py``'s old
    ``_render_grants_section``, formerly rendered once per Me/All Portfolio
    sub-tab) reads ``st.session_state["portfolio_snapshot"]`` directly rather
    than taking it as a parameter — same internal-session_state-read
    convention ``render_accounts_partial`` already uses for its "(synced)"
    badge. Consolidated to render exactly ONCE here instead of twice (Me tab
    + All tab) since ``GRANTS_KEY``'s governance card below has explicit
    widget keys (``trust_grants``/``manual_grants``/``confirm_grants``) that
    would raise ``DuplicateWidgetID`` if rendered from two call sites in the
    same script run — this dedup, and the txn_price relocation described
    next, are a single deliberate Task 5 decision (NOT an application of
    Task 3's reordering exception, which was scoped only to minor same-tab
    cosmetic reordering and does not cover either of these changes — see
    below).

    ``txn_price_now`` is a Command-Center-governed sourced field (one of
    ``HOUSEHOLD_SCALAR_FIELDS``) aliased to the ``"txn_price"`` session key
    (see ``session_keys_for_writeback``/``_apply_confirm_to_session``'s
    docstring for why) — the widget reads/writes
    ``st.session_state.txn_price`` (not ``hh.txn_price_now``, which is a
    ``SourcedValue`` post-resolve and would raise
    ``StreamlitMixedNumericTypesError``), moved (same unkeyed
    controlled-widget shape) from ``views/setup/parameters.py``'s Joint
    sub-tab to here, co-located with the stock-grants table it prices.

    This is a cross-tab, user-visible Classic-mode layout change (Parameters
    -> Joint to Portfolio), which exceeds Task 5's literal text. A
    2026-07-24 spec-compliance review of commit 19e04f69 flagged it as such
    and flagged this docstring's prior (incorrect) citation of "Task 3's
    accepted-reordering exception" as not actually covering a cross-tab
    move. The project owner reviewed and explicitly APPROVED it the same day
    as a deliberate Task 5 design decision: all Options-domain fields
    (equity grants + the stock price that prices them) consolidate into
    exactly one call site, rendered from the Portfolio tab, going forward.
    See ``tests/test_setup_options_partial.py`` for the regression test that
    pins the Stock Price widget to the Portfolio tab (and asserts its
    absence from Parameters -> Joint).

    ``GRANTS_KEY``'s own governance card has no manual-override
    ``number_input`` (see ``_render_field_card``'s ``field_key != GRANTS_KEY``
    branch) and confirming it has no session_state mirror to update (see
    ``_apply_confirm_to_session``'s "grants: no direct session_state
    representation" note) — both pre-existing behaviors, unchanged here.
    """
    pending: set[str] = st.session_state.get("_pending_review", set())
    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    committed_json = load_committed(COMMITTED_PATH) or {}

    def _maybe_card(field_key: str) -> None:
        if field_key not in pending:
            return
        with container.container(border=True):
            _render_field_card(field_key, committed_json, store, choices)

    container.subheader("Stock Grants")
    snap = st.session_state.get("portfolio_snapshot")
    grants = snap.equity_grants if snap is not None else []
    if not grants:
        container.info("No grants loaded.")
    else:
        rows = [
            {
                "grant_id": g.grant_id,
                "type": g.grant_type,
                "grant_date": g.grant_date,
                "shares_granted": g.shares_granted,
                "outstanding": g.outstanding,
                "current_value": g.current_value,
            }
            for g in grants
        ]
        container.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            column_config={
                "current_value": st.column_config.NumberColumn("Current Value", format="$%,.0f"),
            },
        )
        container.caption(
            "Grant owner attribution is not yet available from FinExtract — "
            "all grants are shown here."
        )
    _maybe_card(GRANTS_KEY)

    container.subheader("Stock Price")
    st.session_state.txn_price = container.number_input(
        f"{st.session_state.get('_stock_ticker', 'Stock')} Current Price",
        min_value=0,
        value=st.session_state.txn_price,
        step=5,
        format="%d",
    )
    _maybe_card("txn_price_now")
