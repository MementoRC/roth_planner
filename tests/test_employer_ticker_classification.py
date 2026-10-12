"""The employer's stock is classified by the configured ticker, not a literal.

The dividend-forecast and asset-class maps used to carry a hard-coded entry for
one specific employer ticker, so any other configured ticker fell through to the
dividend forecast's "unknown" class (85% qualified) instead of an individual
equity (100% qualified). The ticker now comes from configuration: an explicit
``employer_ticker`` argument, else the configured ``stock_ticker`` default.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from engine.dividend_forecast import (
    QUALIFIED_DEFAULTS,
    TICKER_CLASS,
    DividendForecast,
    Position,
    forecast_portfolio,
)
from engine.portfolio_sync.classify import _classify_symbol
from engine.portfolio_sync.shapes import ASSET_CLASS, AccountSummary, PortfolioSnapshot
from models.household import Household

_ROOT = Path(__file__).resolve().parent.parent
_EMPLOYER = "ZZZZ"
# SHA-256 of the formerly hard-coded employer ticker (upper case), so the guard
# below can check for it without the ticker appearing in source.
_LEGACY_TICKER_DIGEST = "cb1d474948e3b8bc870ce85f6710d036f1814a17937af0a07832e2e7cd693c5b"


def _is_legacy_ticker(value: str) -> bool:
    return hashlib.sha256(value.upper().encode()).hexdigest() == _LEGACY_TICKER_DIGEST


def _employer_position() -> Position:
    return Position(ticker=_EMPLOYER, shares=100, balance=10_000, ttm_dividends=300)


class TestDividendForecast:
    def test_explicit_employer_ticker_is_fully_qualified_equity(self, tmp_path) -> None:
        fcst = forecast_portfolio(
            [_employer_position()],
            total_balance=10_000,
            overrides_path=tmp_path / "absent.json",
            employer_ticker=_EMPLOYER,
        )
        assert fcst.qualified_fraction == pytest.approx(QUALIFIED_DEFAULTS["equity"])
        assert fcst.per_position[_EMPLOYER]["qualified"] == pytest.approx(1.0)

    def test_match_is_case_insensitive(self, tmp_path) -> None:
        fcst = forecast_portfolio(
            [_employer_position()],
            total_balance=10_000,
            overrides_path=tmp_path / "absent.json",
            employer_ticker=_EMPLOYER.lower(),
        )
        assert fcst.qualified_fraction == pytest.approx(1.0)

    def test_omitted_ticker_falls_back_to_configured_default(self, tmp_path, monkeypatch) -> None:
        import config.loader as loader

        monkeypatch.setattr(loader, "load_defaults", lambda: {"stock_ticker": _EMPLOYER})
        fcst = forecast_portfolio(
            [_employer_position()],
            total_balance=10_000,
            overrides_path=tmp_path / "absent.json",
        )
        assert fcst.qualified_fraction == pytest.approx(1.0)

    def test_other_unknown_ticker_stays_unknown(self, tmp_path) -> None:
        fcst = forecast_portfolio(
            [Position(ticker="QQQQ", shares=100, balance=10_000, ttm_dividends=300)],
            total_balance=10_000,
            overrides_path=tmp_path / "absent.json",
            employer_ticker=_EMPLOYER,
        )
        assert fcst.qualified_fraction == pytest.approx(QUALIFIED_DEFAULTS["unknown"])

    def test_override_still_wins_for_employer_ticker(self, tmp_path) -> None:
        ov = tmp_path / "rates.json"
        ov.write_text(json.dumps({_EMPLOYER: {"qualified_fraction": 0.5}}))
        fcst = forecast_portfolio(
            [_employer_position()],
            total_balance=10_000,
            overrides_path=ov,
            employer_ticker=_EMPLOYER,
        )
        assert fcst.qualified_fraction == pytest.approx(0.5)


class TestSnapshotGrowthThreadsTicker:
    def test_derive_snapshot_growth_passes_employer_ticker(self, monkeypatch) -> None:
        import engine.data_sources.snapshot_ingest as ingest

        seen: dict = {}

        def _spy(positions, total_balance, **kwargs):
            seen.update(kwargs)
            return DividendForecast(0.03, 1.0, {}, {})

        monkeypatch.setattr(ingest, "forecast_portfolio", _spy)
        snap = PortfolioSnapshot(
            accounts=[
                AccountSummary(
                    account_type="brokerage",
                    owner="you",
                    total_value=10_000,
                    equity_value=10_000,
                )
            ]
        )
        ingest.derive_snapshot_growth(Household(), snap, employer_ticker=_EMPLOYER)
        assert seen.get("employer_ticker") == _EMPLOYER

    def test_app_passes_configured_ticker(self) -> None:
        tree = ast.parse((_ROOT / "app.py").read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "derive_snapshot_growth"
        ]
        assert calls, "app.py no longer calls derive_snapshot_growth"
        for call in calls:
            kw = {k.arg: k.value for k in call.keywords}
            assert "employer_ticker" in kw
            value = kw["employer_ticker"]
            assert isinstance(value, ast.Call)
            assert isinstance(value.func, ast.Name)
            assert value.func.id == "configured_ticker"


class TestAssetClass:
    def test_configured_ticker_classifies_as_equity(self) -> None:
        assert _classify_symbol(_EMPLOYER) == "equity"


@pytest.mark.parametrize(
    "rel_path",
    ["engine/dividend_forecast.py", "engine/portfolio_sync/shapes.py"],
)
def test_no_hard_coded_employer_literal(rel_path: str) -> None:
    source = (_ROOT / rel_path).read_text()
    literals = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not any(_is_legacy_ticker(lit) for lit in literals)


def test_maps_carry_no_employer_entry() -> None:
    assert not any(_is_legacy_ticker(k) for k in TICKER_CLASS)
    assert not any(_is_legacy_ticker(k) for k in ASSET_CLASS)
