"""Audit-0706 wave-2 regression tests for engine/portfolio_sync/exercises.py.

psync-equity-1 (medium) / psync-equity-3 (low): _parse_equity_sales_lots must
preserve fractional-share precision when computing the spread; only shares_ytd
(a display/accumulation counter) should be truncated to int.

The sibling _parse_option_exercises_rows already uses float precision throughout.
"""

from __future__ import annotations

import pytest

from engine.portfolio_sync.exercises import (
    _parse_equity_sales_lots,
    _parse_option_exercises_rows,
)


class TestEquitySalesLotsSpreadPrecision:
    """psync-equity-1 / psync-equity-3: fractional-share spread precision."""

    # grant_price=$169, qty=10.7 shares, gross=$1900.00
    # Correct spread  = 1900.00 - (169 * 10.7) = 1900.00 - 1808.30 = 91.70
    # Truncated spread = 1900.00 - (169 * 10)  = 1900.00 - 1690.00 = 210.00  (WRONG)
    LOT_FRACTIONAL: dict = {
        "grant_number": "TEST001",
        "grant_price": 169.0,
        "execution_quantity": "10.7",  # string numeric as FinExtract returns
        "gross_proceeds": 1900.00,
        "grant_date": "2021-06-01",
    }
    EXPECTED_SPREAD = 1900.00 - (169.0 * 10.7)  # 91.70
    TRUNCATED_SPREAD = 1900.00 - (169.0 * 10.0)  # 210.00 — the wrong answer

    def test_spread_uses_float_quantity_not_int(self) -> None:
        """spread = gross - (grant_price * qty_float), NOT qty_int."""
        snap = _parse_equity_sales_lots([self.LOT_FRACTIONAL])
        assert snap.total_spread == pytest.approx(self.EXPECTED_SPREAD, abs=1e-9)
        assert snap.total_spread != pytest.approx(self.TRUNCATED_SPREAD, abs=1e-9)

    def test_shares_ytd_is_still_truncated_to_int(self) -> None:
        """shares_ytd display counter must still be int (floor of qty)."""
        snap = _parse_equity_sales_lots([self.LOT_FRACTIONAL])
        grant_info = snap.sale_info_by_grant.get("TEST001", {})
        assert grant_info.get("shares_ytd") == 10  # int(10.7) == 10

    def test_spread_matches_sibling_parser_for_integer_qty(self) -> None:
        """Sanity: integer qty gives identical result in both parsers."""
        lot = {
            "grant_number": "G42",
            "grant_price": 130.0,
            "execution_quantity": "50",
            "gross_proceeds": 8500.0,
            "grant_date": "2020-03-15",
        }
        row = {
            "grant_number": "G42",
            "grant_price": 130.0,
            "execution_quantity": 50,  # already numeric (as sibling receives)
            "gross_proceeds": 8500.0,
            "grant_date": "2020-03-15",
        }
        snap_lots = _parse_equity_sales_lots([lot])
        snap_rows = _parse_option_exercises_rows([row])
        assert snap_lots.total_spread == pytest.approx(snap_rows.total_spread)

    def test_multiple_fractional_lots_accumulate_correctly(self) -> None:
        """Multiple fractional lots: each spread uses float qty; shares_ytd sums int parts."""
        lots = [
            {
                "grant_number": "G99",
                "grant_price": 100.0,
                "execution_quantity": "5.3",
                "gross_proceeds": 600.0,
                "grant_date": "2021-01-01",
            },
            {
                "grant_number": "G99",
                "grant_price": 100.0,
                "execution_quantity": "4.8",
                "gross_proceeds": 550.0,
                "grant_date": "2021-01-01",
            },
        ]
        # spread1 = 600 - (100*5.3) = 600 - 530 = 70
        # spread2 = 550 - (100*4.8) = 550 - 480 = 70
        snap = _parse_equity_sales_lots(lots)
        assert snap.total_spread == pytest.approx(140.0, abs=1e-9)
        grant_info = snap.sale_info_by_grant.get("G99", {})
        # shares_ytd = int(5.3) + int(4.8) = 5 + 4 = 9
        assert grant_info.get("shares_ytd") == 9
