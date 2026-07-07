"""TDD tests for apply_conversion_grid_edits — audit-0706 w2 ui-primary-2.

Behavioral tests for the pure helper that validates/clamps data_editor output.
Uses repo-relative paths for any source inspection.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Import the helper under test.
# It lives in views/planner.py; we import it directly.
# ---------------------------------------------------------------------------
from views.planner import apply_conversion_grid_edits

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_yr(
    year: int = 2026,
    your_age: int = 62,
    spouse_age: int = 56,
    your_ira_begin: float = 500_000.0,
    spouse_ira_begin: float = 400_000.0,
    your_rmd_start_age: int = 73,
    spouse_rmd_start_age: int = 73,
    qcd_min_age: int = 71,
) -> dict:
    """Return a minimal 'year row' dict mirroring what apply_conversion_grid_edits receives."""
    return {
        "year": year,
        "your_age": your_age,
        "spouse_age": spouse_age,
        "your_ira_begin": your_ira_begin,
        "spouse_ira_begin": spouse_ira_begin,
        "your_rmd_start_age": your_rmd_start_age,
        "spouse_rmd_start_age": spouse_rmd_start_age,
        "qcd_min_age": qcd_min_age,
    }


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Build a DataFrame from a list of year-row dicts with editable columns."""
    records = []
    for r in rows:
        records.append(
            {
                "year": r["year"],
                "your_conv": r.get("your_conv", 0),
                "sp_conv": r.get("sp_conv", 0),
                "qcd": r.get("qcd", 0),
                "sp_qcd": r.get("sp_qcd", 0),
            }
        )
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# TestClampToIRABalance
# ---------------------------------------------------------------------------


class TestClampToIRABalance:
    """(i) A conversion exceeding the year's IRA balance is clamped + warning emitted."""

    def test_your_conv_clamped_to_ira_balance(self) -> None:
        yr_rows = [_make_yr(year=2026, your_ira_begin=200_000.0)]
        df = _make_df([{"year": 2026, "your_conv": 300_000, "sp_conv": 0, "qcd": 0, "sp_qcd": 0}])

        conv_your, conv_sp, qcd, sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert conv_your[2026] == pytest.approx(200_000.0), "should be clamped to IRA balance"
        assert len(warnings) >= 1
        assert any("2026" in w and "your" in w.lower() for w in warnings)

    def test_spouse_conv_clamped_to_ira_balance(self) -> None:
        yr_rows = [_make_yr(year=2027, spouse_ira_begin=150_000.0)]
        df = _make_df([{"year": 2027, "your_conv": 0, "sp_conv": 999_000, "qcd": 0, "sp_qcd": 0}])

        conv_your, conv_sp, qcd, sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert conv_sp[2027] == pytest.approx(150_000.0)
        assert any("2027" in w and "spouse" in w.lower() for w in warnings)

    def test_your_conv_zero_when_no_ira(self) -> None:
        yr_rows = [_make_yr(year=2026, your_ira_begin=0.0)]
        df = _make_df([{"year": 2026, "your_conv": 50_000, "sp_conv": 0, "qcd": 0, "sp_qcd": 0}])

        conv_your, conv_sp, qcd, sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        # Clamped to 0 → not stored in dict (zero entries are omitted)
        assert conv_your.get(2026, 0.0) == pytest.approx(0.0)
        assert len(warnings) >= 1

    def test_multiple_years_clamp_independently(self) -> None:
        yr_rows = [
            _make_yr(year=2026, your_ira_begin=100_000.0),
            _make_yr(year=2027, your_ira_begin=80_000.0),
        ]
        df = _make_df(
            [
                {"year": 2026, "your_conv": 150_000, "sp_conv": 0, "qcd": 0, "sp_qcd": 0},
                {"year": 2027, "your_conv": 50_000, "sp_conv": 0, "qcd": 0, "sp_qcd": 0},
            ]
        )

        conv_your, _conv_sp, _qcd, _sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        # 2026 clamped, 2027 within limit → passes through
        assert conv_your[2026] == pytest.approx(100_000.0)
        assert conv_your[2027] == pytest.approx(50_000.0)
        # Only one warning for the clamped year
        clamped_warnings = [w for w in warnings if "2026" in w and "your" in w.lower()]
        assert len(clamped_warnings) >= 1


# ---------------------------------------------------------------------------
# TestAgeGating
# ---------------------------------------------------------------------------


class TestAgeGating:
    """(ii) Age-gated cells (QCD before 71, conv in RMD era) are zeroed + warning."""

    def test_qcd_zeroed_below_qcd_age(self) -> None:
        yr_rows = [_make_yr(year=2026, your_age=65, qcd_min_age=71)]
        df = _make_df([{"year": 2026, "your_conv": 0, "sp_conv": 0, "qcd": 10_000, "sp_qcd": 0}])

        _conv_your, _conv_sp, qcd, sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert qcd.get(2026, 0) == pytest.approx(0.0)
        assert any("2026" in w and "qcd" in w.lower() for w in warnings)

    def test_spouse_qcd_zeroed_below_qcd_age(self) -> None:
        yr_rows = [_make_yr(year=2026, spouse_age=68, qcd_min_age=71)]
        df = _make_df([{"year": 2026, "your_conv": 0, "sp_conv": 0, "qcd": 0, "sp_qcd": 8_000}])

        _conv_your, _conv_sp, qcd, sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert sp_qcd.get(2026, 0) == pytest.approx(0.0)
        assert any("2026" in w and ("spouse" in w.lower() or "sp_qcd" in w.lower()) for w in warnings)

    def test_your_conv_zeroed_in_rmd_era(self) -> None:
        """Conversions are blocked once your_age >= your_rmd_start_age."""
        yr_rows = [_make_yr(year=2037, your_age=73, your_rmd_start_age=73)]
        df = _make_df([{"year": 2037, "your_conv": 100_000, "sp_conv": 0, "qcd": 0, "sp_qcd": 0}])

        conv_your, _conv_sp, _qcd, _sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert conv_your.get(2037, 0) == pytest.approx(0.0)
        assert any("2037" in w for w in warnings)

    def test_spouse_conv_zeroed_in_rmd_era(self) -> None:
        yr_rows = [_make_yr(year=2038, spouse_age=74, spouse_rmd_start_age=73)]
        df = _make_df([{"year": 2038, "your_conv": 0, "sp_conv": 50_000, "qcd": 0, "sp_qcd": 0}])

        _conv_your, conv_sp, _qcd, _sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert conv_sp.get(2038, 0) == pytest.approx(0.0)
        assert len(warnings) >= 1

    def test_qcd_allowed_at_exactly_qcd_min_age(self) -> None:
        """QCD is allowed at exactly QCD_MIN_AGE (71)."""
        yr_rows = [_make_yr(year=2026, your_age=71, qcd_min_age=71)]
        df = _make_df([{"year": 2026, "your_conv": 0, "sp_conv": 0, "qcd": 5_000, "sp_qcd": 0}])

        _conv_your, _conv_sp, qcd, _sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert qcd.get(2026, 0) == pytest.approx(5_000.0)
        # No QCD warning should have been issued for this row
        qcd_warnings = [w for w in warnings if "2026" in w and "qcd" in w.lower()]
        assert len(qcd_warnings) == 0


# ---------------------------------------------------------------------------
# TestValidPassThrough
# ---------------------------------------------------------------------------


class TestValidPassThrough:
    """(iii) Valid entries pass through unchanged and land in the right dicts keyed by year."""

    def test_valid_your_conv_passes_through(self) -> None:
        yr_rows = [_make_yr(year=2026, your_ira_begin=500_000.0)]
        df = _make_df([{"year": 2026, "your_conv": 100_000, "sp_conv": 0, "qcd": 0, "sp_qcd": 0}])

        conv_your, conv_sp, qcd, sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert conv_your[2026] == pytest.approx(100_000.0)
        assert conv_sp.get(2026, 0) == pytest.approx(0.0)
        assert qcd.get(2026, 0) == pytest.approx(0.0)
        assert sp_qcd.get(2026, 0) == pytest.approx(0.0)
        assert warnings == []

    def test_valid_spouse_conv_passes_through(self) -> None:
        yr_rows = [_make_yr(year=2026, spouse_ira_begin=400_000.0)]
        df = _make_df([{"year": 2026, "your_conv": 0, "sp_conv": 80_000, "qcd": 0, "sp_qcd": 0}])

        conv_your, conv_sp, qcd, sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert conv_sp[2026] == pytest.approx(80_000.0)
        assert warnings == []

    def test_valid_qcd_passes_through(self) -> None:
        yr_rows = [_make_yr(year=2026, your_age=72, qcd_min_age=71)]
        df = _make_df([{"year": 2026, "your_conv": 0, "sp_conv": 0, "qcd": 10_000, "sp_qcd": 0}])

        _conv_your, _conv_sp, qcd, _sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert qcd[2026] == pytest.approx(10_000.0)
        assert warnings == []

    def test_valid_sp_qcd_passes_through(self) -> None:
        yr_rows = [_make_yr(year=2026, spouse_age=73, qcd_min_age=71)]
        df = _make_df([{"year": 2026, "your_conv": 0, "sp_conv": 0, "qcd": 0, "sp_qcd": 7_500}])

        _conv_your, _conv_sp, _qcd, sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert sp_qcd[2026] == pytest.approx(7_500.0)
        assert warnings == []

    def test_multi_year_all_valid(self) -> None:
        yr_rows = [
            _make_yr(year=2026, your_ira_begin=500_000.0, spouse_ira_begin=400_000.0),
            _make_yr(year=2027, your_ira_begin=450_000.0, spouse_ira_begin=370_000.0),
            _make_yr(year=2028, your_age=75, your_rmd_start_age=73),  # in RMD era
        ]
        df = _make_df(
            [
                {"year": 2026, "your_conv": 100_000, "sp_conv": 80_000, "qcd": 0, "sp_qcd": 0},
                {"year": 2027, "your_conv": 120_000, "sp_conv": 90_000, "qcd": 0, "sp_qcd": 0},
                {"year": 2028, "your_conv": 0, "sp_conv": 0, "qcd": 0, "sp_qcd": 0},
            ]
        )

        conv_your, conv_sp, qcd, sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        assert conv_your[2026] == pytest.approx(100_000.0)
        assert conv_sp[2026] == pytest.approx(80_000.0)
        assert conv_your[2027] == pytest.approx(120_000.0)
        assert conv_sp[2027] == pytest.approx(90_000.0)
        assert conv_your.get(2028, 0) == pytest.approx(0.0)
        assert warnings == []

    def test_zero_entries_omitted_from_output(self) -> None:
        """Zero values should not produce entries in the output dicts (or produce 0 if present)."""
        yr_rows = [_make_yr(year=2026)]
        df = _make_df([{"year": 2026, "your_conv": 0, "sp_conv": 0, "qcd": 0, "sp_qcd": 0}])

        conv_your, conv_sp, qcd, sp_qcd, warnings = apply_conversion_grid_edits(df, yr_rows)

        # Either absent or zero — both acceptable contracts
        assert conv_your.get(2026, 0) == pytest.approx(0.0)
        assert warnings == []

    def test_return_type_contract(self) -> None:
        """Helper returns (dict, dict, dict, dict, list[str])."""
        yr_rows = [_make_yr(year=2026)]
        df = _make_df([{"year": 2026, "your_conv": 10_000, "sp_conv": 0, "qcd": 0, "sp_qcd": 0}])

        result = apply_conversion_grid_edits(df, yr_rows)

        assert isinstance(result, tuple)
        assert len(result) == 5
        conv_your, conv_sp, qcd, sp_qcd, warnings = result
        assert isinstance(conv_your, dict)
        assert isinstance(conv_sp, dict)
        assert isinstance(qcd, dict)
        assert isinstance(sp_qcd, dict)
        assert isinstance(warnings, list)
        assert all(isinstance(w, str) for w in warnings)

    def test_year_keys_are_ints(self) -> None:
        """Output dict keys must be int (matching existing session_state key convention)."""
        yr_rows = [_make_yr(year=2026, your_ira_begin=300_000.0)]
        df = _make_df([{"year": 2026, "your_conv": 50_000, "sp_conv": 0, "qcd": 0, "sp_qcd": 0}])

        conv_your, _conv_sp, _qcd, _sp_qcd, _warnings = apply_conversion_grid_edits(df, yr_rows)

        for k in conv_your:
            assert isinstance(k, int), f"Expected int key, got {type(k)}"
