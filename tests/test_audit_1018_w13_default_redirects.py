"""audit-1018 W13 (F-52): the autouse conftest redirect must reach the
dividend-forecast function whose default argument was baked from a personal
repo-root file at import time (``monkeypatch.setattr`` on the constant cannot
reach it).

All paths here are synthetic.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import engine.dividend_forecast as dividend_forecast_mod

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _default(func, name: str) -> Path:
    return Path(inspect.signature(func).parameters[name].default)


def test_forecast_portfolio_default_overrides_is_redirected() -> None:
    path = _default(dividend_forecast_mod.forecast_portfolio, "overrides_path")
    assert path.is_absolute()
    assert path.resolve().parent != _REPO_ROOT
    assert not path.exists()


def test_forecast_portfolio_ignores_cwd_override_file(tmp_path, monkeypatch) -> None:
    """A personal ``.dividend_rates.json`` in the cwd must not change results."""
    from engine.dividend_forecast import Position, forecast_portfolio

    (tmp_path / ".dividend_rates.json").write_text('{"VNQ": {"qualified_fraction": 1.0}}')
    monkeypatch.chdir(tmp_path)

    result = forecast_portfolio(
        [Position(ticker="VNQ", shares=100, balance=10_000, ttm_dividends=400)],
        total_balance=10_000,
    )
    # VNQ is a REIT: classified non-qualified by default; the cwd override says 1.0.
    assert result.qualified_fraction == pytest.approx(0.0)
    assert result.source_counts["override"] == 0
