"""Smoke tests for views/ytd_income.py — NQO exercises display (PR3)."""

from unittest.mock import MagicMock, patch

import views.ytd_income as ytd_income_mod
from models.grants import StockGrant
from models.household import Household
from models.ytd_income import YTDSnapshot


def _stub_hh(**kwargs) -> Household:
    return Household(
        your_age=61,
        spouse_age=55,
        your_ira=500_000,
        spouse_ira=500_000,
        **kwargs,
    )


def _make_mock_st(ytd: YTDSnapshot) -> MagicMock:
    """Build a streamlit mock whose session_state returns the given YTDSnapshot."""
    mock_st = MagicMock()
    # session_state needs .get() like a dict AND supports attribute assignment.
    session_state = MagicMock()
    _state: dict = {"ytd_snapshot": ytd, "apply_ytd_to_projection": False}
    session_state.get.side_effect = lambda key, default=None: _state.get(key, default)
    mock_st.session_state = session_state
    # number_input returns 0 so manual-entry YTDSnapshot construction is numeric
    mock_st.number_input.return_value = 0
    # checkbox returns False so the manual-entry block is skipped
    mock_st.checkbox.return_value = False
    # columns returns MagicMocks that support context-manager and attribute access
    def _columns_side_effect(arg):
        n = arg if isinstance(arg, int) else len(arg)
        return [MagicMock() for _ in range(n)]

    mock_st.columns.side_effect = _columns_side_effect
    mock_st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_st.expander.return_value.__exit__ = MagicMock(return_value=False)
    mock_st.button.return_value = False
    return mock_st


class TestYtdIncomeNqoDisplay:
    """View-layer smoke tests for NQO exercises metric and per-grant breakdown."""

    def test_renders_with_nqo_exercises(self):
        """Render with nqo_exercise_ytd set — should not raise; NQO metric present."""
        hh = _stub_hh()
        ytd = YTDSnapshot(nqo_exercise_ytd=96_000.0)
        ytd._option_exercises_by_grant = {"GR-2019": 96_000.0}  # noqa: SLF001
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        metric_labels = [
            (call.args[0] if call.args else call.kwargs.get("label", ""))
            for call in mock_st.metric.call_args_list
        ]
        assert any("NQO" in lbl for lbl in metric_labels), (
            f"Expected st.metric call with 'NQO' label; got: {metric_labels}"
        )

    def test_renders_empty_no_nqo_section(self):
        """Render with nqo_exercise_ytd=0 — should not raise; NQO metric absent."""
        hh = _stub_hh()
        ytd = YTDSnapshot(nqo_exercise_ytd=0.0)
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        metric_labels = [
            (call.args[0] if call.args else call.kwargs.get("label", ""))
            for call in mock_st.metric.call_args_list
        ]
        assert not any("NQO" in lbl for lbl in metric_labels), (
            "NQO metric should not appear when nqo_exercise_ytd=0"
        )

    def test_per_grant_table_with_household_grants(self):
        """Render with by_grant breakdown and household grants — both tables present."""
        grants = [
            StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2029),
            StockGrant(year=2020, strike=130.0, shares=500, expiry_year=2030),
            StockGrant(year=2021, strike=169.0, shares=300, expiry_year=2031),
        ]
        hh = _stub_hh(grants=grants)
        ytd = YTDSnapshot(nqo_exercise_ytd=96_000.0)
        ytd._option_exercises_by_grant = {  # noqa: SLF001
            "GR-2019": 80_000.0,
            "GR-2020": 16_000.0,
        }
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        # Expect at least 2 dataframe calls: per-grant rows + household grants context
        assert mock_st.dataframe.call_count >= 2, (
            f"Expected >=2 st.dataframe calls; got {mock_st.dataframe.call_count}"
        )
