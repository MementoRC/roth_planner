"""Dashboard: dollar text must not render as LaTeX; chart ticks keep one decimal."""

from __future__ import annotations

import re
from pathlib import Path

from views._format import md_dollars
from views.dashboard import DOLLAR_TICKFORMAT

DASHBOARD_SRC = Path(__file__).resolve().parent.parent / "views" / "dashboard.py"


def test_md_dollars_escapes_every_dollar() -> None:
    assert md_dollars("IRAs $0.5M + $0.5M") == "IRAs \\$0.5M + \\$0.5M"


def test_dashboard_ira_summary_has_no_unescaped_dollars() -> None:
    from streamlit.testing.v1 import AppTest

    def _script() -> None:
        import views.dashboard as dashboard
        from models.household import Household

        dashboard.render(Household())

    at = AppTest.from_function(_script, default_timeout=60).run()
    assert not at.exception
    summary = next(c.value for c in at.caption if c.value.startswith("You "))
    assert "$" in summary
    assert not re.search(r"(?<!\\)\$", summary)
    for md in at.markdown:
        assert not re.search(r"(?<!\\)\$.*(?<!\\)\$", md.value), md.value


def test_dashboard_ticks_show_fractional_millions() -> None:
    # d3-format's `s` precision is SIGNIFICANT digits (clamped to >= 1), so
    # "$,.1s" behaves like "$,.0s" and shows $1M for every 1.0M-1.9M tick.
    # ".3~s" gives $1M / $1.25M / $1.5M; `~` trims trailing zeros. Do not
    # "simplify" this back to ".1s".
    assert DOLLAR_TICKFORMAT == "$,.3~s"
    src = DASHBOARD_SRC.read_text(encoding="utf-8")
    assert src.count("yaxis_tickformat=DOLLAR_TICKFORMAT") == 4
    assert '"$,.0s"' not in src
