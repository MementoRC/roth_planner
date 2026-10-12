"""views/optimizer_panel.py: the Auto-Optimizer halves on Conversion Plan and Option Exercises."""

import pytest
from streamlit.testing.v1 import AppTest


def _render_both_halves() -> None:
    from models.grants import StockGrant
    from models.household import Household
    from views.optimizer_panel import render_conversion_half, render_exercise_half

    hh = Household(
        your_age=60,
        spouse_age=54,
        base_year=2026,
        your_ira=1_000_000,
        spouse_ira=1_000_000,
        stock_price_now=200.0,
        grants=[StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")],
    )
    render_conversion_half(hh)
    render_exercise_half(hh)


def _render_no_grants() -> None:
    from models.household import Household
    from views.optimizer_panel import render_conversion_half

    # Household defaults to the demo grants (config/defaults.py)
    render_conversion_half(
        Household(
            your_age=60,
            spouse_age=54,
            base_year=2026,
            your_ira=500_000,
            spouse_ira=500_000,
            grants=[],
        )
    )


def _app() -> AppTest:
    # A solve plus a rerun can pass Streamlit's 3s default under full-suite load.
    return AppTest.from_function(_render_both_halves, default_timeout=60)


def _click(at: AppTest, label: str, nth: int = 0) -> None:
    [b for b in at.button if b.label == label][nth].click()
    at.run()


def test_halves_render_without_exception_before_run() -> None:
    at = _app()
    at.run()
    assert not at.exception
    assert [b.label for b in at.button].count("Run optimizer") == 2
    assert not [b for b in at.button if b.label.startswith("Apply")]


def test_one_run_feeds_both_halves() -> None:
    at = _app()
    at.run()
    _click(at, "Run optimizer", nth=0)

    assert not at.exception
    from engine.exercise_optimizer import OptimizerResult

    assert isinstance(at.session_state["_auto_opt_result"], OptimizerResult)
    assert (
        at.session_state["_auto_opt_result"].best.ceiling_label != "current"
    )  # else the Apply tests pass vacuously
    labels = [b.label for b in at.button]
    assert "Apply conversions" in labels
    assert "Apply exercises" in labels


def test_apply_conversions_writes_plan_and_drops_stale_editor() -> None:
    at = _app()
    at.session_state["conv_grid_editor"] = {"edited_rows": {"0": {"You": 1}}}
    at.session_state["yc_2026"] = 1
    at.run()
    _click(at, "Run optimizer")
    _click(at, "Apply conversions")

    assert not at.exception
    best = at.session_state["_auto_opt_result"].best
    # Apply writes the winner in the grid's normalized form: zero years
    # dropped, each year capped at that year's IRA balance. The expected
    # values come from the grid normalizer itself, run on the same household
    # as _render_both_halves, so a wrong amount in any year fails.
    from models.grants import StockGrant
    from models.household import Household
    from views.planner import grid_normalized_conversions

    hh = Household(
        your_age=60,
        spouse_age=54,
        base_year=2026,
        your_ira=1_000_000,
        spouse_ira=1_000_000,
        stock_price_now=200.0,
        grants=[StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")],
    )
    exp_your, exp_spouse = grid_normalized_conversions(
        hh, best.conversions.your_conversions, best.conversions.spouse_conversions
    )
    assert exp_your or exp_spouse  # the winner converts something
    for written, expected, winner in (
        (at.session_state["conv_plan_your"], exp_your, best.conversions.your_conversions),
        (at.session_state["conv_plan_spouse"], exp_spouse, best.conversions.spouse_conversions),
    ):
        assert set(written) == set(expected)
        for y in written:
            assert written[y] == pytest.approx(expected[y], abs=0.01)
            assert 0 < written[y] <= winner[y]
    assert "conv_grid_editor" not in at.session_state
    assert "yc_2026" not in at.session_state


def test_apply_exercises_saves_once_and_drops_stale_editor(monkeypatch) -> None:
    saved = []
    monkeypatch.setattr(
        "views.optimizer_panel.save_exercise_schedule", lambda schedule: saved.append(schedule)
    )
    at = _app()
    at.session_state["oe_grid_editor"] = {"edited_rows": {}}
    at.session_state["_oe_shares_state"] = {"stale": 1}
    at.session_state["oe_price_2027"] = 250.0  # a user price input must survive Apply
    at.run()
    _click(at, "Run optimizer")
    _click(at, "Apply exercises")

    assert not at.exception
    assert len(saved) == 1
    best = at.session_state["_auto_opt_result"].best.schedule
    assert saved[0].shares_by_grant_year == best.shares_by_grant_year
    assert saved[0].price_by_year == {}  # projected prices are not stored as overrides (#543)
    assert "oe_grid_editor" not in at.session_state
    assert "_oe_shares_state" not in at.session_state
    assert at.session_state["oe_price_2027"] == 250.0


def _successes(at: AppTest, needle: str) -> list[str]:
    return [s.value for s in at.success if needle in s.value]


def test_conversion_flash_survives_an_extra_rerun() -> None:
    at = _app()
    at.run()
    _click(at, "Run optimizer")
    _click(at, "Apply conversions")
    at.run()  # the planner's second st.rerun()

    assert not at.exception
    assert _successes(at, "Conversions applied")


def test_flash_shows_only_in_its_own_half(monkeypatch) -> None:
    monkeypatch.setattr("views.optimizer_panel.save_exercise_schedule", lambda schedule: None)
    at = _app()
    at.run()
    _click(at, "Run optimizer")
    _click(at, "Apply conversions")

    assert len(_successes(at, "Conversions applied")) == 1
    assert not _successes(at, "Exercise schedule applied")

    _click(at, "Apply exercises")
    assert not _successes(at, "Conversions applied")
    assert len(_successes(at, "Exercise schedule applied")) == 1


def test_run_optimizer_clears_flash() -> None:
    at = _app()
    at.run()
    _click(at, "Run optimizer")
    _click(at, "Apply conversions")
    _click(at, "Run optimizer", nth=0)

    assert not at.exception
    assert not _successes(at, "applied")


def test_conversion_half_hidden_without_grants() -> None:
    at = AppTest.from_function(_render_no_grants)
    at.run()
    assert not at.exception
    assert not at.button
    assert not at.expander


def _render_planner_page() -> None:
    from models.grants import StockGrant
    from models.household import Household
    from views.planner import render

    render(
        Household(
            your_age=60,
            spouse_age=54,
            base_year=2026,
            your_ira=1_000_000,
            spouse_ira=1_000_000,
            stock_price_now=200.0,
            grants=[
                StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")
            ],
        )
    )


def _render_options_page() -> None:
    from models.grants import StockGrant
    from models.household import Household
    from views.option_exercise import render

    render(
        Household(
            your_age=60,
            spouse_age=54,
            base_year=2026,
            your_ira=1_000_000,
            spouse_ira=1_000_000,
            stock_price_now=200.0,
            grants=[
                StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")
            ],
        )
    )


def test_conversion_plan_page_hosts_the_conversion_half() -> None:
    at = AppTest.from_function(_render_planner_page, default_timeout=60)
    at.run()
    assert not at.exception
    assert any("conversions with option exercises" in e.label for e in at.expander)


def test_apply_conversions_survives_the_planner_grid_round_trip() -> None:
    """On the full Conversion Plan page the grid re-normalizes conv_plan_*
    after Apply; the applied result must still be shown afterwards."""
    at = AppTest.from_function(_render_planner_page, default_timeout=60)
    at.run()
    _click(at, "Run optimizer")
    best = at.session_state["_auto_opt_result"].best
    _click(at, "Apply conversions")
    at.run()  # the planner's second st.rerun()

    assert not at.exception
    assert "Apply conversions" in [b.label for b in at.button]
    assert any("Winning Strategy" in m.value for m in at.markdown)
    assert best.ceiling_label != "current"  # else Apply is a no-op and this passes vacuously


def test_option_exercises_page_hosts_the_exercise_half() -> None:
    at = AppTest.from_function(_render_options_page, default_timeout=60)
    at.run()
    assert not at.exception
    assert any("exercise timing" in e.label for e in at.expander)


def _spy_optimizer(monkeypatch) -> list[dict]:
    import views.optimizer_panel as panel

    calls: list[dict] = []
    real = panel.optimize_exercises

    def spy(hh, **kwargs):
        calls.append(kwargs)
        return real(hh, **kwargs)

    monkeypatch.setattr("views.optimizer_panel.optimize_exercises", spy)
    return calls


def test_run_passes_projection_ytd_when_toggle_on(monkeypatch) -> None:
    from models.ytd_income import YTDSnapshot

    calls = _spy_optimizer(monkeypatch)
    at = _app()
    at.session_state["apply_ytd_to_projection"] = True
    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=12_300.0)
    at.run()
    _click(at, "Run optimizer")

    assert not at.exception
    assert calls[-1]["ytd"].nqo_exercise_ytd == 12_300.0


def test_run_passes_no_ytd_when_toggle_off(monkeypatch) -> None:
    from models.ytd_income import YTDSnapshot

    calls = _spy_optimizer(monkeypatch)
    at = _app()
    at.session_state["apply_ytd_to_projection"] = False
    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=12_300.0)
    at.run()
    _click(at, "Run optimizer")

    assert calls[-1]["ytd"] is None


def test_result_goes_stale_when_actuals_change() -> None:
    from models.ytd_income import YTDSnapshot

    at = _app()
    at.session_state["apply_ytd_to_projection"] = True
    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026, wages_ytd=10_000.0)
    at.run()
    _click(at, "Run optimizer")
    assert "Apply conversions" in [b.label for b in at.button]

    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=2026, wages_ytd=90_000.0)
    at.run()
    assert "Apply conversions" not in [b.label for b in at.button]


def test_apply_conversions_keeps_the_result_for_the_exercise_half() -> None:
    """Applying the winner's own conversions is not a stale plan: one run
    still feeds both halves, so Apply exercises stays available."""
    at = _app()
    at.run()
    _click(at, "Run optimizer")
    _click(at, "Apply conversions")

    assert not at.exception
    labels = [b.label for b in at.button]
    assert "Apply exercises" in labels
    assert "Apply conversions" in labels
    assert any("Winning Strategy" in m.value for m in at.markdown)


def _render_persistent_hh() -> None:
    """Both halves over one Household kept in session state, so a test can
    edit it like a Profile edit (and in-place mutation persists like in app)."""
    import streamlit as st

    from models.grants import StockGrant
    from models.household import Household
    from views.optimizer_panel import render_conversion_half, render_exercise_half

    if "_test_hh" not in st.session_state:
        st.session_state["_test_hh"] = Household(
            your_age=60,
            spouse_age=54,
            base_year=2026,
            your_ira=1_000_000,
            spouse_ira=1_000_000,
            stock_price_now=200.0,
            grants=[
                StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")
            ],
        )
    hh = st.session_state["_test_hh"]
    render_conversion_half(hh)
    render_exercise_half(hh)


def _ran_app() -> AppTest:
    at = AppTest.from_function(_render_persistent_hh, default_timeout=60)
    at.run()
    _click(at, "Run optimizer")
    assert "Apply conversions" in [b.label for b in at.button]
    return at


def test_result_survives_a_plain_rerun() -> None:
    at = _ran_app()
    at.run()
    assert not at.exception
    assert "Apply conversions" in [b.label for b in at.button]
    assert "Apply exercises" in [b.label for b in at.button]


@pytest.mark.parametrize(
    ("field", "value"),
    [("your_ira", 1_250_000), ("stock_price_now", 260.0), ("filing_status", "Single")],
)
def test_result_goes_stale_when_household_changes(field: str, value: object) -> None:
    at = _ran_app()
    setattr(at.session_state["_test_hh"], field, value)
    at.run()
    assert not at.exception
    labels = [b.label for b in at.button]
    assert "Apply conversions" not in labels
    assert "Apply exercises" not in labels


def test_result_survives_in_place_schedule_migration() -> None:
    """A legacy ``year:strike`` key in the stored schedule is migrated in
    place by effective_schedule(); that must not strand the result's own
    signature, on the run render or the next one."""
    from models.exercise_schedule import ExerciseSchedule
    from models.grants import StockGrant

    at = AppTest.from_function(_render_persistent_hh, default_timeout=60)
    at.run()
    hh = at.session_state["_test_hh"]
    hh.grants = [StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030)]
    hh.exercise_schedule = ExerciseSchedule(shares_by_grant_year={"2019:100": {2027: 100}})
    at.run()
    _click(at, "Run optimizer")
    assert list(hh.exercise_schedule.shares_by_grant_year) == ["2019:100:2030"]  # migrated
    assert "Apply conversions" in [b.label for b in at.button]
    at.run()
    assert not at.exception
    assert "Apply conversions" in [b.label for b in at.button]


def test_apply_exercises_keeps_the_result(monkeypatch) -> None:
    """Applying the winner's schedule writes it into hh; that is not a stale
    household, so both halves keep the result."""
    monkeypatch.setattr("views.optimizer_panel.save_exercise_schedule", lambda schedule: None)
    at = _ran_app()
    _click(at, "Apply exercises")
    for _ in range(2):
        assert not at.exception
        labels = [b.label for b in at.button]
        assert "Apply exercises" in labels
        assert "Apply conversions" in labels
        at.run()


def test_result_goes_stale_when_current_plan_changes() -> None:
    at = _app()
    at.run()
    _click(at, "Run optimizer")
    assert "Apply conversions" in [b.label for b in at.button]

    at.session_state["conv_plan_your"] = {2026: 12_345.0}
    at.run()
    assert "Apply conversions" not in [b.label for b in at.button]
