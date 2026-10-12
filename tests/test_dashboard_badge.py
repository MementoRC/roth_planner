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
