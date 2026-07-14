"""Tests for models.exercise_schedule — ExerciseSchedule + StockGrant helpers."""

import pytest

from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant


def approx(expected, tol=1e-6):
    return pytest.approx(expected, abs=tol)


def make_grants():
    return [
        StockGrant(year=2019, strike=104.0, shares=5000, expiry_year=2029, grant_id="g19"),
        StockGrant(year=2020, strike=130.0, shares=4000, expiry_year=2030, grant_id="g20"),
        StockGrant(year=2021, strike=169.0, shares=3000, expiry_year=2031, grant_id="g21"),
    ]


class TestStockGrantHelpers:
    def test_per_share_spread_in_the_money(self):
        g = StockGrant(year=2019, strike=100.0, shares=100, expiry_year=2029)
        assert g.per_share_spread(150.0) == approx(50.0)

    def test_per_share_spread_out_of_the_money(self):
        g = StockGrant(year=2019, strike=100.0, shares=100, expiry_year=2029)
        assert g.per_share_spread(80.0) == approx(0.0)

    def test_per_share_spread_at_the_money(self):
        g = StockGrant(year=2019, strike=100.0, shares=100, expiry_year=2029)
        assert g.per_share_spread(100.0) == approx(0.0)

    def test_key_uses_grant_id_when_set(self):
        g = StockGrant(year=2019, strike=104.0, shares=100, expiry_year=2029, grant_id="abc123")
        assert g.key() == "abc123"

    def test_key_falls_back_to_year_strike_when_no_grant_id(self):
        g = StockGrant(year=2019, strike=104.0, shares=100, expiry_year=2029)
        assert g.key() == "2019:104"

    def test_key_fallback_formats_strike_with_g(self):
        g = StockGrant(year=2020, strike=130.5, shares=100, expiry_year=2030)
        assert g.key() == "2020:130.5"


class TestIncomeFor:
    def test_single_partial_exercise(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1000)
        sched.set_price(2026, 154.0)
        # spread/share = 154 - 104 = 50; 1000 shares -> 50000
        assert sched.income_for(2026, grants) == approx(50000.0)

    def test_multi_grant_multi_year_summation(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 5000)
        sched.set_shares("g20", 2026, 2000)
        sched.set_price(2026, 200.0)
        # g19: (200-104)*5000 = 480000; g20: (200-130)*2000 = 140000
        assert sched.income_for(2026, grants) == approx(620000.0)

    def test_per_cell_clamp_to_grant_shares(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        # Malformed cache: schedule more shares than the grant has.
        sched.set_shares("g19", 2026, 999_999)
        sched.set_price(2026, 154.0)
        # Clamped to grant.shares=5000: 50 * 5000 = 250000
        assert sched.income_for(2026, grants) == approx(250000.0)

    def test_expiry_guard_zeros_income_past_expiry(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        # g19 expires 2029; schedule shares in 2030 anyway.
        sched.shares_by_grant_year["g19"] = {2030: 1000}
        sched.set_price(2030, 300.0)
        assert sched.income_for(2030, grants) == approx(0.0)

    def test_missing_price_contributes_zero(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1000)
        # No price set for 2026.
        assert sched.income_for(2026, grants) == approx(0.0)

    def test_no_shares_scheduled_yields_zero(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_price(2026, 200.0)
        assert sched.income_for(2026, grants) == approx(0.0)


class TestSharesAccessors:
    def test_shares_default_zero(self):
        sched = ExerciseSchedule()
        assert sched.shares("g19", 2026) == 0

    def test_set_shares_then_get(self):
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1500)
        assert sched.shares("g19", 2026) == 1500

    def test_set_shares_zero_removes_entry(self):
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1500)
        sched.set_shares("g19", 2026, 0)
        assert sched.shares("g19", 2026) == 0
        assert "g19" not in sched.shares_by_grant_year

    def test_set_shares_negative_removes_entry(self):
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1500)
        sched.set_shares("g19", 2026, -5)
        assert "g19" not in sched.shares_by_grant_year

    def test_total_exercised_sums_years(self):
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1000)
        sched.set_shares("g19", 2027, 2000)
        assert sched.total_exercised("g19") == 3000

    def test_total_exercised_unknown_key_zero(self):
        sched = ExerciseSchedule()
        assert sched.total_exercised("nope") == 0

    def test_remaining(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1000)
        assert sched.remaining(grants[0]) == 4000

    def test_price_fallback(self):
        sched = ExerciseSchedule()
        assert sched.price(2026, fallback=123.0) == 123.0

    def test_price_stored_overrides_fallback(self):
        sched = ExerciseSchedule()
        sched.set_price(2026, 200.0)
        assert sched.price(2026, fallback=123.0) == 200.0

    def test_price_no_fallback_returns_none(self):
        sched = ExerciseSchedule()
        assert sched.price(2026) is None


class TestIsEmpty:
    def test_empty_schedule_is_empty(self):
        assert ExerciseSchedule().is_empty() is True

    def test_schedule_with_shares_not_empty(self):
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 100)
        assert sched.is_empty() is False

    def test_price_only_schedule_still_empty(self):
        sched = ExerciseSchedule()
        sched.set_price(2026, 200.0)
        assert sched.is_empty() is True


class TestValidate:
    def test_valid_schedule_returns_empty_list(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1000)
        sched.set_price(2026, 154.0)
        assert sched.validate(grants, base_year=2026) == []

    def test_over_exercise_flagged(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 4000)
        sched.set_shares("g19", 2027, 4000)  # sum 8000 > 5000 shares
        msgs = sched.validate(grants, base_year=2026)
        assert any("g19" in m for m in msgs)

    def test_year_before_base_year_flagged(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2020, 100)
        msgs = sched.validate(grants, base_year=2026)
        assert len(msgs) >= 1

    def test_year_after_expiry_flagged(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2030, 100)  # g19 expires 2029
        msgs = sched.validate(grants, base_year=2026)
        assert len(msgs) >= 1

    def test_negative_shares_flagged(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        # bypass set_shares' auto-prune to inject a malformed negative value directly
        sched.shares_by_grant_year["g19"] = {2026: -50}
        msgs = sched.validate(grants, base_year=2026)
        assert len(msgs) >= 1

    def test_negative_price_flagged(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_price(2026, -10.0)
        msgs = sched.validate(grants, base_year=2026)
        assert len(msgs) >= 1


class TestToDictFromDict:
    def test_round_trip_preserves_values(self):
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1000)
        sched.set_price(2026, 154.0)
        d = sched.to_dict()
        restored = ExerciseSchedule.from_dict(d)
        assert restored.shares("g19", 2026) == 1000
        assert restored.price(2026) == approx(154.0)

    def test_round_trip_year_keys_are_ints(self):
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1000)
        sched.set_price(2027, 200.0)
        d = sched.to_dict()
        restored = ExerciseSchedule.from_dict(d)
        for grant_years in restored.shares_by_grant_year.values():
            for year in grant_years:
                assert isinstance(year, int)
        for year in restored.price_by_year:
            assert isinstance(year, int)

    def test_to_dict_has_version(self):
        sched = ExerciseSchedule()
        d = sched.to_dict()
        assert d["version"] == 1


class TestContentKeyStability:
    """Regression pin for #369: keys are content-based, not positional."""

    def test_income_for_unchanged_when_grants_reordered(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_shares("g19", 2026, 1000)
        sched.set_shares("g21", 2026, 500)
        sched.set_price(2026, 200.0)

        forward = sched.income_for(2026, grants)
        reversed_grants = list(reversed(grants))
        backward = sched.income_for(2026, reversed_grants)

        assert forward == approx(backward)
        assert forward > 0

    def test_income_for_unchanged_when_grants_shuffled_and_compacted(self):
        grants = make_grants()
        sched = ExerciseSchedule()
        sched.set_shares("g20", 2026, 1500)
        sched.set_price(2026, 200.0)

        original = sched.income_for(2026, grants)
        # Simulate a FinExtract compaction: drop g19, reorder remaining.
        compacted = [grants[2], grants[1]]
        after = sched.income_for(2026, compacted)

        assert original == approx(after)


class TestDefaultFromLegacy:
    def test_distinct_year_grants_map_to_base_year_offsets(self):
        grants = make_grants()
        price_now = 200.0
        sched = ExerciseSchedule.default_from_legacy(grants, base_year=2026, price_now=price_now)

        assert sched.income_for(2026, grants) == approx(grants[0].spread(price_now))
        assert sched.income_for(2027, grants) == approx(grants[1].spread(price_now))
        assert sched.income_for(2028, grants) == approx(grants[2].spread(price_now))

    def test_off_target_years_yield_zero(self):
        grants = make_grants()
        sched = ExerciseSchedule.default_from_legacy(grants, base_year=2026, price_now=200.0)
        assert sched.income_for(2029, grants) == approx(0.0)

    def test_empty_grants_yields_empty_schedule(self):
        sched = ExerciseSchedule.default_from_legacy([], base_year=2026, price_now=200.0)
        assert sched.is_empty() is True
