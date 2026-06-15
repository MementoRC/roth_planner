"""Tests for models.grants — StockGrant."""

import pytest

from config.defaults import DEFAULTS
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestGrants:
    def test_grant_spreads(self):
        hh = Household()
        price = DEFAULTS["stock_price_now"]
        for i, g in enumerate(hh.grants):
            expected = DEFAULTS["grants"][i]
            assert g.spread(price) == approx(expected.spread(price))

    def test_total_spread(self):
        hh = Household()
        price = DEFAULTS["stock_price_now"]
        total = sum(g.spread(price) for g in hh.grants)
        expected = sum(g.spread(price) for g in DEFAULTS["grants"])
        assert total == approx(expected, tol=10)

    def test_option_income_by_year(self):
        hh = Household()
        assert hh.option_income(2026, True) == approx(hh.grants[0].spread(hh.txn_price_now))
        assert hh.option_income(2027, True) == approx(hh.grants[1].spread(hh.txn_price_now))
        assert hh.option_income(2028, True) == approx(hh.grants[2].spread(hh.txn_price_now))
        assert hh.option_income(2029, True) == 0
