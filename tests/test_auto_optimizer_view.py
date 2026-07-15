"""Smoke test for views/auto_optimizer.py — Exercise Auto-Optimizer page.

Uses ``streamlit.testing.v1.AppTest.from_function`` (extracts the function's
source and re-runs it as a standalone script), so the script function must be
fully self-contained — all imports and object construction live inside its
body, mirroring the pattern documented in
``streamlit.testing.v1.app_test.AppTest.from_function``.
"""

from streamlit.testing.v1 import AppTest


def _render_two_grants() -> None:
    from models.grants import StockGrant
    from models.household import Household
    from views.auto_optimizer import render

    grant1 = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2028, grant_id="g1")
    grant2 = StockGrant(year=2020, strike=130.0, shares=500, expiry_year=2029, grant_id="g2")
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        your_ira=500_000,
        spouse_ira=500_000,
        txn_price_now=150.0,
        grants=[grant1, grant2],
    )
    render(hh)


def test_auto_optimizer_view_smoke_renders_without_exception() -> None:
    at = AppTest.from_function(_render_two_grants)
    at.run()
    assert not at.exception
