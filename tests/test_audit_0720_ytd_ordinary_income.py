"""Regression test for audit Finding F1.

Finding F1: engine/scenario.py `run_scenario`'s `combined_gross` YTD block
enumerates 8 YTDSnapshot ordinary-income fields but omits `crypto_stcg_ytd`
and `crypto_income_ytd`. Both are ordinary-bracket income per
`YTDSnapshot.total_ordinary_income` and already flow into MAGI (via
`compute_magi` -> `magi_ytd`), but `combined_gross` (and therefore
`taxable_income` and `federal_tax_amt`) silently drops them.

Rule: crypto STCG YTD is ordinary-bracket income and must flow into
combined_gross / taxable_income / federal tax, consistent with its
inclusion in MAGI.
"""

import pytest

from engine.headroom import compute_headroom
from engine.scenario import ConversionPlan, run_scenario
from engine.sweet_spot_compute import base_income_for_year
from engine.tax import deductions, room_to_22, senior_bonus_deduction, taxable_ss
from models.household import Household
from models.ytd_income import YTDSnapshot


class TestF1CryptoStcgYtdInCombinedGross:
    """F1 — crypto_stcg_ytd must reach combined_gross, not just MAGI."""

    def test_f1_combined_gross_includes_crypto_stcg_ytd(self) -> None:
        # Given: a household with no other income sources, and a YTD snapshot
        # whose only income is $300K of crypto short-term capital gains.
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=60_000.0,
        )
        ytd = YTDSnapshot(tax_year=2026, crypto_stcg_ytd=300_000.0)
        plan = ConversionPlan()

        # When: the scenario is projected for the base year only.
        result = run_scenario(hh, plan, "f1_crypto_stcg", end_age=61, ytd=ytd)
        yr = result.years[0]

        # Then:
        # MAGI already routes crypto_stcg_ytd correctly (guard — was already correct).
        assert yr.magi == pytest.approx(300_000.0)

        # combined_gross must include the same $300K of ordinary-bracket income
        # (the bug: pre-fix code silently drops crypto_stcg_ytd here).
        assert yr.combined_gross == pytest.approx(300_000.0)

        # With $300K of ordinary income and no offsetting deductions beyond the
        # standard deduction, federal tax must be strictly positive.
        assert yr.federal_tax_amt > 0


class TestF2CryptoYtdInSocialSecurityProvisionalIncome:
    """F2 — crypto YTD income must reach the SS provisional-income base.

    Finding F2: `engine/scenario_compute.py::compute_social_security`'s
    YTD ordinary-income block (used to build IRC §86(b)(2) provisional
    income) enumerates wages/NEC/STCG/dividends/conversions/distributions/
    interest but omits `crypto_stcg_ytd`, `crypto_income_ytd`, and
    `crypto_ltcg_ytd`. All three are AGI items (crypto STCG/income are
    ordinary; crypto LTCG is a capital gain, same AGI-inclusion treatment
    as `ltcg_ytd`/`qualified_dividends_ytd` already in the block) and must
    count toward provisional income, understating taxable Social Security
    when omitted.

    Rule: provisional income = AGI items (incl. crypto STCG/income/LTCG) +
    tax-exempt interest + 1/2 combined SS (IRC §86(b)(2)).
    """

    def test_f2_taxable_ss_reflects_crypto_ytd_provisional_income(self) -> None:
        # Given: a household where SS has already started (age 70, default
        # your_ss_start_age=70), no other income sources, and a YTD snapshot
        # with $100K of crypto STCG — deliberately chosen (with the default
        # SS benefit) to land provisional income in the 85%-taxable tier.
        hh = Household(
            your_age=70,
            spouse_age=65,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=60_000.0,
        )
        ytd = YTDSnapshot(tax_year=2026, crypto_stcg_ytd=100_000.0)
        plan = ConversionPlan()

        # When: the scenario is projected for the base year only.
        result = run_scenario(hh, plan, "f2_crypto_ss", end_age=70, ytd=ytd)
        yr = result.years[0]

        # Then: the only non-SS AGI item is the $100K crypto STCG, so the
        # correct provisional income is exactly crypto_stcg_ytd + 1/2 combined_ss,
        # and taxable_ss_amt must match the formula applied to that base.
        expected_taxable_ss = taxable_ss(
            yr.combined_ss, 100_000.0, filing_status=hh.filing_status
        )
        assert expected_taxable_ss > 0, "test setup must land in a taxable SS tier"
        assert yr.taxable_ss_amt == pytest.approx(expected_taxable_ss)


class TestF3CryptoLtcgYtdInLtcgStackWalk:
    """F3 — crypto_ltcg_ytd must reach the LTCG preferential-rate stack-walk.

    Finding F3: `engine/scenario.py`'s LTCG stack-walk guard/base
    (`_ytd_ltcg_total`, ~line 578) sums only `ltcg_ytd` +
    `qualified_dividends_ytd`, omitting `crypto_ltcg_ytd`. Crypto LTCG
    already flows into MAGI (`magi_ytd`) and NIIT (`total_investment_income`)
    but never reaches the 0%/15%/20% LTCG stack-walk, so a household whose
    only realized gain is crypto LTCG pays $0 LTCG tax while NIIT correctly
    fires on the same dollars.

    Rule: crypto LTCG is taxed at the same preferential 0/15/20% rates as
    ordinary LTCG (IRC §1(h)) and must be included in the stack-walk base.
    """

    def test_f3_ytd_ltcg_tax_positive_when_only_gain_is_crypto_ltcg(self) -> None:
        # Given: a household with zero ordinary income and a YTD snapshot
        # whose only investment activity is $200K of crypto long-term
        # capital gains — large enough to cross the 0%->15% LTCG threshold
        # even starting from $0 ordinary taxable income.
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=60_000.0,
        )
        ytd = YTDSnapshot(tax_year=2026, crypto_ltcg_ytd=200_000.0)
        plan = ConversionPlan()

        # When: the scenario is projected for the base year only.
        result = run_scenario(hh, plan, "f3_crypto_ltcg", end_age=61, ytd=ytd)
        yr = result.years[0]

        # Then:
        # MAGI already routes crypto_ltcg_ytd correctly (guard — was already correct).
        assert yr.magi == pytest.approx(200_000.0)

        # The bug: the stack-walk guard/base omits crypto_ltcg_ytd entirely,
        # so ytd_ltcg_tax (and its fold-in to federal_tax_amt) is $0.
        assert yr.ytd_ltcg_tax > 0


class TestF4YtdCashInflowsInAvailableIncome:
    """F4 — wages/NEC/interest/STCG YTD cash must reach available_income.

    Finding F4: `engine/scenario.py`'s `available_income` (~line 644, feeds
    `income_needed`/`excess_rmd`) sums RMDs, extra withdrawals, SS, option
    income, and inherited distributions, but never adds back the YTD cash
    already received this year (wages, NEC self-employment income, interest,
    realized STCG). Those dollars are already taxed (counted in
    combined_gross -> federal_tax_amt, which IS subtracted), but the
    corresponding cash inflow is never added back — producing a phantom
    income_needed shortfall for households who already received substantial
    YTD cash income.

    Rule: available_income = actual spendable cash received this year minus
    tax paid on it. YTD wages/NEC/interest/STCG are spendable cash and must
    be added, mirroring how they are already taxed via combined_gross.
    """

    def test_f4_available_income_includes_ytd_wages_reducing_phantom_shortfall(self) -> None:
        # Given: a household with no SS, no RMDs, no conversions — the only
        # income this year is $80K of YTD wages — and living expenses well
        # below what $80K of after-tax wages can cover.
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=30_000.0,
        )
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=80_000.0)
        plan = ConversionPlan()

        # When: the scenario is projected for the base year only.
        result = run_scenario(hh, plan, "f4_ytd_wages", end_age=61, ytd=ytd)
        yr = result.years[0]

        # Then: with no SS/RMDs/conversions/inherited distributions, the only
        # correct available_income is the YTD wage cash net of the tax it
        # already generated (which combined_gross/federal_tax_amt correctly
        # includes, per F1).
        expected_available_income = 80_000.0 - yr.federal_tax_amt
        expected_income_needed = max(hh.living_expenses - expected_available_income, 0.0)
        expected_excess_rmd = max(expected_available_income - hh.living_expenses, 0.0)

        # The bug: available_income never adds back wages_ytd, so income_needed
        # is phantom-overstated (or excess_rmd phantom-understated).
        assert yr.income_needed == pytest.approx(expected_income_needed)
        assert yr.excess_rmd == pytest.approx(expected_excess_rmd)
        # Sanity: with $80K wages against $30K expenses, there should be no
        # shortfall at all once the cash is correctly counted.
        assert expected_income_needed == 0.0


class TestF6SweetSpotYtdOrdinaryAlreadyMatchesScenarioForCrypto:
    """F6 — SKIPPED: does not reproduce at current HEAD.

    Finding F6 hypothesized that `engine/sweet_spot_compute.py::base_income_for_year`'s
    `ytd_ordinary` (~line 292, built from `ytd.total_ordinary_income -
    ytd.nqo_exercise_ytd`) diverges from scenario.py's `combined_gross` for
    crypto-YTD households. Investigation at current HEAD: `total_ordinary_income`
    (models/ytd_income.py) ALREADY includes `crypto_stcg_ytd` and
    `crypto_income_ytd` — that inclusion predates this branch's F1 fix, which
    only touched `engine/scenario.py`. Since F1 made `combined_gross` include
    the same two crypto fields, sweet_spot and scenario already agree for a
    crypto-only household; adding a red test with ONLY crypto YTD income
    (no above-the-line adjustments) confirmed base.ytd_ordinary ==
    combined_gross with zero delta.

    The ~$32K gap the audit actually measured came from a household that
    ALSO carried above-the-line HSA/deductible-IRA YTD contributions:
    `total_ordinary_income` nets those out, which is the TAX-CORRECT
    treatment (an above-the-line deduction reduces AGI and therefore the
    ordinary bracket base) — confirmed independently by the pre-existing,
    still-passing
    `tests/test_headroom.py::TestHeadroom::test_above_the_line_adjustments_widen_all_headroom_by_exact_amount`,
    which pins the identical `total_ordinary_income`-based pattern in
    headroom.py as correct. It is `engine/scenario.py`'s `combined_gross`
    that never subtracts above-the-line adjustments at all — a separate,
    pre-existing gap outside F6's scope (which targets
    sweet_spot_compute.py) and NOT remediated here.

    Guard (no production change): for a crypto-only YTD snapshot (no
    above-the-line adjustments), sweet_spot's ytd_ordinary must equal
    scenario's combined_gross.
    """

    def test_f6_ytd_ordinary_matches_combined_gross_for_crypto_only_ytd(self) -> None:
        # Given: a household with $100K of crypto STCG YTD and no
        # above-the-line adjustments.
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=60_000.0,
        )
        ytd = YTDSnapshot(tax_year=2026, crypto_stcg_ytd=100_000.0)
        plan = ConversionPlan()

        # When: both engines compute their YTD ordinary-income base for the
        # same base year.
        base = base_income_for_year(hh, hh.base_year, ytd)
        result = run_scenario(hh, plan, "f6_guard", end_age=61, ytd=ytd)
        combined_gross = result.years[0].combined_gross

        # Then: they already agree — no fix needed (F6 does not reproduce).
        assert base.ytd_ordinary == pytest.approx(combined_gross)
        assert base.ytd_ordinary == pytest.approx(100_000.0)


class TestF7HeadroomLockedGrossAlreadyMatchesScenarioForCrypto:
    """F7 — SKIPPED: does not reproduce at current HEAD.

    Same root-cause analysis as F6 (see above), applied to
    `engine/headroom.py::compute_headroom`'s `locked_gross`/`planned_gross`
    (~line 171/198): both are built from `ytd.total_ordinary_income`, which
    already included `crypto_stcg_ytd`/`crypto_income_ytd` before this
    branch's F1 fix, so headroom already agreed with scenario's
    `combined_gross` for crypto-only households — confirmed by the
    pre-existing, still-passing
    `tests/test_headroom.py::TestHeadroom::test_crypto_stcg_reduces_all_four_rooms_by_exact_amount`.
    The audit's earlier note that headroom.py uses `ytd.total_ordinary_income`
    "correctly" is independently confirmed by
    `test_above_the_line_adjustments_widen_all_headroom_by_exact_amount`,
    which pins that HSA/IRA above-the-line adjustments must widen
    `room_to_12pct`/`room_to_22pct` by the exact adjustment amount — i.e.
    `total_ordinary_income`'s above-the-line subtraction is the CORRECT
    behavior, not a bug. (An earlier attempt to "fix" F7 by removing that
    subtraction to match scenario.py's combined_gross was reverted after it
    broke this exact golden test.) The residual gap the audit measured is
    scenario.py's `combined_gross` never subtracting above-the-line
    adjustments — out of scope for F7 (which targets headroom.py) and NOT
    remediated here.

    Guard (no production change): for a crypto-only YTD snapshot (no
    above-the-line adjustments), headroom's bracket-room computation must
    reflect the same ordinary gross as scenario's combined_gross.
    """

    def test_f7_room_to_22pct_reflects_combined_gross_ordinary_base_for_crypto_only_ytd(
        self,
    ) -> None:
        # Given: a household below SS-start age (no SS this year, isolating
        # the ordinary-gross effect) with $100K of crypto STCG YTD and no
        # above-the-line adjustments.
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=60_000.0,
        )
        ytd = YTDSnapshot(tax_year=2026, crypto_stcg_ytd=100_000.0)

        # When: headroom is computed for the base year.
        result = compute_headroom(hh, ytd, filing_status=hh.filing_status)

        # Then: room_to_22pct already reflects the same $100K ordinary gross
        # scenario's combined_gross would produce (no SS, no above-the-line,
        # standard deduction only at these under-65 ages) — no fix needed
        # (F7 does not reproduce).
        ded = deductions(
            hh.your_age,
            hh.spouse_age,
            hh.std_deduction,
            hh.senior_extra,
            filing_status=hh.filing_status,
            year=hh.base_year,
            cpi=hh.cpi_assumption,
        )
        ded += senior_bonus_deduction(
            hh.your_age,
            hh.spouse_age,
            ytd.niit_magi_ytd,
            year=hh.base_year,
            cpi=hh.cpi_assumption,
            filing_status=hh.filing_status,
        )
        expected_room_to_22pct = room_to_22(
            100_000.0,
            ded,
            year=hh.base_year,
            cpi=hh.cpi_assumption,
            filing_status=hh.filing_status,
        )
        assert result.room_to_22pct == pytest.approx(expected_room_to_22pct)
