from __future__ import annotations

import pandas as pd
import streamlit as st

from engine.exercise_grid import GridNormalization
from models.household import Household
from views._format import fmt_dollars


def render_review_partial(
    hh: Household,
    years: list[int],
    norm: GridNormalization,
    price_by_year: dict[int, float],
) -> None:
    """Sections 3+4 of the original page: the live 'Remaining' readout and
    the read-only dollar mirror grid (ordinary income by tax year).
    """
    # --- 3. Live "Remaining" readout ---
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
