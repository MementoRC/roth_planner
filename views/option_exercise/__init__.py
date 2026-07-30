"""Option Exercise Planner — editable per-grant/per-year NQO exercise schedule.

Exercise timing and share count are a free decision variable: this page lets
the user tune how many shares of which grant to exercise in which year, and
shows the resulting per-year TXN price row, a live "Remaining" readout, a
read-only dollar mirror grid (spread income by tax year), and inline
validation. Persists via ``engine.exercise_schedule_store``.

All computation goes through ``ExerciseSchedule`` / the store — this module
owns Streamlit only.
"""

import streamlit as st

from engine.exercise_schedule_store import clear_exercise_schedule, save_exercise_schedule
from models.household import Household
from views.option_exercise._partials import (
    handle_txn_quote_fetch,
    render_grid_partial,
    render_price_basis_partial,
    render_review_partial,
    render_validate_save_partial,
)

# save_exercise_schedule / clear_exercise_schedule are imported (but not called
# directly in this module — the actual calls live in _validate_save.py,
# resolved via this module's own attribute) so that
# monkeypatch.setattr(oe_module, "save_exercise_schedule", ...) /
# "clear_exercise_schedule" in tests still intercepts the real page's
# Save/Reset buttons.

# handle_txn_quote_fetch is re-exported (not called directly in this module —
# the actual call lives in _price_basis.py, resolved via this module's own
# attribute) so that tests can still `from views.option_exercise import
# handle_txn_quote_fetch` and `monkeypatch.setattr(oe_module,
# "handle_txn_quote_fetch", ...)` to intercept the real page's fetch button.
__all__ = [
    "clear_exercise_schedule",
    "handle_txn_quote_fetch",
    "render",
    "save_exercise_schedule",
]


def render(hh: Household) -> None:
    st.title("Option Exercise Planner")
    st.caption(
        "Choose how many shares of each grant to exercise in which year — "
        "a lever for filling tax brackets and timing Roth conversions."
    )

    if not hh.grants:
        st.info("No option grants loaded.")
        return

    schedule = hh.effective_schedule()
    years = list(range(hh.base_year, max(g.expiry_year for g in hh.grants) + 1))

    # Only a genuinely persisted (saved) schedule carries EXPLICIT per-year
    # price overrides. ``hh.effective_schedule()`` may instead be a synthesized
    # default (default_at_expiry) that pre-fills price_by_year at every grant's
    # expiry year from the COMMITTED hh.txn_price_now — those synthetic entries
    # are not user overrides and must not shadow a freshly fetched quote.
    explicit_schedule = (
        hh.exercise_schedule
        if hh.exercise_schedule is not None and not hh.exercise_schedule.is_empty()
        else None
    )
    explicit_price_years = set(explicit_schedule.price_by_year) if explicit_schedule else set()

    price_by_year, effective_base, effective_growth = render_price_basis_partial(
        hh, years, explicit_schedule, explicit_price_years
    )

    norm = render_grid_partial(hh, years, schedule)

    render_review_partial(hh, years, norm, price_by_year)

    render_validate_save_partial(hh, norm, price_by_year, effective_base, effective_growth)
