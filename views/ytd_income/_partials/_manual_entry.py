import streamlit as st

from engine.tax import LTCG_RATES_MFJ
from models.household import Household
from models.ytd_income import YTDSnapshot, sum_income_events
from views._format import fmt_pct
from views.ytd_income._partials._event_log import render_event_log_partial


def render_manual_entry_partial(hh: Household) -> YTDSnapshot:
    manual = st.checkbox(
        "Manual entry",
        value=st.session_state.get("ytd_manual_entry", True),
        key="ytd_manual_entry",
    )

    # Get existing snapshot or create empty
    ytd: YTDSnapshot = st.session_state.get("ytd_snapshot", YTDSnapshot())

    if manual:
        col1, col2, col3 = st.columns(3)
        with col1:
            wages = st.number_input(
                "Wages YTD",
                value=int(ytd.wages_ytd),
                step=5_000,
                format="%d",
            )
            nec_income = st.number_input(
                "1099-NEC / Self-Employment Income YTD",
                value=int(ytd.nec_income_ytd),
                step=1_000,
                format="%d",
                help="Self-employment or contractor income year-to-date.",
            )
            ltcg = st.number_input(
                "Long-Term Capital Gains YTD",
                value=int(ytd.ltcg_ytd),
                step=10_000,
                format="%d",
                help="From stop-loss triggers, mutual fund distributions, etc.",
            )
        with col2:
            stcg = st.number_input(
                "Short-Term Capital Gains YTD",
                value=int(ytd.stcg_ytd),
                step=5_000,
                format="%d",
            )
            div_col1, div_col2 = st.columns(2)
            with div_col1:
                qualified_dividends = st.number_input(
                    "Qualified dividends YTD",
                    value=int(ytd.qualified_dividends_ytd),
                    step=500,
                    format="%d",
                    help=f"Taxed at LTCG rates ({fmt_pct(LTCG_RATES_MFJ[0], 0)}/{fmt_pct(LTCG_RATES_MFJ[1], 0)}/{fmt_pct(LTCG_RATES_MFJ[2], 0)}); counts toward MAGI but not ordinary brackets.",
                )
            with div_col2:
                ordinary_dividends = st.number_input(
                    "Ordinary dividends YTD",
                    value=int(ytd.ordinary_dividends_ytd),
                    step=500,
                    format="%d",
                    help="Taxed as ordinary income; stacks into brackets and SS taxation.",
                )
        with col3:
            interest = st.number_input(
                "Interest YTD",
                value=int(ytd.interest_ytd),
                step=1_000,
                format="%d",
            )
            tax_exempt_interest = st.number_input(
                "Tax-exempt (muni) interest YTD",
                value=int(ytd.tax_exempt_interest_ytd),
                step=1_000,
                format="%d",
                help="Muni bond interest — counts toward MAGI/IRMAA and SS provisional income, not ordinary brackets.",
                key="ytd_tax_exempt_interest",
            )
            federal_withholding = st.number_input(
                "Federal Tax Withheld YTD",
                value=int(ytd.federal_withholding_ytd),
                step=1_000,
                format="%d",
                help="W-2 federal income tax withheld year-to-date; counts as 'Already paid' toward safe-harbor.",
            )

        st.markdown("##### Above-the-line adjustments")
        st.caption(
            "These reduce MAGI (IRMAA/NIIT/ACA) AND ordinary bracket room — they lower AGI "
            "before either is computed."
        )
        atl_col1, atl_col2 = st.columns(2)
        with atl_col1:
            hsa_contribution = st.number_input(
                "HSA contribution YTD",
                value=int(ytd.hsa_contribution_ytd),
                step=500,
                format="%d",
                help="Deductible HSA contribution (Form 8889). Above-the-line: lowers AGI/MAGI and widens bracket room.",
            )
        with atl_col2:
            deductible_ira = st.number_input(
                "Deductible IRA contribution YTD",
                value=int(ytd.deductible_ira_contribution_ytd),
                step=500,
                format="%d",
                help="Deductible traditional-IRA contribution (Schedule 1). Above-the-line: lowers AGI/MAGI and widens bracket room.",
            )

        st.markdown("##### Crypto (from Koinly)")
        st.caption(
            "These three numbers come from a Koinly tax report (short-term gains / "
            "long-term gains / income). Short-term gains and income are ordinary-rate "
            "and hit brackets + MAGI; long-term gains are preferential-rate (MAGI + NIIT, "
            "not brackets); income (staking/DeFi/airdrops) hits brackets + MAGI but not NIIT."
        )
        crypto_col1, crypto_col2, crypto_col3 = st.columns(3)
        with crypto_col1:
            crypto_stcg = st.number_input(
                "Crypto short-term gains YTD",
                value=float(ytd.crypto_stcg_ytd),
                step=100.0,
                format="%.2f",
                help="Koinly short-term capital gains. Ordinary-rate: hits brackets, MAGI, and NIIT.",
            )
        with crypto_col2:
            crypto_ltcg = st.number_input(
                "Crypto long-term gains YTD",
                value=float(ytd.crypto_ltcg_ytd),
                step=100.0,
                format="%.2f",
                help="Koinly long-term capital gains. Preferential-rate: hits MAGI and NIIT but not ordinary brackets.",
            )
        with crypto_col3:
            crypto_income = st.number_input(
                "Crypto income YTD (staking/DeFi)",
                value=float(ytd.crypto_income_ytd),
                step=100.0,
                format="%.2f",
                help="Koinly income report (staking, DeFi, airdrops). Ordinary income: hits brackets and MAGI.",
            )

        income_events = render_event_log_partial(hh, ytd)

        conversions_done = sum_income_events(income_events, kind="conversion", owner="you")
        spouse_conversions_done = sum_income_events(income_events, kind="conversion", owner="spouse")
        distributions_done = sum_income_events(income_events, kind="distribution")

        # Overlay only the fields this widget set actually computed onto the
        # previously-persisted snapshot (audit-0805 C42) -- a fresh
        # YTDSnapshot(...) here would silently drop any field this form does
        # not have a widget for (e.g. nqo_exercise_ytd, synced separately).
        ytd = ytd.overlay(
            tax_year=hh.base_year,
            wages_ytd=float(wages),
            nec_income_ytd=float(nec_income),
            ltcg_ytd=float(ltcg),
            stcg_ytd=float(stcg),
            qualified_dividends_ytd=float(qualified_dividends),
            ordinary_dividends_ytd=float(ordinary_dividends),
            interest_ytd=float(interest),
            tax_exempt_interest_ytd=float(tax_exempt_interest),
            ira_conversions_ytd=conversions_done,
            spouse_ira_conversions_ytd=spouse_conversions_done,
            ira_distributions_ytd=distributions_done,
            income_events=income_events,
            federal_withholding_ytd=float(federal_withholding),
            hsa_contribution_ytd=float(hsa_contribution),
            deductible_ira_contribution_ytd=float(deductible_ira),
            crypto_stcg_ytd=float(crypto_stcg),
            crypto_ltcg_ytd=float(crypto_ltcg),
            crypto_income_ytd=float(crypto_income),
            manually_entered=True,
        ).with_snapshot_date()

        st.session_state.ytd_snapshot = ytd

    return ytd
