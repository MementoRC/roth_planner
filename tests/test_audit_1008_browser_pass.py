"""Audit 2026-10-08 final browser pass: display regressions.

1. RMD Squeeze Tier-1 IRMAA note rendered "$0/yr": the view probed its own
   un-rounded indexed threshold + 1, which fell below the engine's
   $1,000-rounded Tier-1 threshold (audit-1008 F-52) whenever CPI > 0.
2-4. Sweet Spot "Cost Jump Points", the RMD Squeeze "Total RMD-Era Tax" delta
   and the Asset Location insight box carried two unescaped ``$`` amounts, so
   Streamlit rendered the text between them as LaTeX.
5. Option Exercises labelled the price basis with a hard-coded ticker instead
   of the configured one that Profile › Assumptions shows.
"""

from __future__ import annotations

import hashlib
import re

from streamlit.testing.v1 import AppTest

from engine.irmaa import irmaa_surcharge, irmaa_tier1_threshold
from views._format import fmt_dollars

_CPI = 0.025
# SHA-256 of the formerly hard-coded employer ticker (upper case), so item 5
# can check for it without the ticker appearing in source.
_LEGACY_TICKER_DIGEST = "cb1d474948e3b8bc870ce85f6710d036f1814a17937af0a07832e2e7cd693c5b"


def _mentions_legacy_ticker(text: str) -> bool:
    """True when any 3-character window of *text* (upper-cased) is the ticker."""
    up = text.upper()
    return any(
        hashlib.sha256(up[i : i + 3].encode()).hexdigest() == _LEGACY_TICKER_DIGEST
        for i in range(len(up) - 2)
    )


def _no_unescaped_dollar(text: str) -> bool:
    return not re.search(r"(?<!\\)\$", text)


def _run(script, *args) -> AppTest:
    at = AppTest.from_function(script, args=args, default_timeout=90).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _render_rmd(kw: dict) -> None:
    import views.rmd_squeeze as rmd
    from models.household import Household

    rmd.render(Household(**kw))


def _render_sweet(kw: dict) -> None:
    import views.sweet_spot as ss
    from models.household import Household

    ss.render(Household(**kw))


def _render_location(kw: dict) -> None:
    import views.asset_location as al
    from models.household import Household

    al.render(Household(**kw))


def _render_exercise(ticker: str) -> None:
    import streamlit as st

    from models.grants import StockGrant
    from models.household import Household
    from views.option_exercise import render

    st.session_state["_stock_ticker"] = ticker
    render(
        Household(
            base_year=2026,
            stock_price_now=50.0,
            grants=[
                StockGrant(year=2020, strike=20.0, shares=100, expiry_year=2028, grant_id="g1")
            ],
        )
    )


# --- 1 ---------------------------------------------------------------------


def test_rmd_tier1_note_is_not_zero_with_cpi() -> None:
    hh_kw = {"cpi_assumption": _CPI, "filing_status": "MFJ"}
    at = _run(_render_rmd, hh_kw)
    text = next(m.value for m in at.markdown if "IRMAA trigger" in m.value)

    from models.household import Household

    hh = Household(**hh_kw)
    thresh = irmaa_tier1_threshold(hh.base_year, "MFJ", _CPI)
    expected = irmaa_surcharge(
        thresh + 1,
        2,
        base_part_b=hh.medicare_part_b_base_monthly * 12,
        filing_status="MFJ",
        year=hh.base_year + 2,
        cpi=_CPI,
    )
    assert expected > 0
    assert "\\$0/yr" not in text, text
    assert fmt_dollars(expected).replace("$", "\\$") + "/yr for a couple" in text
    assert f"\\${thresh / 1000:.0f}K" in text


# --- 2 ---------------------------------------------------------------------


def test_sweet_spot_cost_jump_points_escape_dollars() -> None:
    at = _run(_render_sweet, {"cpi_assumption": _CPI})
    assert any("Cost Jump Points" in m.value for m in at.markdown)
    jumps = [w.value for w in at.warning if "marginal cost jumps" in w.value]
    assert jumps
    for text in jumps:
        assert "\\$" in text
        assert _no_unescaped_dollar(text), text


# --- 3 ---------------------------------------------------------------------


def test_rmd_total_tax_delta_escapes_dollars() -> None:
    at = _run(_render_rmd, {"cpi_assumption": _CPI})
    delta = next(m.delta for m in at.metric if m.label == "Total RMD-Era Tax")
    assert "\\$" in delta
    assert _no_unescaped_dollar(delta), delta


# --- 4 ---------------------------------------------------------------------


def test_asset_location_insight_escapes_dollars() -> None:
    at = _run(_render_location, {})
    boxes = [s.value for s in at.success if "Equity-first leaves" in s.value]
    assert boxes
    for text in boxes:
        assert "\\$" in text
        assert _no_unescaped_dollar(text), text


# --- 5 ---------------------------------------------------------------------


def test_option_exercise_uses_configured_ticker() -> None:
    at = _run(_render_exercise, "ZZZQ")
    labels = [b.label for b in at.button]
    headings = [m.value for m in at.markdown]
    assert "Fetch ZZZQ quote (Yahoo)" in labels
    assert "### ZZZQ Price Basis" in headings
    assert "### Assumed ZZZQ Price by Year" in headings
    assert not any(_mentions_legacy_ticker(t) for t in labels + headings)
