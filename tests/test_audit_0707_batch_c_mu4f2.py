"""Regression tests for audit 0707 finding MU4-F2 — per-category $50 CPI rounding.

Statutory inflation-adjusted amounts (ordinary brackets, standard/senior
deductions, LTCG breakpoints) are rounded to the nearest $50 per IRC §1(f)(6).
That rounding is opt-in via the ``round50`` flag on the tax_indexing helpers and
must NOT leak onto IRMAA/FPL/ACA/QCD/contribution/catch-up/phase-out amounts,
which use different statutory rounding. Base-year (2026) values stay unrounded
because the published 2026 constants are already official.
"""

from __future__ import annotations

import math

import pytest

from engine.tax_indexing import (
    BASE_YEAR,
    _round_to_nearest_50,
    index_bracket_list,
    index_tuple,
    index_value,
)


class TestRoundToNearest50:
    def test_exact_multiple_unchanged(self):
        assert _round_to_nearest_50(100.0) == 100.0
        assert _round_to_nearest_50(0.0) == 0.0

    def test_rounds_down_below_25(self):
        assert _round_to_nearest_50(124.0) == 100.0

    def test_rounds_up_above_25(self):
        assert _round_to_nearest_50(126.0) == 150.0

    def test_half_step_25_rounds_up(self):
        # exact $25 half-step rounds UP to the next $50 (IRC §1(f)(6))
        assert _round_to_nearest_50(25.0) == 50.0
        assert _round_to_nearest_50(75.0) == 100.0

    def test_infinite_returned_unchanged(self):
        assert _round_to_nearest_50(float("inf")) == float("inf")
        assert _round_to_nearest_50(float("-inf")) == float("-inf")


class TestIndexValueRound50:
    def test_default_is_unrounded(self):
        v = index_value(100_000.0, 2030, 0.025)
        assert v == pytest.approx(100_000.0 * (1.025**4))
        assert v % 50 != 0  # proves no rounding on the default path

    def test_round50_true_lands_on_50_multiple(self):
        v = index_value(100_000.0, 2030, 0.025, round50=True)
        assert v % 50 == 0
        assert v == _round_to_nearest_50(100_000.0 * (1.025**4))

    def test_base_year_unrounded_even_with_round50(self):
        assert index_value(98_925.0, BASE_YEAR, 0.025, round50=True) == 98_925.0
        assert index_value(98_925.0, 2020, 0.025, round50=True) == 98_925.0

    def test_round50_matches_manual_computation(self):
        base, year, cpi = 24_800.0, 2035, 0.03
        scaled = base * (1.0 + cpi) ** (year - BASE_YEAR)
        assert index_value(base, year, cpi, round50=True) == math.floor(scaled / 50.0 + 0.5) * 50.0


class TestIndexTupleAndBracketListRound50:
    def test_index_tuple_round50_all_elements_on_50(self):
        out = index_tuple((98_900.0, 613_700.0), 2032, 0.025, round50=True)
        assert all(x % 50 == 0 for x in out)

    def test_index_tuple_default_unrounded(self):
        out = index_tuple((98_900.0, 613_700.0), 2032, 0.025)
        assert any(x % 50 != 0 for x in out)

    def test_bracket_list_round50_finite_ceilings_on_50(self):
        brackets = [(24_800.0, 0.10), (100_800.0, 0.12), (float("inf"), 0.37)]
        out = index_bracket_list(brackets, 2033, 0.025, round50=True)
        for ceil, _rate in out:
            if math.isfinite(ceil):
                assert ceil % 50 == 0

    def test_bracket_list_round50_preserves_inf_ceiling(self):
        # the open-ended top bracket must not raise (math.floor(inf) would)
        brackets = [(24_800.0, 0.10), (float("inf"), 0.37)]
        out = index_bracket_list(brackets, 2040, 0.025, round50=True)
        assert out[-1][0] == float("inf")
        assert out[-1][1] == 0.37

    def test_bracket_list_preserves_rates(self):
        brackets = [(24_800.0, 0.10), (100_800.0, 0.12), (float("inf"), 0.37)]
        out = index_bracket_list(brackets, 2033, 0.025, round50=True)
        assert [r for _c, r in out] == [0.10, 0.12, 0.37]


class TestCategoryIsolation:
    """round50 must NOT be applied to non-$50 categories (default path)."""

    def test_irmaa_like_value_unrounded_by_default(self):
        v = index_value(206_000.0, 2030, 0.025)
        assert v == pytest.approx(206_000.0 * (1.025**4))
        assert v % 50 != 0


class TestTaxPyWiring:
    """Prove tax.py actually threads round50=True into the bracket walk."""

    def test_federal_tax_matches_round50_brackets(self):
        from engine.tax import BRACKETS_MFJ, federal_tax

        year, cpi, ti = 2035, 0.025, 300_000.0
        brackets = index_bracket_list(BRACKETS_MFJ, year, cpi, round50=True)
        ref, prev = 0.0, 0.0
        for ceil, rate in brackets:
            chunk = min(ti, ceil) - prev
            if chunk <= 0:
                break
            ref += chunk * rate
            prev = ceil
        assert federal_tax(ti, year=year, cpi=cpi) == pytest.approx(ref)

    def test_federal_tax_differs_from_unrounded_future_year(self):
        # guards against the round50 flag being dropped from tax.py
        from engine.tax import BRACKETS_MFJ, federal_tax

        year, cpi, ti = 2035, 0.025, 300_000.0
        unrounded = [(c * (1.025 ** (year - 2026)), r) for c, r in BRACKETS_MFJ]
        ref, prev = 0.0, 0.0
        for ceil, rate in unrounded:
            chunk = min(ti, ceil) - prev
            if chunk <= 0:
                break
            ref += chunk * rate
            prev = ceil
        assert federal_tax(ti, year=year, cpi=cpi) != pytest.approx(ref, abs=0.5)

    def test_base_year_federal_tax_unaffected(self):
        # 2026 tax must be identical to pre-MU4-F2 behavior (no rounding at base year)
        from engine.tax import BRACKETS_MFJ, federal_tax

        ti = 300_000.0
        ref, prev = 0.0, 0.0
        for ceil, rate in BRACKETS_MFJ:
            chunk = min(ti, ceil) - prev
            if chunk <= 0:
                break
            ref += chunk * rate
            prev = ceil
        assert federal_tax(ti, year=2026) == pytest.approx(ref)
