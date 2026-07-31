from __future__ import annotations

import pandas as pd
import streamlit as st

from engine.exercise_grid import GridNormalization, normalize_grid_edits
from models.exercise_schedule import ExerciseSchedule
from models.household import Household
from views.option_exercise._partials._helpers import _GRID_EDITOR_KEY, _SHARES_STATE_KEY


def render_grid_partial(
    hh: Household, years: list[int], schedule: ExerciseSchedule
) -> GridNormalization:
    """Section 2 of the original page: the editable per-grant/per-year
    exercise-shares grid, normalized via engine.exercise_grid.normalize_grid_edits.
    """
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

    raw_by_key: dict[str, dict[int, int]] = {}
    for i, g in enumerate(hh.grants):
        cells: dict[int, int] = {}
        for year in years:
            val = edited_df.iloc[i][str(year)]
            cells[year] = int(val) if pd.notna(val) else 0
        raw_by_key[g.key()] = cells
    norm = normalize_grid_edits(hh.grants, years, raw_by_key)

    st.session_state[_SHARES_STATE_KEY] = {
        key: {str(y): n for y, n in cells.items()}
        for key, cells in norm.shares_by_key.items()
    }

    for grant, year, n in norm.out_of_range:
        st.error(
            f"{grant.year} grant expires {grant.expiry_year}: "
            f"{n:,} shares entered in {year} were ignored."
        )

    return norm
