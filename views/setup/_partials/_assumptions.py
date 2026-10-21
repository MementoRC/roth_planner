"""Assumptions Setup-domain partial (originally Task 7 of the
ui-shell-theme-toggle plan).

Extracted from ``views/setup/parameters.py``'s Joint sub-tab: growth rate,
living expenses, ACA benchmark premium / enhanced-subsidies toggle / advance
APTC, Medicare Part B base premium, CPI projection rate, the prior-year
filed-MAGI IRMAA-lookback anchor, plus the Survivor-scenario and
Inherited-IRAs expanders (both household-level, no better domain fit among
Household/Accounts/Options).

Per-field sourced-value governance cards (including the prior-year-MAGI
anchor's) do NOT render here — they render exclusively in
``views/setup/command_center.py``'s generic per-pending-field loop (one
owner only, to avoid ``DuplicateWidgetID`` from ``st.tabs()`` executing
every tab body every run; see that module's docstring).

All of these fields are household-level (not per-person), so — like
``render_options_partial``/``render_portfolio_legacy`` — this partial takes
no ``owner`` argument.

Unlike the 4 modules Task 6b split out of the original flat
``views/setup/_partials.py`` (``_household``/``_accounts``/``_options``/
``_portfolio``), this module is new as of Task 7; it follows the same
per-partial-module package pattern going forward.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from config.scalar_registry import TERMINAL_IRA_TAX_RATE_BOUNDS, scalar_default
from engine.aca import derive_couple_benchmark_annual
from engine.data_sources.orchestrator import MAGI_CLEARED_KEY
from engine.upload_merge import sanitize_living_expenses
from models.household import Household
from views.optimizer_panel import terminal_rate_pct

from ._clamp import clamp as _clamp


def _render_prior_year_magi_anchor(container, base_year: int) -> None:
    """Render the Prior-year filed MAGI anchor expander.

    The trust/manual/confirm governance card for any ``prior_year_magi.<year>``
    field currently pending review does NOT render here — it renders
    exclusively in ``views/setup/command_center.py``'s generic
    per-pending-field loop.
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

        # audit-1008 F-64: popping a year from session alone cannot clear a
        # committed anchor (a fresh session also starts with {}), so a year
        # the user sets back to 0 is flagged explicitly for app.py's resolve.
        cleared = set(st.session_state.get(MAGI_CLEARED_KEY) or ())
        newly_cleared = False
        for year, value in ((base_year - 2, v1), (base_year - 1, v2)):
            if value > 0:
                prior_magi[year] = float(value)
                cleared.discard(year)
            elif prior_magi.pop(year, None) is not None:
                cleared.add(year)
                newly_cleared = True

        st.session_state["prior_year_magi"] = prior_magi
        if cleared:
            st.session_state[MAGI_CLEARED_KEY] = sorted(cleared)
        else:
            st.session_state.pop(MAGI_CLEARED_KEY, None)
        if newly_cleared:
            # The household for this run was resolved before this widget ran;
            # rerun so the cleared anchor stops driving IRMAA right away.
            st.rerun()


def _render_survivor_scenario(container, base_year: int) -> None:
    """Render the Survivor scenario expander in the Joint sub-tab."""
    current: dict = st.session_state.get("survivor") or {}

    with container.expander("Survivor scenario (advanced sensitivity)", expanded=False):
        st.caption(
            "Optional. Models death of one spouse mid-projection. "
            "Survivor switches to single-filer brackets, std deduction, and senior bonus "
            "starting death_year + 1. Deceased's IRA rolls to survivor (spousal rollover). "
            "Social Security: the deceased's benefit ends and the survivor gets the larger "
            "of their own benefit or the widow(er) benefit (the deceased's benefit including "
            "delayed credits, reduced for the survivor's age, and limited to the larger of "
            "the deceased's benefit or 82.5% of it at full retirement age if they claimed "
            "early). IRMAA for the two years after death uses the joint return's MFJ "
            "thresholds (2-year lookback), then Single. A survivor can ask SSA to use current "
            "income instead with Form SSA-44 (life-changing event); that appeal is not "
            "assumed. Not modeled: inherited-IRA stretch rules."
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


def _finite_int(value: Any, default: int = 0) -> int:
    """``int(value)``, or *default* when it is NaN/infinite or not a number.

    Session state can hold a NaN balance (``json.loads`` accepts bare ``NaN``),
    and ``int(float("nan"))`` raises, which would take the whole tab down.
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _render_inherited_iras(container, base_year: int) -> None:
    """Render the Inherited IRAs expander in the Joint sub-tab."""

    with container.expander("Inherited IRAs (non-spousal, 10-year rule)", expanded=False):
        st.caption(
            "Model non-spousal inherited IRAs subject to the SECURE Act 10-year rule. "
            "Enter the year the original owner died as the year inherited. The balance must "
            "be fully distributed by December 31 of the 10th year after that, so the model "
            "spreads it evenly over the death year plus the 10 years that follow. "
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
                value=_clamp(_finite_int(entry.get("balance", 0)), 0, 100_000_000),
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
                value=_clamp(float(entry.get("growth_rate", 0.07)) * 100, 0.0, 15.0),
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
            # Row widgets are keyed by list position (iira_*_{idx}); after a pop the
            # surviving rows shift down but Streamlit's per-key widget state persists,
            # so each survivor would inherit the removed/shifted row's cached value
            # and overwrite its own on the next render. Purge the position-keyed
            # widget state so every row re-seeds from value= (audit-0802 F6).
            for _i in range(len(iiras) + 1):
                for _prefix in ("iira_balance_", "iira_year_", "iira_rate_", "iira_owner_"):
                    st.session_state.pop(f"{_prefix}{_i}", None)
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
    ``render_portfolio_legacy``'s ``(hh, container)`` signature: no
    ``owner`` argument, since every field here is household-level, not
    per-person.

    The 9 top-level widgets (growth_rate, living_expenses, net_inv_income,
    aca_benchmark_premium_annual, aca_enhanced_subsidies_active,
    advance_aptc_annual, medicare_part_b_base_monthly, cpi_assumption,
    terminal_ira_tax_rate) plus
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
        value=_clamp(int(sanitize_living_expenses(st.session_state.living_expenses)), 0),
        step=5_000,
        format="%d",
    )
    # One home (redesign PR 2): the Conversion Plan, Sweet Spot and
    # ACA + IRMAA pages show this read-only. Unkeyed on purpose -- a keyed
    # widget's value is dropped whenever its page is not rendered.
    st.session_state["net_inv_income"] = container.number_input(
        "Additional net investment income ($/yr)",
        min_value=0,
        max_value=100_000_000,
        value=_clamp(
            int(st.session_state.get("net_inv_income", scalar_default("net_inv_income"))),
            0,
            100_000_000,
        ),
        step=5_000,
        format="%d",
        help=(
            "Manual NII not otherwise modeled -- on top of forecast dividends/gains "
            "and YTD investment income (if included). Used for NIIT on the Conversion "
            "Plan, Sweet Spot and ACA + IRMAA."
        ),
    )
    # stock_price / stock_price_now moved into
    # views/setup/_partials/_options.py:render_options_partial (called once
    # from views/setup/portfolio.py's Portfolio tab) as of Task 5 of the
    # ui-shell-theme-toggle plan — co-located with the stock-grants table
    # it prices; its trust/manual/confirm governance card renders in
    # views/setup/command_center.py, not here.
    # None means "derive" (national-average SLCSP, age-rated + CPI-indexed via
    # engine.aca.derive_couple_benchmark_annual) and an explicit float
    # (including 0.0) is a household override used verbatim -- see
    # models/household.py's aca_benchmark_premium_annual docstring. A plain
    # number_input can't represent "unset" without smuggling it in as 0.0
    # (a legitimate override meaning "no ACA premium exposure"), so an
    # explicit checkbox gates the numeric override instead; unchecking writes
    # None back rather than 0.0. The single source of truth for the derived
    # figure is engine.aca.derive_couple_benchmark_annual -- not recomputed
    # here (this project has killed parallel-computation "dual writer"
    # defects structurally twice, PRs #376 and #378).
    _current_benchmark = st.session_state.get("aca_benchmark_premium_annual")
    _derived_benchmark = derive_couple_benchmark_annual(
        hh.your_age,
        hh.spouse_age,
        hh.filing_status,
        year=hh.base_year,
        cpi=hh.cpi_assumption,
    )
    _override_benchmark = container.checkbox(
        "Override ACA benchmark premium with my county's SLCSP",
        value=_current_benchmark is not None,
        help=(
            "Leave unchecked to use a national-average estimate, age-rated for "
            "your household and CPI-indexed to the projection year (shown "
            "below when unchecked). Check this to enter YOUR county's actual "
            "2nd-lowest-cost Silver (SLCSP) plan premium: look up the gross "
            "monthly premium, BEFORE subsidies, for the plan year, at "
            "healthcare.gov/tax-tool (or your state's exchange), for the "
            "enrolled adult(s) -- then enter the ANNUAL total below. State "
            "variation is large (roughly $401/mo in NH to $1,299/mo in VT at "
            "age 40), so the national default can be far off for your actual "
            "county."
        ),
    )
    if _override_benchmark:
        _seed_benchmark = (
            _current_benchmark if _current_benchmark is not None else _derived_benchmark
        )
        st.session_state["aca_benchmark_premium_annual"] = float(
            container.number_input(
                "ACA Benchmark Premium ($/yr)",
                min_value=0,
                max_value=60_000,
                value=_clamp(int(_seed_benchmark), 0, 60_000),
                step=100,
                format="%d",
                help=(
                    "Annual cost of YOUR county's 2nd-lowest-cost Silver (SLCSP) "
                    "plan -- gross monthly premium before subsidies, for the "
                    "enrolled adult(s), for the full plan year. Used to calculate "
                    "ACA subsidy loss from conversions."
                ),
            )
        )
    else:
        st.session_state["aca_benchmark_premium_annual"] = None
        container.caption(
            f"Using national-average estimate for your ages: "
            f"${_derived_benchmark:,.0f}/yr. Check the box above to enter your "
            "own county's SLCSP instead."
        )
    st.session_state["aca_enhanced_subsidies_active"] = container.checkbox(
        "ACA enhanced subsidies active (ARP/IRA-style)",
        value=st.session_state.get(
            "aca_enhanced_subsidies_active", scalar_default("aca_enhanced_subsidies_active")
        ),
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
        value=_clamp(
            int(st.session_state.get("advance_aptc_annual", scalar_default("advance_aptc_annual"))),
            0,
            60_000,
        ),
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
            float(
                st.session_state.get(
                    "medicare_part_b_base_monthly", scalar_default("medicare_part_b_base_monthly")
                )
            ),
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
        value=_clamp(
            float(st.session_state.get("cpi_assumption", scalar_default("cpi_assumption"))),
            0.0,
            0.06,
        ),
        step=0.001,
        format="%.3f",
        help=(
            "Annual CPI projection rate (default 2.5%). Tax brackets, IRMAA tiers, "
            "FPL, etc. are projected forward from 2026 base values using this rate."
        ),
    )
    # audit-1008 F-07 / W19 follow-up: the exercise optimizer's terminal rate is
    # a household setting (was an engine constant). The optimizer panels edit
    # the same session key; this is its home. Unkeyed, like the widgets above.
    _terminal_low, _terminal_high = TERMINAL_IRA_TAX_RATE_BOUNDS
    st.session_state["terminal_ira_tax_rate"] = container.number_input(
        "Tax rate on IRA money left at the end (%)",
        min_value=_terminal_low,
        max_value=_terminal_high,
        value=terminal_rate_pct(),
        step=1.0,
        format="%.1f",
        help=(
            "Used by the exercise auto-optimizer. Traditional and inherited IRA "
            "balances still unspent at the end of the projection are taxed when "
            "they come out, by the survivor or by heirs; this rate prices that "
            "deferred tax so a plan can't look cheap just by leaving it behind. "
            "Default 24%."
        ),
    )

    _render_prior_year_magi_anchor(container, hh.base_year)
    _render_survivor_scenario(container, hh.base_year)
    _render_inherited_iras(container, hh.base_year)
