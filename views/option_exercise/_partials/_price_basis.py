from __future__ import annotations

from dataclasses import replace

import streamlit as st

import views.option_exercise as _option_exercise
from config.loader import save_user_defaults
from models.exercise_schedule import ExerciseSchedule
from models.household import GrowthProfile, Household, project_price
from views.option_exercise._partials._helpers import (
    _GROWTH_RATE_KEY,
    _QUOTE_PRICE_KEY,
    _clear_assumed_price_widgets,
    _price_key,
)


def render_price_basis_partial(
    hh: Household,
    years: list[int],
    explicit_schedule: ExerciseSchedule | None,
    explicit_price_years: set[int],
) -> tuple[dict[int, float], float, GrowthProfile]:
    """Sections 0+1 of the original page: the live TXN quote/growth-rate
    controls, then the per-year 'Assumed TXN Price by Year' row.

    Returns (price_by_year, effective_base, effective_growth) — the last
    two are also needed by render_validate_save_partial's Save-button
    "did this price diverge from the projected assumption" check, so they
    must be threaded through rather than recomputed.
    """
    # --- 0. Live TXN quote + growth-rate controls ---
    st.markdown("### TXN Price Basis")
    qc1, qc2 = st.columns([1, 2])
    with qc1:
        if st.button("Fetch TXN quote (Yahoo)"):
            # Resolved via the parent module attribute (not a direct import) so
            # ``monkeypatch.setattr(views.option_exercise, "handle_txn_quote_fetch", ...)``
            # in tests still intercepts this call.
            result = _option_exercise.handle_txn_quote_fetch()
            if result.ok and result.price is not None:
                st.success(f"Fetched TXN @ ${result.price:.2f}")
                st.caption("source: Yahoo Finance · pending review in Command Center")
                _clear_assumed_price_widgets(explicit_price_years)
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
            is_assumed = year not in explicit_price_years
            default_price = (
                explicit_schedule.price(year)
                if explicit_schedule is not None and year in explicit_price_years
                else project_price(effective_base, hh.base_year, effective_growth, year)
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

    return price_by_year, effective_base, effective_growth
