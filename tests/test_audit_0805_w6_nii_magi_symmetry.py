"""TDD regression tests for audit-0805 W6 finding C10.

C10 -- manual additional NII inflates the NII base but not MAGI
-----------------------------------------------------------------
``niit(magi, nii)`` charges 3.8% on ``min(nii, magi - threshold)``. The
manual ``net_inv_income`` parameter (the "Additional NII $/yr" widget --
user-entered off-portfolio interest/gains not otherwise modeled) is added
to the NII argument at both call sites below but was NOT added to the
MAGI argument, so NIIT is measured against a MAGI that omits income the
user just declared. That understates the excess-over-threshold and
therefore understates the tax whenever the omission changes which side
of the threshold MAGI falls on.

Two confirmed sites:
- engine/scenario.py (run_scenario): ``net_investment_income += net_inv_income``
  but ``yr.niit_magi`` derives from ``yr.magi``, which never included it.
- engine/sweet_spot_compute.py (all_in_at_conversion): ``total_net_inv_income
  = net_inv_income + base.net_investment_income_addl`` but ``niit_magi`` /
  ``niit_base_magi`` are built without it.

Both fixtures are constructed with zero IRA balances, zero SS, no grants,
and no brokerage/YTD income so every MAGI component collapses to just the
swept conversion amount -- isolating the missing ``net_inv_income`` term.
"""

from __future__ import annotations

import pytest

from engine.niit import NIIT_THRESHOLD_MFJ, niit
from engine.scenario import ConversionPlan, run_scenario
from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year
from models.household import Household


def approx(expected: float, tol: float = 0.01) -> object:
    return pytest.approx(expected, abs=tol)


def _bare_mfj_household(**overrides: object) -> Household:
    """MFJ household with every non-conversion income source zeroed out:
    no RMDs (well below RMD start age), no grants (no option income), no SS,
    no brokerage. MAGI for a given year then collapses to exactly the
    conversion amount. your_ira is sized comfortably above any conversion
    used in these tests -- scenario-core-5 (engine/scenario.py:259-262)
    clamps yr.your_conversion to the available IRA balance, so an
    under-funded IRA would silently zero the planned conversion."""
    base: dict[str, object] = {
        "grants": [],
        "your_age": 61,
        "spouse_age": 61,
        "your_ira": 1_000_000.0,
        "spouse_ira": 0.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "filing_status": "MFJ",
        "brokerage_start": 0.0,
        "base_year": 2026,
    }
    base.update(overrides)
    return Household(**base)  # type: ignore[arg-type]


class TestScenarioNiitMagiOmitsManualNII:
    """engine/scenario.py: yr.niit_magi must include the manual net_inv_income
    passed to run_scenario -- it is real declared income, not just NII."""

    def test_niit_crosses_threshold_only_when_manual_nii_added_to_magi(self) -> None:
        """MFJ, conversion=$240K (=MAGI, since every other source is zeroed),
        manual net_inv_income=$20K.

        Hand-derivation:
          Correct niit_magi = 240_000 (conversion/MAGI) + 20_000 (manual NII) = 260_000.
          excess = 260_000 - 250_000 = 10_000.
          net_investment_income = 0 (no realized gains/divs) + 20_000 = 20_000.
          taxable_nii = min(20_000, 10_000) = 10_000.
          correct niit_cost = 10_000 * 0.038 = 380.00.

        Defective code passes niit_magi=240_000 (excludes the manual NII) --
        below the $250K MFJ threshold -- so niit() short-circuits to 0.0
        regardless of net_investment_income.
        """
        # Fund the conversion's own federal-tax (+NIIT) cost from an inert,
        # basis-matched brokerage reserve (brok_turnover=0.0 so no gain is
        # ever realized) instead of forcing an IRA draw -- otherwise the
        # IRA-withdrawal waterfall's fixed point (draw -> tax -> larger draw)
        # inflates magi beyond the conversion amount, confounding this test's
        # isolated assertion.
        hh = _bare_mfj_household(
            living_expenses=0.0, brokerage_start=500_000.0, brok_turnover=0.0
        )
        plan = ConversionPlan(your_conversions={2026: 240_000.0})
        result = run_scenario(hh, plan, "c10-scenario", end_age=61, net_inv_income=20_000.0)
        yr = result.years[0]

        # Guard the fixture: MAGI before folding in manual NII must sit
        # exactly at the conversion amount and below the MFJ threshold.
        assert yr.magi == approx(240_000.0)
        assert yr.magi < NIIT_THRESHOLD_MFJ

        assert yr.niit_magi == approx(260_000.0), (
            f"Expected niit_magi=260000.00 (yr.magi=240000 + manual "
            f"net_inv_income=20000), got {yr.niit_magi:.2f} -- the manual "
            f"NII is being added to the NII term but not to the MAGI term "
            f"(engine/scenario.py)"
        )
        assert yr.niit_cost == approx(380.00), (
            f"Expected niit_cost=380.00 (3.8% of min(20000 NII, 10000 excess "
            f"over the $250K MFJ threshold when manual NII is folded into "
            f"MAGI)), got {yr.niit_cost:.2f}"
        )


class TestSweetSpotNiitMagiOmitsManualNII:
    """engine/sweet_spot_compute.py: all_in_at_conversion's niit_magi /
    niit_base_magi must include the manual net_inv_income -- mirroring the
    scenario.py site above."""

    def test_niit_delta_crosses_threshold_only_when_manual_nii_added_to_magi(self) -> None:
        """MFJ, conv=$240K (with-conversion MAGI), base (no-conversion) MAGI=$0,
        manual net_inv_income=$20K.

        Hand-derivation:
          Correct niit_magi (with) = 240_000 + 20_000 = 260_000 -> excess 10_000.
          Correct niit_base_magi (without) = 0 + 20_000 = 20_000 -> below threshold.
          total_net_inv_income = 20_000 (manual) + 0 (no auto-detected NII) = 20_000.
          niit_with = min(20_000, 10_000) * 0.038 = 380.00.
          niit_without = 0.00 (below threshold).
          correct niit_delta = 380.00.

        Defective code passes niit_magi=240_000 and niit_base_magi=0 (neither
        folds in the manual NII) -- both stay at or below the $250K MFJ
        threshold, so niit_delta=0.00 regardless of net_investment_income.
        """
        hh = _bare_mfj_household()
        base = base_income_for_year(hh, hh.base_year)

        # Guard the fixture: every base-year income source collapses to zero.
        assert base.opt == 0.0
        assert base.combined_ss == 0.0
        assert base.magi_addl == 0.0
        assert base.net_investment_income_addl == 0.0

        result = all_in_at_conversion(hh, base, 240_000.0, 20_000.0, ltcg_eligible=0.0)

        assert result.niit_magi == approx(260_000.0), (
            f"Expected niit_magi=260000.00 (conv=240000 + manual "
            f"net_inv_income=20000), got {result.niit_magi:.2f} -- the manual "
            f"NII is being added to total_net_inv_income but not to niit_magi "
            f"(engine/sweet_spot_compute.py)"
        )
        assert result.niit_delta == approx(380.00), (
            f"Expected niit_delta=380.00 (3.8% of min(20000 NII, 10000 excess) "
            f"once manual NII is folded into both niit_magi and "
            f"niit_base_magi), got {result.niit_delta:.2f}"
        )


def test_niit_helper_sanity() -> None:
    """Sanity-check the hand-derived NIIT arithmetic used above against the
    niit() function directly (not against the code under test)."""
    assert niit(260_000.0, 20_000.0, filing_status="MFJ") == approx(380.00)
    assert niit(20_000.0, 20_000.0, filing_status="MFJ") == approx(0.00)
    assert niit(240_000.0, 20_000.0, filing_status="MFJ") == approx(0.00)
