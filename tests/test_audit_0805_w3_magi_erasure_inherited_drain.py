"""Regression tests for audit-0805 W3 findings C12, C22, N1, C21.

C12 (engine/scenario.py): the base-year MAGI option-income contribution
(option_income - nqo_exercise_ytd) had no floor, so when realized YTD NQO
exercises exceed the SCHEDULED option income for the year, the excess
"disappears" instead of flowing into MAGI via the netted term.

C22 (engine/sweet_spot_compute.py): the raw scheduled `opt` value feeds
MAGI/gross/NIIT-MAGI/SS-provisional-income directly and is never bounded by
the realized YTD amount -- unlike scenario.py, which at least attempts (buggily)
to net it via subtraction.

N1: the same root cause (unbounded scheduled option income) also under-counts
combined_gross and SS provisional income in BOTH files when realized exceeds
scheduled.

C21 (engine/sweet_spot_compute.py): estimate_rmd_income passes the STATIC
original inherited-IRA balance to inherited_ira_drain instead of a shrinking,
growth-compounded running balance -- so the final window year drains the
entire original balance instead of the true (much smaller) balance-of-record.
"""

from __future__ import annotations

import pytest

from engine.ira import inherited_ira_drain, inherited_ira_drain_for_year
from engine.scenario import ConversionPlan, run_scenario
from engine.sweet_spot_compute import (
    all_in_at_conversion,
    base_income_for_year,
    estimate_rmd_income,
)
from engine.tax import taxable_ss
from models.grants import StockGrant
from models.household import Household, InheritedIRA
from models.ytd_income import YTDSnapshot


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


def _oracle_year(hh: Household, year: int, ytd: YTDSnapshot | None = None):
    """Run engine.scenario for `hh` through `year` and return that year's YearResult."""
    end_age = hh.your_age + (year - hh.base_year)
    result = run_scenario(hh, ConversionPlan(), "oracle", end_age=end_age, ytd=ytd)
    for yr in result.years:
        if yr.year == year:
            return yr
    raise AssertionError(f"year {year} not found in scenario result")  # pragma: no cover


class TestC12ScenarioMagiFloor:
    """engine/scenario.py:344 -- option_income_for_magi must floor at 0.0
    when realized YTD NQO exercises exceed the scheduled option income."""

    def _hh(self) -> Household:
        return Household(
            base_year=2026,
            your_age=61,
            spouse_age=55,
            your_ira=0.0,
            spouse_ira=0.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026)],
            txn_price_now=200.0,
        )

    def test_realized_exceeds_scheduled_magi_uses_full_realized(self) -> None:
        # scheduled opt = (200-104)*2000 = 192_000; realized = 300_000 (> scheduled).
        hh = self._hh()
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=300_000.0)
        yr = _oracle_year(hh, 2026, ytd=ytd)

        # Buggy (no floor): magi collapses to opt (192_000) -- the extra 108_000
        # of ACTUALLY realized income vanishes. Correct: magi == ytd.magi_ytd
        # (300_000) since realized fully supersedes the unrealized schedule.
        assert yr.magi == approx(300_000.0)

    def test_realized_below_scheduled_unchanged(self) -> None:
        """Invariant: realized <= scheduled must be numerically unchanged by the floor."""
        hh = self._hh()
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=50_000.0)
        yr = _oracle_year(hh, 2026, ytd=ytd)
        # opt=192_000, realized=50_000 -> option_income_for_magi = 142_000 (unaffected by floor)
        # magi = 142_000 + ytd.magi_ytd(50_000) = 192_000 (equals scheduled opt, as expected
        # when realized < scheduled and no other income).
        assert yr.magi == approx(192_000.0)


class TestN1ScenarioGrossAndSsProvisionalUndercounts:
    """engine/scenario.py -- combined_gross and SS taxable amount must also use
    the realized-vs-scheduled bound, not the raw (unbounded) scheduled option income."""

    def _hh(self) -> Household:
        return Household(
            base_year=2026,
            your_age=67,
            spouse_age=55,
            your_ira=0.0,
            spouse_ira=0.0,
            your_ss_fra=20_000 / 12,
            your_ss_start_age=67,
            your_fra_age=67,
            spouse_ss_fra=0.0,
            grants=[StockGrant(year=2019, strike=104, shares=100, expiry_year=2026)],
            txn_price_now=204.0,
        )

    def test_gross_and_taxable_ss_use_bounded_option_income(self) -> None:
        # scheduled opt = (204-104)*100 = 10_000; realized = 40_000 (> scheduled).
        # combined_ss = 20_000/yr exactly (claimed at FRA, no reduction/credit).
        hh = self._hh()
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=40_000.0)
        yr = _oracle_year(hh, 2026, ytd=ytd)

        # Hand-computed correct taxable SS: provisional = max(opt,realized) + 0.5*ss
        #   = 40_000 + 10_000 = 50_000 > tier2 (44_000, MFJ)
        #   tier1_contribution = min(0.5*20_000, 0.5*(44_000-32_000)) = 6_000
        #   taxable = 0.85*(50_000-44_000) + 6_000 = 11_100 (< cap of 0.85*20_000=17_000)
        expected_tss = taxable_ss(20_000.0, 40_000.0, filing_status="MFJ")
        assert expected_tss == approx(11_100.0)
        assert yr.taxable_ss_amt == approx(11_100.0), (
            f"Pre-fix bug used raw scheduled opt (10_000) as SS provisional income, "
            f"giving taxable_ss_amt=0.0 (provisional 20_000 < tier1 32_000). "
            f"Got {yr.taxable_ss_amt}."
        )

        # combined_gross must reflect the same bounded option income + the (now
        # correctly nonzero) taxable SS -- pre-fix this was 10_000 (raw opt only,
        # taxable_ss also 0 due to the SS undercount above).
        assert yr.combined_gross == approx(51_100.0)

    def test_realized_below_scheduled_ss_and_gross_unchanged(self) -> None:
        hh = self._hh()
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=1_000.0)  # < scheduled 10_000
        yr = _oracle_year(hh, 2026, ytd=ytd)
        # provisional = 10_000 + 10_000 = 20_000 < tier1 (32_000) -> tss = 0
        assert yr.taxable_ss_amt == approx(0.0)
        assert yr.combined_gross == approx(10_000.0)


class TestC22SweetSpotOptBoundParity:
    """engine/sweet_spot_compute.py -- base_income_for_year must bound the raw
    `opt` value itself (mirroring headroom.py), not just the already-netted
    ytd_magi/ytd_niit_magi/ytd_ordinary side. Parity oracle: engine.scenario
    (post-C12/N1-fix)."""

    def _hh(self) -> Household:
        return Household(
            base_year=2026,
            your_age=67,
            spouse_age=55,
            your_ira=0.0,
            spouse_ira=0.0,
            your_ss_fra=20_000 / 12,
            your_ss_start_age=67,
            your_fra_age=67,
            spouse_ss_fra=0.0,
            grants=[StockGrant(year=2019, strike=104, shares=100, expiry_year=2026)],
            txn_price_now=204.0,
        )

    def test_sweet_spot_matches_scenario_oracle_when_realized_exceeds_scheduled(self) -> None:
        hh = self._hh()
        year = hh.base_year
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=40_000.0)
        oracle = _oracle_year(hh, year, ytd=ytd)

        base = base_income_for_year(hh, year, ytd=ytd)
        result = all_in_at_conversion(hh, base, 0.0, 0.0)

        # Pre-fix: sweet_spot's raw `opt` (10_000) is never bounded, giving
        # base_gross=10_000 / result.magi=10_000 -- matching scenario.py's OWN
        # pre-fix bug (both wrong in the same cancelling way), not the correct
        # 51_100 the fixed oracle now reports.
        assert base.base_gross == approx(oracle.combined_gross)
        assert result.magi == approx(oracle.magi)
        assert base.base_gross == approx(51_100.0)
        assert result.magi == approx(51_100.0)


class TestC21InheritedIraDrainParity:
    """engine/sweet_spot_compute.py:297 -- estimate_rmd_income must replay the
    shrinking, growth-compounded running balance (like scenario.py's stateful
    loop), not drain the STATIC original balance every year."""

    BASE_YEAR = 2026

    def _hh(self, iira: InheritedIRA) -> Household:
        return Household(
            base_year=self.BASE_YEAR,
            your_age=61,
            spouse_age=55,
            your_ira=0.0,
            spouse_ira=0.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
            inherited_iras=[iira],
        )

    def test_zero_growth_final_year_matches_simple_even_split(self) -> None:
        """No-growth sanity check matching the audit's illustrative arithmetic:
        B=$500K over N=10 years -> true final-year drain is B/N=$50K, not B."""
        iira = InheritedIRA(
            balance=500_000.0, inherited_year=self.BASE_YEAR + 1, owner="you", growth_rate=0.0
        )
        hh = self._hh(iira)
        final_year = self.BASE_YEAR + 10  # years_in=9, years_remaining=1 (balloon year)

        # Pre-fix bug: inherited_ira_drain(iira.balance, 1) == 500_000.0 (the
        # ENTIRE original balance, ignoring 9 years of prior drains).
        buggy = inherited_ira_drain(iira.balance, 1)
        assert buggy == approx(500_000.0)

        correct = inherited_ira_drain_for_year(
            iira.balance, iira.inherited_year, final_year, iira.growth_rate
        )
        assert correct == approx(50_000.0)

        sweet_spot_drain = estimate_rmd_income(hh, final_year)
        assert sweet_spot_drain == approx(50_000.0), (
            f"Pre-fix sweet_spot drained the static original balance ({buggy}) "
            f"instead of the true balance-of-record ({correct})."
        )

    def test_nonzero_growth_parity_across_full_window(self) -> None:
        """STRONGEST test: sweet_spot's per-year drain must equal scenario.py's
        stateful running-balance loop across the full 10-year window, with a
        NON-ZERO growth_rate."""
        iira = InheritedIRA(
            balance=500_000.0, inherited_year=self.BASE_YEAR + 1, owner="you", growth_rate=0.07
        )
        hh = self._hh(iira)
        result = run_scenario(hh, ConversionPlan(), end_age=hh.your_age + 12)
        oracle_by_year = {yr.year: yr.your_inherited_distribution for yr in result.years}

        for year in range(self.BASE_YEAR + 1, self.BASE_YEAR + 11):
            sweet_spot_drain = estimate_rmd_income(hh, year)
            assert sweet_spot_drain == approx(oracle_by_year[year], tol=1.0), (
                f"year {year}: sweet_spot={sweet_spot_drain} oracle={oracle_by_year[year]}"
            )

        # Final window year (years_in=9, years_remaining=1): the buggy code
        # would report the static original balance (500_000.0); the correct
        # (grown, drained) balance-of-record is far smaller (~$91,928).
        final_year = self.BASE_YEAR + 10
        assert oracle_by_year[final_year] == approx(91_927.64, tol=5.0)
        assert oracle_by_year[final_year] < 500_000.0 / 2
