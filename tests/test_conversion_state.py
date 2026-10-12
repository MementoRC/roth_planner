"""views/conversion_state.clear_conversion_widgets drops the grid editor and old per-year keys."""

from views import conversion_state
from views.conversion_state import GRID_KEY, clear_conversion_widgets


def test_clear_conversion_widgets_drops_editor_and_prefixed_keys(monkeypatch) -> None:
    state = dict.fromkeys((GRID_KEY, "yc_2026", "sc_2026", "qcd_2026", "sp_qcd_2026"), 1)
    state["conv_plan_your"] = {2026: 50_000}
    state["nav_page"] = "conversions"
    # Bare-mode st.session_state is process-global; patch it like
    # tests/test_reset_to_demo_clears_personal_state.py does.
    monkeypatch.setattr(conversion_state.st, "session_state", state)

    clear_conversion_widgets()

    assert state == {"conv_plan_your": {2026: 50_000}, "nav_page": "conversions"}


def test_grid_key_matches_planner() -> None:
    from views import planner

    assert planner._GRID_KEY == GRID_KEY == "conv_grid_editor"
