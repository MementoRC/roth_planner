"""Per-ticker qualified-vs-ordinary dividend breakdown (YTD page roadmap item)."""

import pytest

from engine.dividend_breakdown import qualified_breakdown
from engine.dividend_forecast import Position, forecast_portfolio


class TestQualifiedBreakdown:
    def test_split_math(self):
        bd = qualified_breakdown({"ACME": {"annual_div": 1000.0, "qualified": 0.75}})
        (row,) = bd.rows
        assert row.ticker == "ACME"
        assert row.annual_div == pytest.approx(1000.0)
        assert row.qualified == pytest.approx(750.0)
        assert row.ordinary == pytest.approx(250.0)
        assert row.qualified_pct == pytest.approx(0.75)

    def test_fraction_zero_and_one(self):
        bd = qualified_breakdown(
            {
                "FOO": {"annual_div": 400.0, "qualified": 0.0},
                "ACME": {"annual_div": 600.0, "qualified": 1.0},
            }
        )
        by_ticker = {r.ticker: r for r in bd.rows}
        assert by_ticker["FOO"].qualified == 0.0
        assert by_ticker["FOO"].ordinary == pytest.approx(400.0)
        assert by_ticker["ACME"].qualified == pytest.approx(600.0)
        assert by_ticker["ACME"].ordinary == 0.0

    def test_sorted_by_dividend_descending_then_ticker(self):
        bd = qualified_breakdown(
            {
                "FOO": {"annual_div": 100.0, "qualified": 0.5},
                "ACME": {"annual_div": 500.0, "qualified": 0.5},
                "BAR": {"annual_div": 100.0, "qualified": 0.5},
            }
        )
        assert [r.ticker for r in bd.rows] == ["ACME", "BAR", "FOO"]

    def test_totals_row(self):
        bd = qualified_breakdown(
            {
                "ACME": {"annual_div": 1000.0, "qualified": 0.5},
                "FOO": {"annual_div": 1000.0, "qualified": 1.0},
            }
        )
        assert bd.total_annual_div == pytest.approx(2000.0)
        assert bd.total_qualified == pytest.approx(1500.0)
        assert bd.total_ordinary == pytest.approx(500.0)
        assert bd.total_qualified_pct == pytest.approx(0.75)

    def test_empty_has_no_rows_and_zero_totals(self):
        bd = qualified_breakdown({})
        assert bd.rows == ()
        assert bd.total_annual_div == 0.0
        assert bd.total_qualified_pct == 0.0

    def test_zero_dividend_ticker_is_dropped(self):
        bd = qualified_breakdown({"ACME": {"annual_div": 0.0, "qualified": 1.0}})
        assert bd.rows == ()

    def test_fraction_clamped_to_unit_interval(self):
        bd = qualified_breakdown({"ACME": {"annual_div": 100.0, "qualified": 1.5}})
        assert bd.rows[0].qualified == pytest.approx(100.0)
        assert bd.rows[0].ordinary == 0.0

    def test_totals_match_forecast_qualified_fraction(self, tmp_path):
        positions = [
            Position(ticker="ACME", shares=100, balance=10_000, ttm_dividends=300.0),
            Position(ticker="FOO", shares=50, balance=5_000, ttm_dividends=200.0),
        ]
        fcst = forecast_portfolio(
            positions,
            total_balance=15_000,
            overrides_path=tmp_path / "none.json",
            employer_ticker="ACME",
        )
        bd = qualified_breakdown(fcst.per_position)
        assert bd.total_annual_div == pytest.approx(500.0)
        assert bd.total_qualified_pct == pytest.approx(fcst.qualified_fraction)
        assert bd.total_qualified + bd.total_ordinary == pytest.approx(bd.total_annual_div)
