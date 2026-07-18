"""Option Exercise Planner — editable per-grant/per-year NQO exercise schedule.

Exercise timing and share count are a free decision variable: this page lets
the user tune how many shares of which grant to exercise in which year, and
shows the resulting per-year TXN price row, a live "Remaining" readout, a
read-only dollar mirror grid (spread income by tax year), and inline
validation. Persists via ``engine.exercise_schedule_store``.

All computation goes through ``ExerciseSchedule`` / the store — this module
owns Streamlit only.
"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pandas as pd
import streamlit as st

from config.loader import save_user_defaults
from engine.data_sources.paths import CANDIDATE_STORE_PATH
from engine.data_sources.record import record_txn_quote_candidate
from engine.exercise_grid import normalize_grid_edits
from engine.exercise_schedule_store import clear_exercise_schedule, save_exercise_schedule
from engine.market_quote import QuoteResult, fetch_txn_quote
from models.exercise_schedule import ExerciseSchedule
from models.household import Household, project_price
from views._format import fmt_dollars

_SHARES_STATE_KEY = "_oe_shares_state"
_GRID_EDITOR_KEY = "oe_grid_editor"
_QUOTE_PRICE_KEY = "_txn_quote_price"
_GROWTH_RATE_KEY = "txn_price_growth_rate"


def _price_key(year: int) -> str:
    return f"oe_price_{year}"


def _clear_widget_state() -> None:
    """Drop all per-page session_state so widgets re-seed from hh on rerun."""
    st.session_state.pop(_SHARES_STATE_KEY, None)
    st.session_state.pop(_GRID_EDITOR_KEY, None)
    for k in [k for k in st.session_state if k.startswith("oe_price_")]:
        st.session_state.pop(k, None)


def handle_txn_quote_fetch(
    *,
    store_path: str | Path = CANDIDATE_STORE_PATH,
    fetcher: Callable[[], QuoteResult] = fetch_txn_quote,
) -> QuoteResult:
    """Fetch a live TXN quote, record it as a pending Command Center candidate,
    and stash it in session_state so it immediately drives this page's price
    projection basis.

    The committed ``hh.txn_price_now`` is left untouched — the fetched price
    only becomes authoritative once confirmed via the Command Center review
    gate (``record_txn_quote_candidate``); until then it lives in
    ``st.session_state[_QUOTE_PRICE_KEY]`` as the page's effective basis.

    Never raises: ``fetcher`` (matching ``fetch_txn_quote``) always returns a
    ``QuoteResult`` rather than raising, so this helper does too — callers
    branch on ``result.ok`` to decide what to show.
    """
    result = fetcher()
    if result.ok and result.price is not None:
        record_txn_quote_candidate(result.price, store_path=store_path)
        st.session_state[_QUOTE_PRICE_KEY] = result.price
    return result


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

    # --- 0. Live TXN quote + growth-rate controls ---
    st.markdown("### TXN Price Basis")
    qc1, qc2 = st.columns([1, 2])
    with qc1:
        if st.button("Fetch TXN quote (Yahoo)"):
            result = handle_txn_quote_fetch()
            if result.ok and result.price is not None:
                st.success(f"Fetched TXN @ ${result.price:.2f}")
                st.caption("source: Yahoo Finance · pending review in Command Center")
            else:
                st.warning(
                    f"Couldn't fetch a live quote ({result.error}); using last known price."
                )
    with qc2:
        growth_pct = st.number_input(
            "Assumed TXN growth (%/yr)",
            value=float(hh.txn_price_growth.default_rate * 100),
            step=0.5,
            format="%.2f",
        )
        st.session_state[_GROWTH_RATE_KEY] = growth_pct
        if not st.session_state.get("_suppress_snapshot_autoload"):
            save_user_defaults({"txn_price_growth_rate": float(growth_pct)})

    effective_growth = replace(hh.txn_price_growth, default_rate=growth_pct / 100)
    effective_base = st.session_state.get(_QUOTE_PRICE_KEY, hh.txn_price_now)

    # --- 1. Per-year TXN price row ---
    st.markdown("### Assumed TXN Price by Year")
    price_by_year: dict[int, float] = {}
    price_cols = st.columns(len(years))
    for col, year in zip(price_cols, years, strict=True):
        with col:
            is_assumed = year not in schedule.price_by_year
            default_price = schedule.price(
                year, fallback=project_price(effective_base, hh.base_year, effective_growth, year)
            )
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
