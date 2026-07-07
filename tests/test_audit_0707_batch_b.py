"""Regression tests for audit-0707 Batch B fixes.

MU1-F02: effective_rate denominator must exclude tax-exempt muni interest.
UU4-UI-02: Roth phase-out reduced-limit must use math.ceil and $200 floor.
SU1-SEC-01: _try_load must refuse secret key files with lax permissions.
"""

from __future__ import annotations

import base64
import warnings
from pathlib import Path

import pytest


def _make_hh() -> object:
    from models.household import Household

    return Household(
        your_age=66,
        spouse_age=66,
        base_year=2026,
        cpi_assumption=0.0,
        your_ira=500_000.0,
        spouse_ira=500_000.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        grants=[],
    )


def _make_ytd(wages: float = 80_000.0, tax_exempt_interest: float = 0.0) -> object:
    from models.ytd_income import YTDSnapshot

    return YTDSnapshot(
        tax_year=2026,
        wages_ytd=wages,
        tax_exempt_interest_ytd=tax_exempt_interest,
    )


# ---------------------------------------------------------------------------
# MU1-F02 — effective_rate denominator excludes tax-exempt muni interest
# ---------------------------------------------------------------------------


class TestEffectiveRateDenominator:
    """effective_rate must use niit_magi_with_ss (excl. muni) not magi_ytd (incl. muni)."""

    def test_muni_interest_does_not_change_effective_rate(self) -> None:
        """Adding tax-exempt muni interest must NOT change effective_rate.

        Post-fix: denominator = niit_magi_with_ss (excludes muni). Since muni is
        also not taxed (numerator unchanged), effective_rate stays constant when
        muni interest is added. Pre-fix (magi_ytd denominator) would lower the rate.
        """
        from engine.tax import estimate_ytd_federal_tax

        hh = _make_hh()
        ytd_no_muni = _make_ytd(wages=150_000.0, tax_exempt_interest=0.0)
        ytd_with_muni = _make_ytd(wages=150_000.0, tax_exempt_interest=20_000.0)

        est_no_muni = estimate_ytd_federal_tax(ytd_no_muni, hh, combined_ss=0.0)
        est_with_muni = estimate_ytd_federal_tax(ytd_with_muni, hh, combined_ss=0.0)

        # Under the fix, both denominators equal niit_magi_ytd (no muni) and the
        # numerators are identical (muni is not taxed), so rates must be equal.
        assert est_no_muni.effective_rate == pytest.approx(est_with_muni.effective_rate, rel=1e-9), (
            "effective_rate must be identical when muni interest is added "
            "(muni excluded from niit_magi_with_ss denominator)"
        )

    def test_effective_rate_equals_tax_over_niit_magi_with_ss(self) -> None:
        """effective_rate == total / niit_magi_ytd (no SS so tss=0)."""
        from engine.tax import estimate_ytd_federal_tax

        hh = _make_hh()
        ytd = _make_ytd(wages=150_000.0, tax_exempt_interest=30_000.0)
        est = estimate_ytd_federal_tax(ytd, hh, combined_ss=0.0)

        # niit_magi_with_ss = niit_magi_ytd + tss; tss=0 (combined_ss=0)
        expected_base = ytd.niit_magi_ytd  # = 150_000 (muni stripped)
        expected_rate = est.total / expected_base if expected_base > 0 else 0.0

        assert est.effective_rate == pytest.approx(expected_rate, rel=1e-9), (
            f"effective_rate {est.effective_rate:.6f} != total/niit_magi_ytd {expected_rate:.6f}"
        )

    def test_effective_rate_strictly_higher_than_magi_ytd_denominator(self) -> None:
        """With muni interest, rate using niit_magi (smaller) > rate using magi_ytd (larger)."""
        from engine.tax import estimate_ytd_federal_tax

        hh = _make_hh()
        ytd = _make_ytd(wages=150_000.0, tax_exempt_interest=30_000.0)
        est = estimate_ytd_federal_tax(ytd, hh, combined_ss=0.0)

        rate_with_magi_ytd_denom = est.total / ytd.magi_ytd       # pre-fix (wrong, lower)
        rate_with_niit_magi_denom = est.total / ytd.niit_magi_ytd  # post-fix (correct, higher)

        assert est.effective_rate == pytest.approx(rate_with_niit_magi_denom, rel=1e-9)
        assert est.effective_rate > rate_with_magi_ytd_denom, (
            "effective_rate must be higher than pre-fix value (muni inflates old denominator)"
        )

    def test_effective_rate_zero_income_no_divide_by_zero(self) -> None:
        """Zero income must not raise ZeroDivisionError; effective_rate == 0."""
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        hh = _make_hh()
        ytd = YTDSnapshot(tax_year=2026)  # all zeros
        est = estimate_ytd_federal_tax(ytd, hh, combined_ss=0.0)
        assert est.effective_rate == 0.0


# ---------------------------------------------------------------------------
# UU4-UI-02 — Roth phase-out: math.ceil rounding + $200 floor
# ---------------------------------------------------------------------------


class TestRothPhaseoutRounding:
    """_phase_out must ceil-round to next $10 and apply $200 minimum floor."""

    def test_fractional_reduced_rounds_up_not_banker(self) -> None:
        """A reduced amount that is not a multiple of $10 rounds UP.

        Example: limit=$7000, 38% through phase-out → reduced ≈ $4340
        Banker's round(4340/10)*10 = 4340 (exact multiple, trivial).
        Use a value that exercises ceil: reduced=4321 → ceil→4330.
        """
        from views.roth_eligibility import _phase_out

        # Construct (magi, lower, upper, limit) so that reduced = 4321.0 exactly.
        # reduced = limit * (upper - magi) / (upper - lower)
        # 4321 = 7000 * (upper - magi) / (upper - lower)
        # fraction = 4321/7000; e.g. upper=10000, lower=0, magi=10000*(1-4321/7000)
        limit = 7_000.0
        lower = 0.0
        upper = 7_000.0
        magi = upper - (4_321.0 / limit) * (upper - lower)  # = 7000 - 4321 = 2679

        result = _phase_out(magi, lower, upper, limit)
        assert result == 4_330.0, (
            f"Expected ceil-rounded 4330, got {result}. "
            "Must use math.ceil not banker's round."
        )

    def test_exact_multiple_of_10_unchanged(self) -> None:
        """A reduced amount that is already a multiple of $10 stays unchanged."""
        from views.roth_eligibility import _phase_out

        limit = 7_000.0
        lower = 0.0
        upper = 7_000.0
        # Make reduced = 4330.0 exactly
        magi = upper - (4_330.0 / limit) * (upper - lower)

        result = _phase_out(magi, lower, upper, limit)
        assert result == 4_330.0

    def test_positive_result_below_200_floors_to_200(self) -> None:
        """A positive reduced result < $200 must be raised to $200."""
        from views.roth_eligibility import _phase_out

        # Make reduced = 90.0 (positive but < $200)
        limit = 7_000.0
        lower = 0.0
        upper = 7_000.0
        magi = upper - (90.0 / limit) * (upper - lower)  # = 7000 - 90 = 6910

        result = _phase_out(magi, lower, upper, limit)
        assert result == 200.0, (
            f"Expected $200 floor for small positive result, got {result}"
        )

    def test_fully_phased_out_returns_zero_not_200(self) -> None:
        """A taxpayer above the upper bound gets $0 — floor does not apply."""
        from views.roth_eligibility import _phase_out

        result = _phase_out(magi=300_000.0, lower=242_000.0, upper=252_000.0, limit=7_500.0)
        assert result == 0.0, f"Expected 0 for fully-phased-out, got {result}"

    def test_below_lower_returns_full_limit(self) -> None:
        """MAGI below lower threshold → full limit returned (no phase-out)."""
        from views.roth_eligibility import _phase_out

        result = _phase_out(magi=200_000.0, lower=242_000.0, upper=252_000.0, limit=7_500.0)
        assert result == 7_500.0


# ---------------------------------------------------------------------------
# SU1-SEC-01 — _try_load refuses secret key files with lax permissions
# ---------------------------------------------------------------------------


class TestSecretKeyPermissions:
    """_try_load(secret=True) must return None for files with lax permissions."""

    def _write_key_with_mode(self, path: Path, mode: int) -> bytes:
        key = b"\xab" * 32
        encoded = base64.b64encode(key).decode("ascii") + "\n"
        path.write_text(encoded)
        path.chmod(mode)
        return key

    def test_secret_lax_644_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Private key file with mode 0o644 must be refused (returns None)."""
        from engine.data_bridge_keys import _try_load

        key_file = tmp_path / "data-bridge.priv"
        self._write_key_with_mode(key_file, 0o644)
        monkeypatch.delenv("ROTH_PLANNER_DATA_BRIDGE_PRIVKEY", raising=False)

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _try_load("ROTH_PLANNER_DATA_BRIDGE_PRIVKEY", key_file, secret=True)

        assert result is None, (
            f"Expected None for 0o644 secret key, got {result!r}"
        )

    def test_secret_lax_640_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Private key file with mode 0o640 must also be refused."""
        from engine.data_bridge_keys import _try_load

        key_file = tmp_path / "data-bridge.priv"
        self._write_key_with_mode(key_file, 0o640)
        monkeypatch.delenv("ROTH_PLANNER_DATA_BRIDGE_PRIVKEY", raising=False)

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = _try_load("ROTH_PLANNER_DATA_BRIDGE_PRIVKEY", key_file, secret=True)

        assert result is None

    def test_secret_strict_600_loads_normally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Private key file with mode 0o600 must load successfully."""
        from engine.data_bridge_keys import _try_load

        key_file = tmp_path / "data-bridge.priv"
        expected_key = self._write_key_with_mode(key_file, 0o600)
        monkeypatch.delenv("ROTH_PLANNER_DATA_BRIDGE_PRIVKEY", raising=False)

        result = _try_load("ROTH_PLANNER_DATA_BRIDGE_PRIVKEY", key_file, secret=True)
        assert result == expected_key

    def test_non_secret_lax_644_still_loads_with_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Public key (secret=False) with 0o644 loads successfully but emits a warning."""
        from engine.data_bridge_keys import _try_load

        key_file = tmp_path / "data-bridge.pub"
        expected_key = self._write_key_with_mode(key_file, 0o644)
        monkeypatch.delenv("ROTH_PLANNER_DATA_BRIDGE_PUBKEY", raising=False)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _try_load("ROTH_PLANNER_DATA_BRIDGE_PUBKEY", key_file, secret=False)

        assert result == expected_key, "Public key with 0o644 should load"
        assert any(issubclass(warning.category, RuntimeWarning) for warning in w), (
            "Expected RuntimeWarning for lax-permission public key"
        )

    def test_load_privkey_uses_secret_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_privkey() must use secret=True, returning None for a lax-permission file."""
        from engine.data_bridge_keys import load_privkey

        key_file = tmp_path / "data-bridge.priv"
        self._write_key_with_mode(key_file, 0o644)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", key_file)
        monkeypatch.delenv("ROTH_PLANNER_DATA_BRIDGE_PRIVKEY", raising=False)

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = load_privkey()

        assert result is None, (
            "load_privkey() with 0o644 file must return None (secret=True path)"
        )
