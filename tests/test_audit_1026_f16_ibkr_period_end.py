"""audit-1026 F-16: ranged IBKR Activity Statement header yields the range END."""

from engine.brokerage_statement_pdf import _extract_ibkr_period_end


def test_single_date_header_unchanged() -> None:
    assert _extract_ibkr_period_end("Activity Statement - July 9, 2026 Page: 1 of 5") == "2026-07-09"


def test_ranged_header_returns_end_date() -> None:
    text = "Activity Statement\nJanuary 1, 2026 - March 31, 2026\nPage: 1 of 5"
    assert _extract_ibkr_period_end(text) == "2026-03-31"


def test_ranged_header_same_line() -> None:
    text = "Activity Statement January 1, 2026 - March 31, 2026 Page: 1"
    assert _extract_ibkr_period_end(text) == "2026-03-31"
