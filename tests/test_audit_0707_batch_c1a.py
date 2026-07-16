"""Audit-0707 Batch C1a — verification tests for 5 targeted fixes.

Fixes covered:
  MU4-F1     niit.py       — NIIT threshold dict for all 4 filing statuses
  MU6-F2     scenario.py   — LTCG_RATES_SINGLE imported and used for Single filer
  UU5-UI-04  app.py + upload_merge.py — prior_year_magi keeps genuine 0.0 values
  PU1-M03    ytd.py        — NEC label match tightened to '1099-nec'
  UU2-UI-06  parameters.py — min_value=0 on SS FRA inputs (comment-only; no test)
"""
from __future__ import annotations

import pytest

from engine.niit import (
    NIIT_THRESHOLD_HOH,
    NIIT_THRESHOLD_MFJ,
    NIIT_THRESHOLD_MFS,
    NIIT_THRESHOLD_SINGLE,
    niit,
)
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan
from engine.tax import LTCG_RATES_MFJ, LTCG_RATES_SINGLE
from engine.upload_merge import extract_bundle_magi
from models.household import Household

# ── FIX 1: MU4-F1 — NIIT thresholds for all 4 filing statuses ───────────────


@pytest.mark.parametrize(
    ("filing_status", "threshold"),
    [
        ("MFJ", NIIT_THRESHOLD_MFJ),
        ("Single", NIIT_THRESHOLD_SINGLE),
        ("HoH", NIIT_THRESHOLD_HOH),
        ("MFS", NIIT_THRESHOLD_MFS),
    ],
)
def test_niit_threshold_by_filing_status(filing_status: str, threshold: int) -> None:
    """Each filing status selects the correct statutory NIIT threshold (IRC §1411)."""
    result_at = niit(float(threshold), 50_000.0, filing_status=filing_status)
    assert result_at == 0.0, f"{filing_status}: expected 0 at threshold, got {result_at}"

    result_above = niit(float(threshold) + 1.0, 50_000.0, filing_status=filing_status)
    assert result_above > 0.0, f"{filing_status}: expected NIIT > 0 above threshold"


def test_niit_threshold_statutory_values() -> None:
    """Statutory values: MFJ=250k, Single=HoH=200k, MFS=125k."""
    assert NIIT_THRESHOLD_MFJ == 250_000
    assert NIIT_THRESHOLD_SINGLE == 200_000
    assert NIIT_THRESHOLD_HOH == 200_000
    assert NIIT_THRESHOLD_MFS == 125_000


def test_niit_unknown_status_falls_back_to_mfj() -> None:
    """Unknown filing status must not raise KeyError; falls back to MFJ threshold."""
    # $220k MAGI — below MFJ ($250k) but above Single ($200k) threshold
    result = niit(220_000.0, 50_000.0, filing_status="UnknownStatus")
    assert result == 0.0  # below MFJ fallback threshold

    result_high = niit(260_000.0, 50_000.0, filing_status="UnknownStatus")
    assert result_high > 0.0  # above MFJ fallback threshold


def test_niit_hoh_and_mfs_not_using_mfj_threshold() -> None:
    """HoH and MFS must NOT use the MFJ $250k threshold (prior bug fix)."""
    # $230k MAGI: above HoH/Single (200k), below MFJ (250k)
    hoh_result = niit(230_000.0, 50_000.0, filing_status="HoH")
    assert hoh_result > 0.0, "HoH: NIIT should apply at $230k (threshold is $200k)"

    # $130k MAGI: above MFS (125k), below all others
    mfs_result = niit(130_000.0, 50_000.0, filing_status="MFS")
    assert mfs_result > 0.0, "MFS: NIIT should apply at $130k (threshold is $125k)"


# ── FIX 2: MU6-F2 — LTCG_RATES_SINGLE imported and used for Single filers ────


def test_ltcg_rates_single_is_importable() -> None:
    """LTCG_RATES_SINGLE must be importable (was never imported before fix)."""
    assert isinstance(LTCG_RATES_SINGLE, tuple)
    assert len(LTCG_RATES_SINGLE) == 3
    assert LTCG_RATES_SINGLE[0] == 0.0
    assert LTCG_RATES_SINGLE[1] == 0.15
    assert LTCG_RATES_SINGLE[2] == 0.20


def test_ltcg_rates_single_equals_mfj_today() -> None:
    """Values are identical today — no behavioral change, latent trap closed."""
    assert LTCG_RATES_SINGLE == LTCG_RATES_MFJ


def test_run_scenario_single_filer_brokerage_gain_tax() -> None:
    """Single filer scenario must produce a valid brokerage_gain_tax (uses fixed path)."""
    hh = Household(
        your_age=65,
        spouse_age=65,
        your_ira=500_000,
        spouse_ira=0,
        your_ss_fra=2_000,
        spouse_ss_fra=0,
        filing_status="Single",
    )
    plan = ConversionPlan()  # all-zeros default
    result = run_scenario(hh, plan, name="SingleTest", end_age=67)
    assert result is not None
    assert len(result.years) >= 1
    yr0 = result.years[0]
    assert isinstance(yr0.brokerage_gain_tax, float)


# ── FIX 3: UU5-UI-04 — prior_year_magi keeps genuine 0.0 values ─────────────


def test_upload_merge_keeps_zero_magi_year() -> None:
    """A 0.0 MAGI year must NOT be dropped (prior bug: `if v` filtered falsy 0.0).

    Wave 5 (Setup / Command Center): bundle MAGI extraction moved out of
    build_user_defaults_session_updates (which no longer touches
    prior_year_magi at all — defect #2) into the pure
    engine.upload_merge.extract_bundle_magi, called by the Data Bridge view
    ahead of record_magi_candidates(Source.BUNDLE, ...). This test now
    exercises that function directly; the 0.0-preservation behavior is
    unchanged.
    """
    data = {"prior_year_magi": {"2023": 0.0, "2024": 150_000.0}}
    pym = extract_bundle_magi(data)
    assert 2023 in pym, "Year 2023 with 0.0 MAGI was incorrectly dropped"
    assert pym[2023] == 0.0
    assert pym[2024] == 150_000.0


def test_upload_merge_drops_none_and_empty_string() -> None:
    """None and empty-string values are still excluded (see extract_bundle_magi)."""
    data = {"prior_year_magi": {"2023": None, "2024": "", "2025": 50_000.0}}
    pym = extract_bundle_magi(data)
    assert 2023 not in pym
    assert 2024 not in pym
    assert 2025 in pym


# ── FIX 4: PU1-M03 — NEC label tightened to '1099-nec' ───────────────────────
# Removed: _parse_ytd_income_rows (and the FinExtract tax_return/ytd_income
# endpoint it parsed) has been retired — see engine/portfolio_sync/ytd.py.


# ── FIX 5: UU2-UI-06 — min_value=0 on SS FRA inputs ─────────────────────────
# Streamlit widget arguments are not unit-testable without a running Streamlit app.
# Change verified by inspection: views/setup/parameters.py lines ~441 and ~517
# both now include `min_value=0,  # UU2-UI-06`.
# No automated test for this fix per task specification.
