"""Tests for the Dashboard data-completeness badge (A3)."""


def test_dashboard_renders_completeness_badge() -> None:
    from streamlit.testing.v1 import AppTest

    def _script() -> None:
        import views.dashboard as dashboard
        from models.household import Household

        dashboard.render(Household())

    # default_timeout=60: page render runs the projection engine; settrace-based
    # coverage on CI runners multiplies render time ~10-30x past the 3s default.
    at = AppTest.from_function(_script, default_timeout=60).run()
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert any("Data completeness" in c for c in captions)
    (expander,) = [e for e in at.expander if e.label == "Data sources"]
    table = expander.markdown[0].value
    assert "| Field | Status | Source | As of |" in table
    assert "Your IRA balance" in table


def test_data_source_rows_label_source_and_date() -> None:
    from datetime import datetime

    from engine.data_status import DataStatusItem
    from models.sourced import Source
    from views._shared import data_source_rows

    when = datetime(2026, 1, 2, 3, 4)
    rows = data_source_rows(
        (
            DataStatusItem("your_ira", "Your IRA balance", "ok", "", Source.PDF, when),
            DataStatusItem("spouse_ira", "Spouse IRA balance", "missing", "x"),
        )
    )

    assert rows[0] == {
        "Field": "Your IRA balance",
        "Status": "ok",
        "Source": "Statement PDF",
        "As of": "2026-01-02",
    }
    assert rows[1]["Source"] == "—"
    assert rows[1]["As of"] == "—"
