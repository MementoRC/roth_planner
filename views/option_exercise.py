"""Option Exercise Planner — editable per-grant/per-year NQO exercise schedule.

Exercise timing and share count are a free decision variable: this page lets
the user tune how many shares of which grant to exercise in which year, and
shows the resulting per-year TXN price row, a live "Remaining" readout, a
read-only dollar mirror grid (spread income by tax year), and inline
validation. Persists via ``engine.exercise_schedule_store``.

All computation goes through ``ExerciseSchedule`` / the store — this module
owns Streamlit only.
"""

import pandas as pd
import streamlit as st

from engine.exercise_grid import normalize_grid_edits
from engine.exercise_schedule_store import clear_exercise_schedule, save_exercise_schedule
from models.exercise_schedule import ExerciseSchedule
from models.household import Household
from views._format import fmt_dollars

_SHARES_STATE_KEY = "_oe_shares_state"
_GRID_EDITOR_KEY = "oe_grid_editor"


def _price_key(year: int) -> str:
    return f"oe_price_{year}"


def _clear_widget_state() -> None:
    """Drop all per-page session_state so widgets re-seed from hh on rerun."""
    st.session_state.pop(_SHARES_STATE_KEY, None)
    st.session_state.pop(_GRID_EDITOR_KEY, None)
    for k in [k for k in st.session_state if k.startswith("oe_price_")]:
        st.session_state.pop(k, None)


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

    # --- 1. Per-year TXN price row ---
    st.markdown("### Assumed TXN Price by Year")
    price_by_year: dict[int, float] = {}
    price_cols = st.columns(len(years))
    for col, year in zip(price_cols, years, strict=True):
        with col:
            is_assumed = year not in schedule.price_by_year
            default_price = schedule.price(year, fallback=hh.txn_price_now)
            price_by_year[year] = st.number_input(
                str(year),
                value=float(default_price),
                step=1.0,
                format="%.2f",
                key=_price_key(year),
            )
            if is_assumed:
                st.caption("assumed")

    # --- 2. Editable exercise grid ---
    st.markdown("### Exercise Schedule (shares)")
    st.caption(
        "Each grant can only be exercised on or before its expiry year "
        "(grant year + 10). Cells past a grant's expiry are blank and any shares "
        "entered there are rejected."
    )
    prior_shares: dict[str, dict[str, int]] | None = st.session_state.get(_SHARES_STATE_KEY)

    def _seed_shares(gkey: str, year: int) -> int:
        if prior_shares is not None:
            return int(prior_shares.get(gkey, {}).get(str(year), 0))
        return schedule.shares(gkey, year)

    rows = []
    for g in hh.grants:
        row: dict[str, object] = {"Grant": f"{g.year} · ${g.strike:g} · {g.shares:,} sh"}
        for year in years:
            row[str(year)] = None if year > g.expiry_year else _seed_shares(g.key(), year)
        rows.append(row)
    grid_df = pd.DataFrame(rows)

    edited_df = st.data_editor(
        grid_df,
        key=_GRID_EDITOR_KEY,
        hide_index=True,
        width="stretch",
        disabled=["Grant"],
        column_config={
            str(year): st.column_config.NumberColumn(str(year), min_value=0, step=1)
            for year in years
        },
    )

    # Normalize edits through the pure helper: enforce each grant's expiry bound,
    # drop non-positive, compute live remaining (all from the CURRENT edited_df, so
    # the readouts below update in the same rerun as the edit).
    raw_by_key: dict[str, dict[int, int]] = {}
    for i, g in enumerate(hh.grants):
        cells: dict[int, int] = {}
        for year in years:
            val = edited_df.iloc[i][str(year)]
            cells[year] = int(val) if pd.notna(val) else 0
        raw_by_key[g.key()] = cells
    norm = normalize_grid_edits(hh.grants, years, raw_by_key)

    # Persist (string-keyed years) so the next rerun's seed reflects unsaved edits.
    st.session_state[_SHARES_STATE_KEY] = {
        key: {str(y): n for y, n in cells.items()}
        for key, cells in norm.shares_by_key.items()
    }

    for grant, year, n in norm.out_of_range:
        st.error(
            f"{grant.year} grant expires {grant.expiry_year}: "
            f"{n:,} shares entered in {year} were ignored."
        )

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
            save_exercise_schedule(current_schedule)
            hh.exercise_schedule = current_schedule
            st.success("Exercise schedule saved.")
            st.rerun()
    with b2:
        if st.button("Reset to default (hold to expiry)"):
            clear_exercise_schedule()
            hh.exercise_schedule = None
            _clear_widget_state()
            st.success("Reset to legacy default.")
            st.rerun()
