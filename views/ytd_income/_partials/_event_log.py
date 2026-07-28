import pandas as pd
import streamlit as st

from models.household import Household
from models.ytd_income import IncomeEvent, YTDSnapshot
from views._format import fmt_dollars


def render_event_log_partial(hh: Household, ytd: YTDSnapshot) -> list[IncomeEvent]:
    st.markdown("##### Roth Conversions & IRA Distributions")
    st.caption(
        "Log each conversion or distribution as you execute it — custodian statements "
        "lag, so this is the most accurate running total."
    )

    income_events = st.session_state.get("income_events", list(ytd.income_events))

    with st.form("add_income_event", clear_on_submit=True):
        ie_col1, ie_col2, ie_col3, ie_col4 = st.columns(4)
        with ie_col1:
            ie_date = st.date_input("Date")
        with ie_col2:
            ie_kind = st.selectbox("Type", ["Conversion", "Distribution"])
        with ie_col3:
            owner_options = ["You"] if hh.filing_status == "Single" else ["You", "Spouse"]
            ie_owner = st.selectbox("Whose", owner_options)
        with ie_col4:
            ie_amount = st.number_input("Amount", min_value=0, step=1_000, format="%d")
        if st.form_submit_button("Add entry") and ie_amount > 0:
            income_events.append(
                IncomeEvent(
                    date=ie_date.isoformat(),
                    amount=float(ie_amount),
                    kind=ie_kind.lower(),
                    owner=ie_owner.lower(),
                )
            )
            st.session_state["income_events"] = income_events

    if income_events:
        ie_rows = [
            {
                "Date": e.date,
                "Type": e.kind.title(),
                "Whose": e.owner.title(),
                "Amount": fmt_dollars(e.amount),
            }
            for e in income_events
        ]
        st.dataframe(pd.DataFrame(ie_rows), width="stretch")
        del_idx = st.selectbox(
            "Remove an entry",
            options=list(range(len(income_events))),
            format_func=lambda i: (
                f"{income_events[i].date} — {income_events[i].kind.title()} — "
                f"{fmt_dollars(income_events[i].amount)}"
            ),
            index=None,
            placeholder="Select an entry to remove",
            key="income_event_delete_select",
        )
        if del_idx is not None and st.button("Remove selected entry"):
            income_events.pop(del_idx)
            st.session_state["income_events"] = income_events
            st.rerun()

    return income_events
