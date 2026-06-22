"""Tests for models.grants — StockGrant."""

import pytest

from config.defaults import DEFAULTS
from engine.scenario_compute import compute_phase
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


class TestComputePhaseNqoWindow:
    """Characterization: compute_phase returns 'options' in exactly 2026-2028."""

    def test_options_phase_active_in_nqo_window(self):
        # Default Household has 3 TXN grants mapped to 2026, 2027, 2028.
        hh = Household()
        base = hh.base_year  # 2026
        for offset, year in enumerate(range(base, base + 3)):
            ya = hh.your_age + offset
            sa = hh.spouse_age + offset
            phase = compute_phase(ya, sa, year, hh, early_exercise=True)
            assert phase == "options", f"Expected 'options' in {year}, got {phase!r}"

    def test_options_phase_inactive_after_nqo_window(self):
        # Year 2029 (base_year + 3) has no grant → must not return 'options'.
        hh = Household()
        base = hh.base_year  # 2026
        year = base + 3  # 2029
        ya = hh.your_age + 3
        sa = hh.spouse_age + 3
        phase = compute_phase(ya, sa, year, hh, early_exercise=True)
        assert phase != "options", f"Expected no 'options' phase in {year}, got {phase!r}"
