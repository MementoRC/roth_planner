from __future__ import annotations

from dataclasses import replace

import streamlit as st

import views.option_exercise as _option_exercise
from engine.exercise_grid import GridNormalization
from models.exercise_schedule import ExerciseSchedule
from models.household import GrowthProfile, Household, project_price
from views.option_exercise._partials._helpers import _clear_widget_state


def render_validate_save_partial(
    hh: Household,
    norm: GridNormalization,
    price_by_year: dict[int, float],
    effective_base: float,
    effective_growth: GrowthProfile,
) -> None:
    """Sections 5+6 of the original page: the validation-message banner and
    the Save/Reset buttons. Mutates hh.exercise_schedule on Save/Reset and
    calls st.rerun() — unchanged from the original (this is the final
    committing step of the page, not a pure display partial).
    """
    # --- 5. Validation banner ---
    current_schedule = ExerciseSchedule(
        shares_by_grant_year={
            key: dict(cells) for key, cells in norm.shares_by_key.items() if cells
        },
        price_by_year=dict(price_by_year),
    )
    messages = current_schedule.validate(hh.grants, hh.base_year)
    for msg in messages:
        st.error(msg)

    # --- 6. Save / Reset buttons ---
    st.markdown("---")
    b1, b2 = st.columns(2)
    with b1:
        if st.button("Save schedule", type="primary"):
            # Only persist a year's price as an explicit override if the widget
            # value actually diverges from the projected assumption -- untouched
            # "assumed" cells must never freeze into fake overrides that shadow a
            # later live-quote fetch (the "stuck at old price" bug).
            persisted_prices = {
                year: price
                for year, price in price_by_year.items()
                if abs(
                    price - project_price(effective_base, hh.base_year, effective_growth, year)
                )
                > 0.005
            }
            schedule_to_save = replace(current_schedule, price_by_year=persisted_prices)
            # Resolved via the parent module attribute (not a direct import) so
            # ``monkeypatch.setattr(views.option_exercise, "save_exercise_schedule", ...)``
            # / ``"clear_exercise_schedule"`` in tests still intercepts these calls.
            _option_exercise.save_exercise_schedule(schedule_to_save)
            hh.exercise_schedule = schedule_to_save
            st.success("Exercise schedule saved.")
            st.rerun()
    with b2:
        if st.button("Reset to default (hold to expiry)"):
            _option_exercise.clear_exercise_schedule()
            hh.exercise_schedule = None
            _clear_widget_state()
            st.success("Reset to legacy default.")
            st.rerun()
