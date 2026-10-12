"""Holdings grant tables label imported grants as pending review (#603)."""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

_CAPTION = "these grants are not used by the plan until you confirm them in"


def _render_options(pending: set[str]) -> None:
    import streamlit as st

    from engine.portfolio_sync import EquityGrant, PortfolioSnapshot
    from models.household import Household
    from views.setup._partials import render_options_partial

    st.session_state["_pending_review"] = pending
    st.session_state["portfolio_snapshot"] = PortfolioSnapshot(
        equity_grants=[EquityGrant("G1", "NQO", "2020-03-01", 100, 100, 5000.0)],
        imported_from_bundle=True,
    )
    render_options_partial(Household(), st)


def _render_comparison(pending: set[str]) -> None:
    import streamlit as st

    from engine.portfolio_sync import EquityGrant, PortfolioSnapshot
    from models.household import Household
    from views.portfolio import render_grant_comparison

    st.session_state["_pending_review"] = pending
    snap = PortfolioSnapshot(
        equity_grants=[EquityGrant("G1", "NQO", "2020-03-01", 100, 100, 5000.0)],
        imported_from_bundle=True,
    )
    render_grant_comparison(snap, Household())


def _captions(at: AppTest) -> list[str]:
    return [c.value for c in at.caption if _CAPTION in c.value]


def test_options_table_shows_pending_caption_when_grants_pending() -> None:
    at = AppTest.from_function(_render_options, kwargs={"pending": {"grants"}})
    at.run()
    assert not at.exception
    assert len(_captions(at)) == 1


def test_options_table_no_pending_caption_when_grants_not_pending() -> None:
    at = AppTest.from_function(_render_options, kwargs={"pending": {"your_ira"}})
    at.run()
    assert not at.exception
    assert _captions(at) == []


def test_comparison_table_shows_pending_caption_when_grants_pending() -> None:
    at = AppTest.from_function(_render_comparison, kwargs={"pending": {"grants"}})
    at.run()
    assert not at.exception
    assert len(_captions(at)) == 1


def test_comparison_table_no_pending_caption_when_grants_not_pending() -> None:
    at = AppTest.from_function(_render_comparison, kwargs={"pending": set()})
    at.run()
    assert not at.exception
    assert _captions(at) == []
