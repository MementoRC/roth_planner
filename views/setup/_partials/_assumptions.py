"""Assumptions Setup-domain partial (Task 7 of the ui-shell-theme-toggle plan).

Extracted from ``views/setup/parameters.py``'s Joint sub-tab: growth rate,
living expenses, ACA benchmark premium / enhanced-subsidies toggle / advance
APTC, Medicare Part B base premium, CPI projection rate, the prior-year
filed-MAGI IRMAA-lookback anchor (including its own inline sourced-field
trust/manual/confirm governance card — the last of Command Center's governed
field categories, removed from that module's old generic per-pending-field
loop in Task 4; see ``views/setup/command_center.py``'s docstring), plus the
Survivor-scenario and Inherited-IRAs expanders (both household-level, no
better domain fit among Household/Accounts/Options).

All of these fields are household-level (not per-person), so — like
``render_options_partial``/``render_portfolio_partial`` — this partial takes
no ``owner`` argument.

Unlike the 4 modules Task 6b split out of the original flat
``views/setup/_partials.py`` (``_household``/``_accounts``/``_options``/
``_portfolio``), this module is new as of Task 7; it follows the same
per-partial-module package pattern going forward.
"""

from __future__ import annotations

from typing import TypeVar

import streamlit as st

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from engine.irmaa import BASE_PART_B
from models.household import Household

from ._governance import _MAGI_PREFIX, _render_field_card

_Num = TypeVar("_Num", int, float)


def _clamp(value: _Num, lo: _Num, hi: _Num) -> _Num:
    """Clamp ``value`` into ``[lo, hi]``.

    Cached/uploaded JSON (.user_defaults.json, .tax_pdf_cache.json) can seed a
    widget ``value`` outside its ``[min_value, max_value]`` bounds, and Streamlit
    raises ``StreamlitAPIException`` at render time — crashing the Joint sub-tab on
    load with no user interaction (audit C4). The widget bounds are widened to
    generous limits so no legitimate value is ever out of range; this clamp is a
    final backstop so genuinely corrupt data still cannot crash the render.
    """
    return min(max(value, lo), hi)


def _render_prior_year_magi_anchor(
    container,
    base_year: int,
    pending: set[str],
    committed_json: dict,
    store: CandidateStore,
    choices: ChoiceMap,
) -> None:
    """Render the Prior-year filed MAGI anchor expander, plus an inline
    trust/manual/confirm governance card for any ``prior_year_magi.<year>``
    field currently pending review (moved from Command Center's old generic
    per-pending-field loop, removed in Task 4).
    """
    with container.expander("Prior-year filed MAGI anchor (IRMAA lookback)", expanded=False):
        st.caption(
            "Optional. Enter actual filed MAGI from your tax return. "
            "The engine will use these values instead of projecting MAGI for the "
            "IRMAA 2-year-lookback "
            f"(years {base_year} and {base_year + 1} IRMAA will be anchored to these). "
            "Leave 0 to use projected MAGI."
        )
        prior_magi: dict[int, float] = dict(st.session_state.get("prior_year_magi") or {})

        v1 = st.number_input(
            f"{base_year - 2} filed MAGI",
            min_value=0,
            max_value=100_000_000,
            value=_clamp(int(prior_magi.get(base_year - 2, 0)), 0, 100_000_000),
            step=1_000,
            format="%d",
            help=(
                f"Filed MAGI from your {base_year - 2} tax return. "
                f"Anchors {base_year} IRMAA via the 2-year lookback."
            ),
        )
        v2 = st.number_input(
            f"{base_year - 1} filed MAGI",
            min_value=0,
            max_value=100_000_000,
            value=_clamp(int(prior_magi.get(base_year - 1, 0)), 0, 100_000_000),
            step=1_000,
            format="%d",
            help=(
                f"Filed MAGI from your {base_year - 1} tax return. "
                f"Anchors {base_year + 1} IRMAA via the 2-year lookback."
            ),
        )

        if v1 > 0:
            prior_magi[base_year - 2] = float(v1)
        else:
            prior_magi.pop(base_year - 2, None)

        if v2 > 0:
            prior_magi[base_year - 1] = float(v2)
        else:
            prior_magi.pop(base_year - 1, None)

        st.session_state["prior_year_magi"] = prior_magi

        for field_key in sorted(pending):
            if field_key.startswith(_MAGI_PREFIX):
                with st.container(border=True):
                    _render_field_card(field_key, committed_json, store, choices)


def _render_survivor_scenario(container, base_year: int) -> None:
    """Render the Survivor scenario expander in the Joint sub-tab."""
    current: dict = st.session_state.get("survivor") or {}

    with container.expander("Survivor scenario (advanced sensitivity)", expanded=False):
        st.caption(
            "Optional. Models death of one spouse mid-projection. "
            "Survivor switches to single-filer brackets, std deduction, and senior bonus "
            "starting death_year + 1. Deceased's IRA rolls to survivor (spousal rollover); "
            "deceased's SS ends. "
            "NOT YET MODELED: SS survivor benefit step-up; inherited-IRA stretch rules."
        )
        # Seed the Enable flag once from any persisted/uploaded survivor scenario. Do NOT
        # pass value= alongside the persistent key: Streamlit ignores value= once the key
        # exists, so after an uncheck a mid-session upload that sets "survivor" would be
        # re-nulled by the else-branch below. The upload path sets "_survivor_enabled" too
        # (audit C9 / ui-streamlit-5).
        st.session_state.setdefault("_survivor_enabled", bool(current))
        enabled = st.checkbox(
            "Enable survivor scenario",
            key="_survivor_enabled",
        )
        if enabled:
            who_options = ["Me", "Spouse"]
            who_default = 0 if current.get("who_dies", "you") == "you" else 1
            who_choice = st.radio(
                "Who dies?",
                who_options,
                index=who_default,
                horizontal=True,
                key="_survivor_who_dies",
            )
            who_dies = "you" if who_choice == "Me" else "spouse"
            death_year = st.number_input(
                "Year of death",
                min_value=base_year,
                max_value=base_year + 50,
                value=_clamp(
                    int(current.get("death_year", base_year + 5)), base_year, base_year + 50
                ),
                step=1,
                format="%d",
                help=(
                    "Calendar year in which the spouse dies. "
                    "MFJ filing applies for that year; Single filing begins the following year."
                ),
                key="_survivor_death_year",
            )
            st.session_state["survivor"] = {"who_dies": who_dies, "death_year": int(death_year)}
        else:
            st.session_state["survivor"] = None


def _render_inherited_iras(container, base_year: int) -> None:
    """Render the Inherited IRAs expander in the Joint sub-tab."""

    with container.expander("Inherited IRAs (non-spousal, 10-year rule)", expanded=False):
        st.caption(
            "Model non-spousal inherited IRAs subject to the SECURE Act 10-year rule. "
            "The beneficiary must fully distribute the balance within 10 years of inheritance. "
            "Distributions add to ordinary income (MAGI). "
            "Leave empty if no inheritances are modeled."
        )

        iiras: list[dict] = list(st.session_state.get("inherited_iras") or [])
        to_remove: int | None = None

        for idx, entry in enumerate(iiras):
            col_bal, col_yr, col_rate, col_owner, col_remove = st.columns([3, 2, 2, 2, 1])
            new_bal = col_bal.number_input(
                "Balance ($)",
                min_value=0,
                max_value=100_000_000,
                value=_clamp(int(entry.get("balance", 0)), 0, 100_000_000),
                step=10_000,
                format="%d",
                key=f"iira_balance_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            new_yr = col_yr.number_input(
                "Year inherited",
                min_value=base_year - 15,
                max_value=base_year + 30,
                value=_clamp(
                    int(entry.get("inherited_year", base_year + 5)), base_year - 15, base_year + 30
                ),
                step=1,
                format="%d",
                key=f"iira_year_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            new_rate = col_rate.number_input(
                "Growth Rate (%)",
                min_value=0.0,
                max_value=15.0,
                value=float(entry.get("growth_rate", 0.07)) * 100,
                step=0.5,
                format="%.1f",
                key=f"iira_rate_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            owner_options = ["Me", "Spouse"]
            owner_val = entry.get("owner", "you")
            owner_idx_sel = 0 if owner_val == "you" else 1
            owner_choice = col_owner.radio(
                "Owner",
                owner_options,
                index=owner_idx_sel,
                horizontal=True,
                key=f"iira_owner_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            if col_remove.button("Remove", key=f"iira_remove_{idx}"):
                to_remove = idx
            iiras[idx] = {
                "balance": float(new_bal),
                "inherited_year": int(new_yr),
                "owner": "you" if owner_choice == "Me" else "spouse",
                "growth_rate": new_rate / 100.0,
            }

        if to_remove is not None:
            iiras.pop(to_remove)
            st.session_state["inherited_iras"] = iiras
            st.rerun()

        if st.button("Add inherited IRA", key="iira_add"):
            iiras.append(
                {
                    "balance": 0.0,
                    "inherited_year": base_year + 5,
                    "owner": "you",
                    "growth_rate": 0.07,
                }
            )
            st.session_state["inherited_iras"] = iiras
            st.rerun()

        st.session_state["inherited_iras"] = iiras


def render_assumptions_partial(hh: Household, container) -> None:
    """Render the household-level Assumptions partial.

    ``hh`` is used only for ``hh.base_year`` (the prior-year-MAGI anchor's
    lookback-year labels, and the survivor/inherited-IRAs expanders' default
    year bounds) — matches ``render_options_partial``/
    ``render_portfolio_partial``'s ``(hh, container)`` signature: no
    ``owner`` argument, since every field here is household-level, not
    per-person.

    The 7 top-level widgets (growth_rate, living_expenses,
    aca_benchmark_premium_annual, aca_enhanced_subsidies_active,
    advance_aptc_annual, medicare_part_b_base_monthly, cpi_assumption) plus
    the prior-year-MAGI anchor's two number_inputs are UNKEYED "controlled"
    widgets (Owner decision 5) — moved verbatim (same ``value=`` sourcing,
    same clamps) from ``views/setup/parameters.py``'s Joint sub-tab.

    ``views/setup/parameters.py``'s ``_render_pdf_1040_import`` (the "Import
    1040 PDF" expander) stays in ``parameters.py`` — it is not part of the
    field list this partial owns per the plan — and now renders AFTER this
    partial's call instead of between the prior-year-MAGI anchor and the
    survivor-scenario expander. This is the same kind of minor same-tab
    cosmetic reorder the plan's Task 3 "accepted reordering exception"
    explicitly extends to Tasks 4/6/7 when consolidating non-contiguous
    source fields into one partial call; no test asserts widget order and
    no `key=`/behavior changes.
    """
    pending: set[str] = st.session_state.get("_pending_review", set())
    store = CandidateStore.load(CANDIDATE_STORE_PATH)
    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    committed_json = load_committed(COMMITTED_PATH) or {}

    st.session_state.growth_rate = container.slider(
        "Growth Rate %",
        3.0,
        12.0,
        _clamp(st.session_state.growth_rate, 3.0, 12.0),
        0.5,
        format="%.1f%%",
    )
    st.session_state.living_expenses = container.number_input(
        "Annual Living Expenses",
        min_value=0,
        value=st.session_state.living_expenses,
        step=5_000,
        format="%d",
    )
    # txn_price / txn_price_now moved into
    # views/setup/_partials/_options.py:render_options_partial (called once
    # from views/setup/portfolio.py's Portfolio tab) as of Task 5 of the
    # ui-shell-theme-toggle plan — co-located with the stock-grants table
    # it prices, alongside its own trust/manual/confirm governance card.
    st.session_state["aca_benchmark_premium_annual"] = container.number_input(
        "ACA Benchmark Premium ($/yr)",
        min_value=0,
        max_value=60_000,
        value=_clamp(
            int(st.session_state.get("aca_benchmark_premium_annual", 21_600.0)), 0, 60_000
        ),
        step=100,
        format="%d",
        help=(
            "Annual cost of the 2nd-lowest-cost Silver plan in your state/county "
            "for your age group. Used to calculate ACA subsidy loss from conversions. "
            "Varies widely by geography — check healthcare.gov for your area."
        ),
    )
    st.session_state["aca_enhanced_subsidies_active"] = container.checkbox(
        "ACA enhanced subsidies active (ARP/IRA-style)",
        value=st.session_state.get("aca_enhanced_subsidies_active", False),
        help=(
            "Toggle for sensitivity analysis. Default OFF matches current law "
            "(ARP enhanced subsidies expired Dec 31, 2025). Turn ON to model "
            "what-if ARP gets extended."
        ),
    )
    st.session_state["advance_aptc_annual"] = container.number_input(
        "Advance APTC ($/yr)",
        min_value=0,
        max_value=60_000,
        value=_clamp(int(st.session_state.get("advance_aptc_annual", 0)), 0, 60_000),
        step=100,
        format="%d",
        help=(
            "Annual advance APTC (total IRS pre-payments to your insurer). "
            "Set 0 if not on marketplace insurance. Reconciled on Form 8962 at "
            "year-end — conversions that raise MAGI may trigger clawback; per "
            "P.L. 119-21, no repayment cap applies for TY 2026+."
        ),
    )
    st.session_state["medicare_part_b_base_monthly"] = container.number_input(
        "Medicare Part B Base Premium ($/mo)",
        min_value=0.0,
        max_value=5000.0,
        value=_clamp(
            float(st.session_state.get("medicare_part_b_base_monthly", BASE_PART_B / 12)),
            0.0,
            5000.0,
        ),
        step=1.0,
        format="%.2f",
        help=(
            "Standard Medicare Part B monthly premium (CMS-published; $202.90 in 2026). "
            "IRMAA surcharges are computed on top of this base."
        ),
    )
    st.session_state["cpi_assumption"] = container.number_input(
        "Annual CPI Projection Rate (0.025 = 2.5%)",
        min_value=0.0,
        max_value=0.06,
        value=_clamp(float(st.session_state.get("cpi_assumption", 0.025)), 0.0, 0.06),
        step=0.001,
        format="%.3f",
        help=(
            "Annual CPI projection rate (default 2.5%). Tax brackets, IRMAA tiers, "
            "FPL, etc. are projected forward from 2026 base values using this rate."
        ),
    )

    _render_prior_year_magi_anchor(container, hh.base_year, pending, committed_json, store, choices)
    _render_survivor_scenario(container, hh.base_year)
    _render_inherited_iras(container, hh.base_year)
