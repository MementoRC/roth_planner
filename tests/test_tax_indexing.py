"""Tests for engine.tax_indexing — CPI inflation indexing helpers."""

import pytest

from engine.tax_indexing import BASE_YEAR, DEFAULT_CPI, index_bracket_list, index_tuple, index_value


class TestIndexValue:
    def test_base_year_returns_base_value(self):
        assert index_value(100_000, BASE_YEAR) == 100_000.0

    def test_year_before_base_returns_the_base_value(self):
        assert index_value(50_000, BASE_YEAR - 5) == 50_000.0

    def test_year_equals_base_year_boundary(self):
        """Exactly BASE_YEAR is not indexed (≤ BASE_YEAR guard)."""
        assert index_value(32_200, 2026) == 32_200.0

    def test_cpi_zero_future_year_unchanged(self):
        """With cpi=0.0, future years never inflate."""
        assert index_value(100_000, 2030, cpi=0.0) == 100_000.0

    def test_cpi_025_four_years_out(self):
        """2026→2030 at 2.5%: factor = 1.025**4 ≈ 1.10381289."""
        expected = 100_000 * (1.025**4)
        result = index_value(100_000, 2030, cpi=0.025)
        assert result == pytest.approx(expected, rel=1e-9)

    def test_cpi_025_one_year_out(self):
        val = index_value(200_000, 2027, cpi=0.025)
        assert val == pytest.approx(200_000 * 1.025, rel=1e-9)

    def test_default_cpi_matches_constant(self):
        """Calling without explicit cpi uses DEFAULT_CPI."""
        result_default = index_value(50_000, 2028)
        result_explicit = index_value(50_000, 2028, cpi=DEFAULT_CPI)
        assert result_default == result_explicit

    def test_zero_base_value_stays_zero(self):
        assert index_value(0.0, 2035) == 0.0


class TestIndexTuple:
    def test_base_year_unchanged(self):
        t = (98_900, 613_700)
        assert index_tuple(t, BASE_YEAR) == t

    def test_future_year_scales_all_elements(self):
        t = (100.0, 200.0)
        result = index_tuple(t, 2027, cpi=0.025)
        assert result == pytest.approx((100.0 * 1.025, 200.0 * 1.025), rel=1e-9)

    def test_tuple_structure_preserved(self):
        t = (1.0, 2.0, 3.0)
        result = index_tuple(t, 2028, cpi=0.0)
        assert len(result) == 3
        assert result == t


class TestIndexBracketList:
    def test_empty_list_returns_empty(self):
        assert index_bracket_list([], 2030) == []

    def test_base_year_ceilings_unchanged(self):
        brackets = [(24_800, 0.10), (100_800, 0.12), (float("inf"), 0.37)]
        result = index_bracket_list(brackets, BASE_YEAR)
        assert result == brackets

    def test_rates_are_preserved(self):
        brackets = [(50_000, 0.10), (100_000, 0.22), (float("inf"), 0.37)]
        result = index_bracket_list(brackets, 2027, cpi=0.025)
        rates = [r for _, r in result]
        assert rates == [0.10, 0.22, 0.37]

    def test_ceilings_indexed_one_year(self):
        brackets = [(100_000, 0.10), (200_000, 0.22)]
        result = index_bracket_list(brackets, 2027, cpi=0.025)
        assert result[0][0] == pytest.approx(100_000 * 1.025, rel=1e-9)
        assert result[1][0] == pytest.approx(200_000 * 1.025, rel=1e-9)

    def test_inf_ceiling_stays_inf(self):
        """float('inf') * any finite factor = inf; must remain the bracket sentinel."""
        brackets = [(100_000, 0.10), (float("inf"), 0.37)]
        result = index_bracket_list(brackets, 2030, cpi=0.025)
        assert result[-1][0] == float("inf")
