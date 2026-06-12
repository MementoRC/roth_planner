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
        """Render with by_grant breakdown and grants without grant_id — single table, all unmatched."""
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

        # Now a single st.dataframe call (joined table) instead of two separate tables
        # All rows show Year="—" since grants have no grant_id to match
        dataframe_calls = mock_st.dataframe.call_args_list
        joined_call = next(
            (c for c in dataframe_calls if isinstance(c.args[0] if c.args else c.kwargs.get("data"), list)),
            None,
        )
        assert joined_call is not None, "Expected at least one st.dataframe call with a list of rows"
        rows = joined_call.args[0] if joined_call.args else joined_call.kwargs["data"]
        assert all(r["Year"] == "—" for r in rows), (
            f"All rows should be unmatched (Year='—') since grants have no grant_id; got: {rows}"
        )

    def test_per_grant_table_joins_when_grant_id_matches(self):
        """Single joined table uses grant data when grant_id matches."""
        grants = [
            StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"),
        ]
        hh = _stub_hh(grants=grants)
        ytd = YTDSnapshot(nqo_exercise_ytd=96_000.0)
        ytd._option_exercises_by_grant = {"GR-2019": 96_000.0}  # noqa: SLF001
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        dataframe_calls = mock_st.dataframe.call_args_list
        joined_call = next(
            (c for c in dataframe_calls if isinstance(c.args[0] if c.args else c.kwargs.get("data"), list)),
            None,
        )
        assert joined_call is not None, "Expected st.dataframe call with list of rows"
        rows = joined_call.args[0] if joined_call.args else joined_call.kwargs["data"]
        assert len(rows) == 1
        assert rows[0]["Year"] == "2019", f"Matched row should show Year='2019' (str); got: {rows[0]}"
        assert rows[0]["Strike"] == "$104.00"
        assert rows[0]["Grant #"] == "GR-2019"

    def test_per_grant_table_year_column_is_string(self):
        """Year/Shares/Expiry columns must be strings to avoid PyArrow ArrowTypeError."""
        grants = [
            StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="GR-2019"),
        ]
        hh = _stub_hh(grants=grants)
        ytd = YTDSnapshot(nqo_exercise_ytd=96_000.0)
        ytd._option_exercises_by_grant = {"GR-2019": 96_000.0}  # noqa: SLF001
        mock_st = _make_mock_st(ytd)

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch("engine.portfolio_sync.save_ytd_snapshot"),
        ):
            ytd_income_mod.render(hh)

        dataframe_calls = mock_st.dataframe.call_args_list
        joined_call = next(
            (c for c in dataframe_calls if isinstance(c.args[0] if c.args else c.kwargs.get("data"), list)),
            None,
        )
        assert joined_call is not None
        rows = joined_call.args[0] if joined_call.args else joined_call.kwargs["data"]
        assert len(rows) == 1
        row = rows[0]
        # Verify Year, Shares, Expiry are all strings (not int)
        assert isinstance(row["Year"], str), f"Year should be str, got {type(row['Year'])}: {row['Year']}"
        assert isinstance(row["Shares"], str), f"Shares should be str, got {type(row['Shares'])}: {row['Shares']}"
        assert isinstance(row["Expiry"], str), f"Expiry should be str, got {type(row['Expiry'])}: {row['Expiry']}"
        assert row["Year"] == "2019"
        assert row["Shares"] == "1000"
        assert row["Expiry"] == "2029"
