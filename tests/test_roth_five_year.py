"""Unit tests for the Roth conversion 5-year ledger and its waterfall penalty."""

from __future__ import annotations

import pytest

from engine.roth_five_year import RothConversionLedger, RothExposure
from engine.withdrawal_waterfall import Accounts, solve_waterfall


def _no_tax(_: float) -> float:
    return 0.0


def _roth_accounts(roth: float, exposure: RothExposure) -> Accounts:
    return Accounts(
        brokerage=0.0,
        brokerage_basis_fraction=1.0,
        your_ira=0.0,
        spouse_ira=0.0,
        your_roth=roth,
        spouse_roth=0.0,
        your_roth_exposure=exposure,
    )


class TestLedger:
    def test_period_covers_conversion_year_plus_four(self) -> None:
        ledger = RothConversionLedger()
        ledger.record_conversion(2026, 100.0)
        assert ledger.exposure(2030) == RothExposure(free_first=0.0, exposed=100.0)
        assert ledger.exposure(2031) == RothExposure(free_first=100.0, exposed=0.0)

    def test_opening_balance_is_never_exposed(self) -> None:
        ledger = RothConversionLedger(50.0)
        ledger.record_conversion(2026, 100.0)
        assert ledger.exposure(2026) == RothExposure(free_first=50.0, exposed=100.0)

    def test_draws_consume_oldest_first(self) -> None:
        ledger = RothConversionLedger()
        ledger.record_conversion(2026, 5.0)
        ledger.record_conversion(2027, 10.0)
        ledger.record_draw(8.0)
        assert ledger.exposure(2031) == RothExposure(free_first=0.0, exposed=7.0)

    def test_zero_conversion_is_ignored(self) -> None:
        ledger = RothConversionLedger()
        ledger.record_conversion(2026, 0.0)
        assert ledger.exposure(2026) == RothExposure()

    def test_copy_is_independent(self) -> None:
        ledger = RothConversionLedger()
        ledger.record_conversion(2026, 10.0)
        clone = ledger.copy()
        clone.record_draw(10.0)
        assert ledger.exposure(2026).exposed == 10.0

    def test_exposed_part_splits_a_draw(self) -> None:
        exposure = RothExposure(free_first=30.0, exposed=50.0)
        assert exposure.exposed_part(20.0) == 0.0
        assert exposure.exposed_part(60.0) == 30.0
        assert exposure.exposed_part(500.0) == 50.0


class TestWaterfallRothPenalty:
    def test_exposed_draw_is_grossed_up_for_the_penalty(self) -> None:
        accounts = _roth_accounts(1_000.0, RothExposure(free_first=0.0, exposed=1_000.0))
        result = solve_waterfall(900.0, accounts, _no_tax, your_age=50, spouse_age=0)
        assert result.early_withdrawal_penalty == pytest.approx(0.10 * result.roth_draw)
        assert result.roth_draw - result.early_withdrawal_penalty == pytest.approx(900.0)
        assert result.unfunded == 0.0

    def test_penalty_free_after_59_and_a_half(self) -> None:
        accounts = _roth_accounts(1_000.0, RothExposure(free_first=0.0, exposed=1_000.0))
        result = solve_waterfall(900.0, accounts, _no_tax, your_age=60, spouse_age=0)
        assert result.early_withdrawal_penalty == 0.0
        assert result.roth_draw == pytest.approx(900.0)

    def test_expired_principal_is_drawn_before_exposed(self) -> None:
        accounts = _roth_accounts(1_000.0, RothExposure(free_first=600.0, exposed=400.0))
        result = solve_waterfall(500.0, accounts, _no_tax, your_age=50, spouse_age=0)
        assert result.early_withdrawal_penalty == 0.0

    def test_straddling_draw_penalises_only_the_exposed_part(self) -> None:
        accounts = _roth_accounts(1_000.0, RothExposure(free_first=600.0, exposed=400.0))
        result = solve_waterfall(700.0, accounts, _no_tax, your_age=50, spouse_age=0)
        exposed_drawn = result.roth_draw - 600.0
        assert result.early_withdrawal_penalty == pytest.approx(0.10 * exposed_drawn)
        assert result.roth_draw - result.early_withdrawal_penalty == pytest.approx(700.0)

    def test_untracked_exposure_means_no_penalty(self) -> None:
        accounts = _roth_accounts(1_000.0, RothExposure())
        result = solve_waterfall(500.0, accounts, _no_tax, your_age=50, spouse_age=0)
        assert result.early_withdrawal_penalty == 0.0
        assert result.roth_draw == pytest.approx(500.0)

    def test_penalty_that_exhausts_the_roth_is_unfunded(self) -> None:
        accounts = _roth_accounts(100.0, RothExposure(free_first=0.0, exposed=100.0))
        result = solve_waterfall(100.0, accounts, _no_tax, your_age=50, spouse_age=0)
        assert result.roth_draw == pytest.approx(100.0)
        assert result.unfunded == pytest.approx(10.0)
