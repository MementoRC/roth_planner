from __future__ import annotations

import streamlit as st

import views.option_exercise as _option_exercise
from engine.data_sources import paths
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.resolver import has_pending_candidate
from models.exercise_schedule import ExerciseSchedule
from models.household import GrowthProfile, Household, project_price
from models.sourced import Source
from views._shared import render_readonly_input
from views.navigation import label_of
from views.option_exercise._partials._helpers import _price_key, configured_ticker

_BASIS_KEY = "_oe_price_basis"


def _pending_quote(confirmed_price: float) -> float | None:
    """A fetched-but-unconfirmed stock quote waiting in Command Center, else None.

    Uses the resolver's own pending predicate (honours the user's trust
    choice) so this page and Command Center agree.
    """
    store = CandidateStore.load(paths.CANDIDATE_STORE_PATH)
    choice = ChoiceMap.load(paths.TRUST_CHOICES_PATH).get("stock_price_now")
    quotes = [
        c for c in store.candidates_for("stock_price_now") if c.prov.source == Source.MARKET_QUOTE
    ]
    if has_pending_candidate(quotes, choice, confirmed_price):
        return float(quotes[0].value)
    return None


def _reproject_assumed_cells(
    hh: Household, years: list[int], explicit_price_years: set[int]
) -> None:
    """Drop stale widget state of ASSUMED year cells when the confirmed basis changes.

    Keyed number_inputs ignore ``value=`` once state exists, so a price/growth
    change made in Profile › Assumptions would otherwise leave assumed cells showing old values.
    Explicit saved overrides are never touched.
    """
    basis = (hh.stock_price_now, hh.stock_price_growth.default_rate)
    last = st.session_state.get(_BASIS_KEY)
    st.session_state[_BASIS_KEY] = basis
    if last is None or last == basis:
        return
    for year in years:
        if year not in explicit_price_years:
            st.session_state.pop(_price_key(year), None)


def render_price_basis_partial(
    hh: Household,
    years: list[int],
    explicit_schedule: ExerciseSchedule | None,
    explicit_price_years: set[int],
) -> tuple[dict[int, float], float, GrowthProfile]:
    """Sections 0+1 of the original page: the read-only confirmed stock
    price/growth (edited in Setup), then the per-year 'Assumed <ticker> Price by Year' row.

    Returns (price_by_year, effective_base, effective_growth) — the last
    two are also needed by render_validate_save_partial's Save-button
    "did this price diverge from the projected assumption" check, so they
    must be threaded through rather than recomputed.
    """
    # --- 0. Live quote + growth-rate controls ---
    # Same ticker source as Profile › Assumptions (views/setup/_partials/_options.py);
    # handle_stock_quote_fetch fetches this same ticker.
    ticker = configured_ticker()
    st.markdown(f"### {ticker} Price Basis")
    qc1, qc2 = st.columns([1, 2])
    with qc1:
        if st.button(f"Fetch {ticker} quote (Yahoo)"):
            # Resolved via the parent module attribute (not a direct import) so
            # ``monkeypatch.setattr(views.option_exercise, "handle_stock_quote_fetch", ...)``
            # in tests still intercepts this call.
            result = _option_exercise.handle_stock_quote_fetch()
            if result.ok and result.price is not None:
                st.success(f"Fetched {ticker} @ ${result.price:.2f}")
                st.caption(
                    f"Pending review: confirm it in {label_of('review')} to use it. "
                    "Until then this page keeps the current price."
                )
            else:
                st.warning(f"Couldn't fetch a live quote ({result.error}); using last known price.")
    with qc2:
        render_readonly_input(
            "Current price",
            f"${hh.stock_price_now:,.2f}",
            key="stock_price",
            page="assumptions",
            where="Profile › Assumptions › Stock Price",
        )
        pending = _pending_quote(hh.stock_price_now)
        if pending is not None:
            st.markdown(f"Pending \\${pending:,.2f} — confirm in {label_of('review')}")
        render_readonly_input(
            "Assumed growth",
            f"{hh.stock_price_growth.default_rate * 100:.2f}%/yr",
            key="stock_growth",
            page="assumptions",
            where="Profile › Assumptions › Stock Price",
        )

    effective_growth = hh.stock_price_growth
    effective_base = hh.stock_price_now
    _reproject_assumed_cells(hh, years, explicit_price_years)

    # --- 1. Per-year price row ---
    st.markdown(f"### Assumed {ticker} Price by Year")
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
