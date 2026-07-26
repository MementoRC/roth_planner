"""Tests for the Dashboard data-completeness badge (A3)."""


def test_dashboard_renders_completeness_badge() -> None:
    from streamlit.testing.v1 import AppTest

    def _script() -> None:
        import views.dashboard as dashboard
        from models.household import Household

        dashboard.render(Household())

    at = AppTest.from_function(_script).run()
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert any("Data completeness" in c for c in captions)
