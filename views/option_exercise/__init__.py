"""Option Exercise Planner — editable per-grant/per-year NQO exercise schedule.

Exercise timing and share count are a free decision variable: this page lets
the user tune how many shares of which grant to exercise in which year, and
shows the resulting per-year TXN price row, a live "Remaining" readout, a
read-only dollar mirror grid (spread income by tax year), and inline
validation. Persists via ``engine.exercise_schedule_store``.

Supports Classic (unchanged) and Domains (2-tab: Edit Allocation / Review
Impact) layouts via the theme-aware ``render(hh, theme)`` dispatcher,
mirroring the YTD Income pilot (Phase 3). Computation lives entirely in
``_partials/`` — this module owns only the Streamlit dispatch.

Module-attribute indirection: handle_txn_quote_fetch, save_exercise_schedule,
and clear_exercise_schedule are imported here (not just in _partials/) so
that _partials submodules can resolve them via `import views.option_exercise
as _option_exercise` at call time — this keeps existing test monkeypatches
(monkeypatch.setattr(views.option_exercise, "<name>", ...)) working across
the partial-extraction refactor. Do not remove these imports/re-exports
even though this module no longer calls them directly itself.
"""

from __future__ import annotations

import streamlit as st

from engine.data_status import compute_exercise_completeness
from engine.exercise_schedule_store import clear_exercise_schedule, save_exercise_schedule
from models.exercise_schedule import ExerciseSchedule
from models.household import Household
from views.option_exercise._partials import (
    handle_txn_quote_fetch,
    render_grid_partial,
    render_price_basis_partial,
    render_review_partial,
    render_validate_save_partial,
)

__all__ = [
    "clear_exercise_schedule",
    "handle_txn_quote_fetch",
    "render",
    "save_exercise_schedule",
]


def render(hh: Household, theme: str | None = None) -> None:
    st.title("Option Exercise Planner")
    st.caption(
        "Choose how many shares of each grant to exercise in which year — "
        "a lever for filling tax brackets and timing Roth conversions."
    )

    if not hh.grants:
        st.info("No option grants loaded.")
        return

    _completeness = compute_exercise_completeness(hh)
    if _completeness.issues:
        st.caption(f"⚠️ {_completeness.issues[0].detail}")

    _theme = theme if theme is not None else st.session_state.get("ui_theme", "Classic")
    if _theme == "Domains":
        _render_domains(hh)
    else:
        _render_classic(hh)


def _prep(
    hh: Household,
) -> tuple[ExerciseSchedule, list[int], ExerciseSchedule | None, set[int]]:
    """Shared setup for both Classic and Domains: the effective schedule
    (for grid seeding), the year range, and whether an explicit (non-empty,
    user-saved) schedule exists with its own price overrides.

    Only a genuinely persisted (saved) schedule carries EXPLICIT per-year
    price overrides. ``hh.effective_schedule()`` may instead be a synthesized
    default (default_at_expiry) that pre-fills price_by_year at every grant's
    expiry year from the COMMITTED hh.txn_price_now — those synthetic entries
    are not user overrides and must not shadow a freshly fetched quote.
    """
    schedule = hh.effective_schedule()
    years = list(range(hh.base_year, max(g.expiry_year for g in hh.grants) + 1))
    explicit_schedule = (
        hh.exercise_schedule
        if hh.exercise_schedule is not None and not hh.exercise_schedule.is_empty()
        else None
    )
    explicit_price_years = set(explicit_schedule.price_by_year) if explicit_schedule else set()
    return schedule, years, explicit_schedule, explicit_price_years


def _render_domains(hh: Household) -> None:
    schedule, years, explicit_schedule, explicit_price_years = _prep(hh)
    tab1, tab2 = st.tabs(["Edit Allocation", "Review Impact"])
    with tab1:
        price_by_year, effective_base, effective_growth = render_price_basis_partial(
            hh, years, explicit_schedule, explicit_price_years
        )
        norm = render_grid_partial(hh, years, schedule)
        render_validate_save_partial(hh, norm, price_by_year, effective_base, effective_growth)
    with tab2:
        render_review_partial(hh, years, norm, price_by_year)


def _render_classic(hh: Household) -> None:
    schedule, years, explicit_schedule, explicit_price_years = _prep(hh)
    price_by_year, effective_base, effective_growth = render_price_basis_partial(
        hh, years, explicit_schedule, explicit_price_years
    )
    norm = render_grid_partial(hh, years, schedule)
    render_review_partial(hh, years, norm, price_by_year)
    render_validate_save_partial(hh, norm, price_by_year, effective_base, effective_growth)
