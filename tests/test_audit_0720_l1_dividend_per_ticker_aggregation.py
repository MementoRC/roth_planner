"""Regression test for audit-0720 finding L1.

forecast_portfolio's per_position dict must aggregate same-ticker Position
records (e.g. both spouses' accounts), not overwrite.
"""

from __future__ import annotations

import pytest

from engine.dividend_forecast import Position, forecast_portfolio


class TestL1DividendForecastAggregatesPerTicker:
    def test_same_ticker_across_two_positions_sums_annual_div(self) -> None:
        positions = [
            Position(ticker="TXN", shares=100, balance=20_000, ttm_dividends=540.0),
            Position(ticker="TXN", shares=400, balance=80_000, ttm_dividends=2_160.0),
        ]

        fcst = forecast_portfolio(positions, total_balance=100_000)

        assert fcst.per_position["TXN"]["annual_div"] == pytest.approx(2_700.0)
        assert fcst.yield_rate == pytest.approx(0.027)
