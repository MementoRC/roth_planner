"""Tests for the pure IRA-withdrawal-waterfall fixed-point solver.

NAMING CONSTRAINT -- test function names in this file must NOT have exactly 35
characters after the `test_` prefix. TruffleHog's Lob API-key detector matches
`(live|test)_` followed by 35 characters and its verifier stamps any
test_-prefixed candidate as Verified=true, so such a name is reported as a
verified leaked secret. CI runs with fail-on-secrets: true, which makes that a
HARD BLOCK on a pure false positive. Two names here were renamed for exactly
this reason (see git history); renaming them back to a 35-character suffix will
silently break CI.
"""

import pytest

from engine.withdrawal_waterfall import Accounts, solve_waterfall


def flat_tax(rate: float):
    return lambda extra: rate * extra


def no_tax(extra: float) -> float:
    return 0.0


def cliff_tax(extra: float) -> float:
    """Discontinuous 'ACA cliff' style tax used to force perpetual oscillation."""
    return 10_000.0 if extra <= 15_000 else 2_000.0


def make_accounts(**overrides: float) -> Accounts:
    defaults: dict[str, float] = {
        "brokerage": 0.0,
        "brokerage_basis_fraction": 0.5,
        "your_ira": 0.0,
        "spouse_ira": 0.0,
        "your_roth": 0.0,
        "spouse_roth": 0.0,
    }
    defaults.update(overrides)
    return Accounts(**defaults)


class TestNoShortfall:
    def test_zero_need_returns_all_zero(self):
        accounts = make_accounts(brokerage=100_000, your_ira=100_000)
        result = solve_waterfall(0, accounts, no_tax, your_age=61, spouse_age=55)
        assert result.brokerage_draw == 0
        assert result.your_ira_draw == 0
        assert result.spouse_ira_draw == 0
        assert result.roth_draw == 0
        assert result.unfunded == 0
        assert result.iterations == 0
        assert result.converged is True

    def test_negative_need_returns_all_zero(self):
        accounts = make_accounts(brokerage=100_000)
        result = solve_waterfall(-500, accounts, no_tax, your_age=61, spouse_age=55)
        assert result.brokerage_draw == 0
        assert result.unfunded == 0
        assert result.converged is True
        assert result.iterations == 0


class TestBrokerageOnly:
    def test_brokerage_covers_need_no_ira_touched(self):
        accounts = make_accounts(
            brokerage=100_000, brokerage_basis_fraction=0.4, your_ira=50_000
        )
        result = solve_waterfall(20_000, accounts, no_tax, your_age=61, spouse_age=55)
        assert result.brokerage_draw == pytest.approx(20_000)
        assert result.realized_gain == pytest.approx(20_000 * 0.6)
        assert result.your_ira_draw == 0
        assert result.spouse_ira_draw == 0
        assert result.roth_draw == 0
        assert result.unfunded == pytest.approx(0)
        assert result.converged is True

    def test_basis_fraction_full_no_gain(self):
        accounts = make_accounts(brokerage=50_000, brokerage_basis_fraction=1.0)
        result = solve_waterfall(10_000, accounts, no_tax, your_age=61, spouse_age=55)
        assert result.realized_gain == pytest.approx(0)

    def test_basis_fraction_zero_full_gain(self):
        accounts = make_accounts(brokerage=50_000, brokerage_basis_fraction=0.0)
        result = solve_waterfall(10_000, accounts, no_tax, your_age=61, spouse_age=55)
        assert result.realized_gain == pytest.approx(10_000)


class TestGrossUp:
    def test_flat_25pct_tax_grosses_up_ira_draw(self):
        accounts = make_accounts(your_ira=200_000)
        result = solve_waterfall(
            60_000, accounts, flat_tax(0.25), your_age=61, spouse_age=61
        )
        # Hand check: 80000 - 0.25*80000 == 60000
        assert result.your_ira_draw == pytest.approx(80_000, abs=1.0)
        assert result.converged is True
        assert result.spouse_ira_draw == 0
        assert result.unfunded == pytest.approx(0, abs=1.0)


class TestPenalty:
    def test_penalty_when_only_exposed_ira_available(self):
        accounts = make_accounts(your_ira=0, spouse_ira=200_000)
        result = solve_waterfall(
            60_000, accounts, flat_tax(0.25), your_age=61, spouse_age=55
        )
        assert result.your_ira_draw == 0
        assert result.spouse_ira_draw == pytest.approx(92_307.69, abs=1.0)
        assert result.early_withdrawal_penalty == pytest.approx(
            0.10 * result.spouse_ira_draw, abs=1.0
        )
        # the draw must gross up for both tax AND its own penalty
        assert result.spouse_ira_draw == pytest.approx(
            60_000
            + flat_tax(0.25)(result.spouse_ira_draw)
            + result.early_withdrawal_penalty,
            abs=1.0,
        )

    def test_no_penalty_when_only_penalty_free_ira_drawn(self):
        accounts = make_accounts(your_ira=200_000, spouse_ira=200_000)
        result = solve_waterfall(
            10_000, accounts, flat_tax(0.25), your_age=61, spouse_age=55
        )
        assert result.spouse_ira_draw == 0
        assert result.early_withdrawal_penalty == 0


class TestOrdering:
    def test_your_ira_drained_before_spouse(self):
        accounts = make_accounts(your_ira=30_000, spouse_ira=200_000)
        result = solve_waterfall(50_000, accounts, no_tax, your_age=61, spouse_age=55)
        assert result.your_ira_draw == pytest.approx(30_000, abs=1.0)
        assert result.spouse_ira_draw > 0

    def test_roth_drawn_only_after_both_iras_exhausted(self):
        accounts = make_accounts(
            your_ira=10_000, spouse_ira=10_000, your_roth=50_000, spouse_roth=50_000
        )
        result = solve_waterfall(15_000, accounts, no_tax, your_age=61, spouse_age=61)
        assert result.your_ira_draw == pytest.approx(10_000, abs=1.0)
        assert result.spouse_ira_draw == pytest.approx(5_000, abs=1.0)
        assert result.roth_draw == pytest.approx(0, abs=1.0)

        accounts2 = make_accounts(
            your_ira=10_000, spouse_ira=10_000, your_roth=50_000, spouse_roth=50_000
        )
        result2 = solve_waterfall(30_000, accounts2, no_tax, your_age=61, spouse_age=61)
        assert result2.your_ira_draw == pytest.approx(10_000, abs=1.0)
        assert result2.spouse_ira_draw == pytest.approx(10_000, abs=1.0)
        assert result2.roth_draw == pytest.approx(10_000, abs=1.0)


class TestExhaustion:
    def test_total_exhaustion_unfunded_equals_residual(self):
        accounts = make_accounts(
            brokerage=5_000,
            brokerage_basis_fraction=1.0,
            your_ira=5_000,
            spouse_ira=5_000,
            your_roth=5_000,
            spouse_roth=5_000,
        )
        result = solve_waterfall(50_000, accounts, no_tax, your_age=61, spouse_age=61)
        assert result.brokerage_draw == pytest.approx(5_000)
        assert result.your_ira_draw == pytest.approx(5_000)
        assert result.spouse_ira_draw == pytest.approx(5_000)
        assert result.roth_draw == pytest.approx(10_000)
        total_funded = (
            result.brokerage_draw
            + result.your_ira_draw
            + result.spouse_ira_draw
            + result.roth_draw
        )
        assert result.unfunded == pytest.approx(50_000 - total_funded, abs=1.0)
        assert result.unfunded > 0


class TestNonConvergence:
    def test_discontinuous_tax_returns_conservative_larger_draw(self):
        accounts = make_accounts(your_ira=100_000)
        result = solve_waterfall(
            10_000,
            accounts,
            cliff_tax,
            your_age=61,
            spouse_age=61,
            max_iterations=50,
            tolerance=1.0,
        )
        assert result.converged is False
        assert result.iterations == 50
        assert result.your_ira_draw == pytest.approx(20_000, abs=1.0)
        assert result.spouse_ira_draw == 0
        assert result.unfunded == pytest.approx(0, abs=1.0)


class TestClamping:
    def test_no_field_negative_and_draws_never_exceed_balance(self):
        accounts = make_accounts(
            brokerage=1_000,
            brokerage_basis_fraction=0.3,
            your_ira=2_000,
            spouse_ira=1_000,
            your_roth=500,
            spouse_roth=500,
        )
        result = solve_waterfall(
            100_000, accounts, flat_tax(0.9), your_age=55, spouse_age=55
        )
        for field in (
            result.brokerage_draw,
            result.realized_gain,
            result.your_ira_draw,
            result.spouse_ira_draw,
            result.early_withdrawal_penalty,
            result.roth_draw,
            result.unfunded,
        ):
            assert field >= 0
        assert result.brokerage_draw <= accounts.brokerage
        assert result.your_ira_draw <= accounts.your_ira
        assert result.spouse_ira_draw <= accounts.spouse_ira
        assert result.roth_draw <= accounts.your_roth + accounts.spouse_roth
