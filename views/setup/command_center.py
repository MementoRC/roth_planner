"""Setup ▸ Command Center — the review/confirm gate for sourced fields.

Surfaces every field flagged pending review (``st.session_state["_pending_review"]``,
populated by ``engine.data_sources.resolver.resolve()`` via ``app.py``'s
``get_household()``) and lets the user confirm which source to trust. No
committed value changes as a result of this page without passing through
``engine.data_sources.confirm.confirm_field`` — the sole mutation path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import streamlit as st

from engine.data_sources.candidate_store import Candidate, CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed, save_committed
from engine.data_sources.confirm import confirm_field
from engine.data_sources.orchestrator import session_keys_for_writeback
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from engine.data_sources.resolver import GRANTS_KEY, HOUSEHOLD_SCALAR_FIELDS
from models.household import Household
from models.sourced import Source
from views._shared import SyncEverythingResult, sync_everything

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
    """Render one pending-review card; defensive — never crashes the gate."""
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


def _format_sync_everything_summary(summary: SyncEverythingResult) -> str:
    """Render a one-line "portfolio: N · SS: N · scan: N files, N errors" summary."""
    portfolio = f"portfolio: {summary.portfolio.candidates_recorded} candidates"
    if summary.portfolio.error:
        portfolio += f" (unavailable: {summary.portfolio.error})"

    ss = f"SS: {summary.ss.candidates_recorded} candidates"
    if summary.ss.warnings:
        ss += f" ({'; '.join(summary.ss.warnings)})"

    if summary.scan.result is not None:
        scan = (
            f"scan: {summary.scan.result.files_scanned} files, "
            f"{len(summary.scan.result.errors)} errors"
        )
    else:
        scan = f"scan: skipped ({summary.scan.error})"

    return " · ".join([portfolio, ss, scan])


def render_command_center(hh: Household) -> None:
    """Render the Setup ▸ Command Center review/confirm gate."""
    st.header("🎛️ Command Center")

    if st.button("⟳ Sync everything", key="sync_everything_btn"):
        with st.spinner("Syncing all sources…"):
            summary = sync_everything(hh)
        st.info(_format_sync_everything_summary(summary))

    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    committed_json = load_committed(COMMITTED_PATH) or {}
    pending: set[str] = st.session_state.get("_pending_review", set())

    st.metric("Fields awaiting review", len(pending))

    if not pending:
        st.success("All data sources reconciled ✓")
        return

    for field_key in sorted(pending):
        with st.container(border=True):
            _render_field_card(field_key, committed_json, store, choices)
