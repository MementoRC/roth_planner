"""Option Exercise Planner — editable per-grant/per-year NQO exercise schedule.

Exercise timing and share count are a free decision variable: this page lets
the user tune how many shares of which grant to exercise in which year, and
shows the resulting per-year TXN price row, a live "Remaining" readout, a
read-only dollar mirror grid (spread income by tax year), and inline
validation. Persists via ``engine.exercise_schedule_store``.

All computation goes through ``ExerciseSchedule`` / the store — this module
owns Streamlit only.
"""

from dataclasses import replace

import pandas as pd
import streamlit as st

from engine.exercise_schedule_store import clear_exercise_schedule, save_exercise_schedule
from models.exercise_schedule import ExerciseSchedule
from models.household import Household, project_price
from views._format import fmt_dollars
from views.option_exercise._partials import (
    handle_txn_quote_fetch,
    render_grid_partial,
    render_price_basis_partial,
)
from views.option_exercise._partials._helpers import _clear_widget_state

# handle_txn_quote_fetch is re-exported (not called directly in this module —
# the actual call lives in _price_basis.py, resolved via this module's own
# attribute) so that tests can still `from views.option_exercise import
# handle_txn_quote_fetch` and `monkeypatch.setattr(oe_module,
# "handle_txn_quote_fetch", ...)` to intercept the real page's fetch button.
__all__ = ["handle_txn_quote_fetch", "render"]


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

    # --- 3. Live "Remaining" readout (same-rerun, from current edits) ---
    st.markdown("### Remaining Unexercised")
    remaining_rows = []
    for g in hh.grants:
        scheduled = sum(norm.shares_by_key[g.key()].values())
        remaining_rows.append(
            {
                "Grant": f"{g.year} · ${g.strike:g}",
                "Granted": f"{g.shares:,}",
                "Scheduled": f"{scheduled:,}",
                "Remaining": f"{g.shares - scheduled:,}",
            }
        )
    st.dataframe(pd.DataFrame(remaining_rows), hide_index=True, width="stretch")

    # --- 4. Read-only dollar mirror grid ---
    st.markdown("### Ordinary Income Produced (unsaved edits included)")
    dollar_rows = []
    year_totals = dict.fromkeys(years, 0.0)
    for g in hh.grants:
        row = {"Grant": f"{g.year} · ${g.strike:g} · {g.shares:,} sh"}
        for year in years:
            shares = norm.shares_by_key[g.key()].get(year, 0)
            dollars = g.per_share_spread(price_by_year[year]) * shares
            row[str(year)] = fmt_dollars(dollars) if year <= g.expiry_year else "—"
            year_totals[year] += dollars
        dollar_rows.append(row)
    total_row = {"Grant": "Total (ordinary income)"}
    for year in years:
        total_row[str(year)] = fmt_dollars(year_totals[year])
    dollar_rows.append(total_row)
    st.dataframe(pd.DataFrame(dollar_rows), hide_index=True, width="stretch")

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
                if abs(price - project_price(effective_base, hh.base_year, effective_growth, year))
                > 0.005
            }
            schedule_to_save = replace(current_schedule, price_by_year=persisted_prices)
            save_exercise_schedule(schedule_to_save)
            hh.exercise_schedule = schedule_to_save
            st.success("Exercise schedule saved.")
            st.rerun()
    with b2:
        if st.button("Reset to default (hold to expiry)"):
            clear_exercise_schedule()
            hh.exercise_schedule = None
            _clear_widget_state()
            st.success("Reset to legacy default.")
            st.rerun()
