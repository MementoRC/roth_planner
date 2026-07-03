"""Forward dividend yield forecasting for taxable brokerage accounts.

Produces a forward-looking yield estimate that the planner can use BEFORE
the 1099-DIV arrives (conversions must be set by Dec 31, 1099-DIV arrives
in February). Strategy chain:
    1. TTM derive: trailing 12-month per-position dividends / current shares
    2. yfinance lookup: Ticker.info['dividendRate'] for newly purchased positions
       with no payment history (optional dependency — gracefully skip on ImportError)
    3. Manual override: .dividend_rates.json {ticker: {"annual_rate": x, "qualified_fraction": y}}

The manual override (3) ALWAYS wins if present. (2) is used only when (1) yields
no result. Results are deterministic for a given input snapshot.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

# Per-security-type qualified-dividend defaults. Override per ticker via
# .dividend_rates.json. REITs/MLPs/bond funds are non-qualified (Section 199A
# REIT divs are non-qualified at federal level); etf_equity=1.0 is an
# optimistic approximation (broad ETFs ~92-95% qualified due to REIT holdings).
#
# International ETFs are split by sub-class (audit C10 / asset-dividend-1):
#   etf_intl_developed: developed-markets funds (VEA etc.) — Vanguard YE QDI
#       reports show ~81-82% qualified (IRC §1(h)(11)(C)).
#   etf_intl_emerging:  emerging-markets funds (VWO etc.) — lower treaty
#       coverage; documented estimate, refine from 1099-DIV box 1b/1a.
#   etf_intl:           total-international / VXUS and unknown-intl fallback.
QUALIFIED_DEFAULTS: dict[str, float] = {
    "equity": 1.0,  # individual stocks (TXN, AAPL, etc.)
    "etf_equity": 1.0,  # broad-market equity ETFs
    "etf_intl": 0.70,  # total-international / VXUS (FTC complications, mixed)
    "etf_intl_developed": 0.82,  # VEA & developed-mkts funds; Vanguard YE QDI ~81-82% (IRC §1(h)(11)(C))
    "etf_intl_emerging": 0.68,  # VWO & emerging-mkts funds; lower QDI (documented estimate, refine from 1099-DIV box 1b/1a)
    "reit": 0.0,
    "mlp": 0.0,
    "bond_fund": 0.0,
    "money_market": 0.0,
    "unknown": 0.85,  # safe-ish default for mixed funds
}

# Tickers we explicitly classify. Extend via .dividend_rates.json overrides.
# audit C10 / asset-dividend-1: VEA → etf_intl_developed, VWO → etf_intl_emerging;
# VXUS and unrecognised international tickers fall back to generic "etf_intl".
TICKER_CLASS: dict[str, str] = {
    "TXN": "equity",
    "VTI": "etf_equity",
    "VOO": "etf_equity",
    "SPY": "etf_equity",
    "IVV": "etf_equity",
    "QQQ": "etf_equity",
    "VTSAX": "etf_equity",
    "VXUS": "etf_intl",          # total-international: ~70% qualified
    "VEA": "etf_intl_developed",  # developed-markets: ~82% qualified
    "VWO": "etf_intl_emerging",   # emerging-markets: ~68% qualified
    "BND": "bond_fund",
    "AGG": "bond_fund",
    "VBTLX": "bond_fund",
    "VMFXX": "money_market",
    "SPAXX": "money_market",
    "FDRXX": "money_market",
    "VNQ": "reit",
}


@dataclass(frozen=True)
class Position:
    """Minimal portfolio position shape for forecasting."""

    ticker: str
    shares: float
    balance: float  # current market value of this position
    ttm_dividends: float = 0.0  # trailing 12-month dividends received

    @property
    def ttm_per_share(self) -> float:
        """Trailing-12-month dividends per CURRENT share.

        Divides by the current share count, not a shares-at-payment average
        (the Position shape carries no per-payment share history). For a
        position whose share count was stable across the TTM window this is
        exact; for a materially changed position it is only approximate and the
        forecast degrades to a dollar-carryforward of last year's TTM total —
        use the manual ``annual_rate`` override (strategy 3) for such positions.
        """
        return self.ttm_dividends / self.shares if self.shares > 0 else 0.0


@dataclass(frozen=True)
class DividendForecast:
    """Aggregate forward dividend estimate for a brokerage portfolio.

    TTM-strategy caveat (audit C10 / asset-dividend-3): when strategy 1 (TTM)
    is used, ``annual_income`` equals ``shares × (ttm_dividends / shares) =
    ttm_dividends`` — the trailing-12-month dollar total is carried forward
    unchanged and does NOT scale with share-count growth.  A position that grew
    from 100 → 120 shares will be understated by ~17%.  To obtain a
    forward-scaled figure, supply an ``annual_rate`` (per-share) override in
    ``.dividend_rates.json`` (strategy 3).
    """

    yield_rate: float
    qualified_fraction: float
    per_position: dict[str, dict[str, float]]  # ticker -> {annual_div, qualified}
    source_counts: dict[str, int]  # strategy -> count


def _load_overrides(path: Path) -> Mapping[str, Mapping[str, float]]:
    if not path.exists():
        return {}
    try:
        raw: dict[str, Mapping[str, float]] = json.loads(path.read_text())
        return {str(k).upper(): v for k, v in raw.items()}
    except (OSError, json.JSONDecodeError):
        return {}


def _yfinance_rate(ticker: str) -> float | None:
    """Optional fallback for new positions with no payment history."""
    try:
        import yfinance  # type: ignore[import-not-found,import-untyped]
    except ImportError:
        return None
    try:
        info = yfinance.Ticker(ticker).info
        rate = info.get("dividendRate")
        return float(rate) if rate is not None else None
    except Exception:
        return None


def _qualified_for(ticker: str, overrides: Mapping[str, Mapping[str, float]]) -> float:
    ov = overrides.get(ticker, {})
    if "qualified_fraction" in ov:
        return float(ov["qualified_fraction"])
    cls = TICKER_CLASS.get(ticker.upper(), "unknown")
    return QUALIFIED_DEFAULTS[cls]


def forecast_portfolio(
    positions: Iterable[Position],
    total_balance: float,
    overrides_path: Path | str = ".dividend_rates.json",
    use_yfinance: bool = False,
) -> DividendForecast:
    """Compute forward yield_rate + qualified_fraction for a brokerage portfolio.

    Args:
        positions: iterable of Position records (from portfolio sync)
        total_balance: total brokerage balance (denominator for yield_rate)
        overrides_path: path to .dividend_rates.json (relative to cwd)
        use_yfinance: enable strategy 2 (yfinance lookup). Default False — opt-in
            because it adds a network dependency.

    Returns DividendForecast with aggregate rates and per-position breakdown.
    """
    overrides = _load_overrides(Path(overrides_path))
    per_pos: dict[str, dict[str, float]] = {}
    src_counts = {"override": 0, "ttm": 0, "yfinance": 0, "none": 0}

    total_qualified = 0.0
    total_ordinary = 0.0

    for pos in positions:
        ticker = pos.ticker.upper()
        ov = overrides.get(ticker, {})
        annual_per_share: float | None = None

        if "annual_rate" in ov:
            annual_per_share = float(ov["annual_rate"])
            src_counts["override"] += 1
        elif pos.ttm_per_share > 0:
            # TTM dollar-carryforward: annual_income = shares × ttm_per_share =
            # ttm_dividends (unchanged).  Does NOT scale with share-count growth;
            # positions with significant share changes may be understated — supply
            # an annual_rate override in .dividend_rates.json for forward scaling.
            # (audit C10 / asset-dividend-3)
            annual_per_share = pos.ttm_per_share
            src_counts["ttm"] += 1
        elif use_yfinance:
            annual_per_share = _yfinance_rate(ticker)
            if annual_per_share is not None:
                src_counts["yfinance"] += 1

        if annual_per_share is None:
            src_counts["none"] += 1
            per_pos[ticker] = {"annual_div": 0.0, "qualified": _qualified_for(ticker, overrides)}
            continue

        annual_income = pos.shares * annual_per_share
        qual_frac = _qualified_for(ticker, overrides)
        per_pos[ticker] = {
            "annual_div": annual_income,
            "qualified": qual_frac,
        }
        total_qualified += annual_income * qual_frac
        total_ordinary += annual_income * (1.0 - qual_frac)

    total_income = total_qualified + total_ordinary
    if total_balance <= 0 or total_income <= 0:
        return DividendForecast(
            yield_rate=0.0,
            qualified_fraction=1.0,
            per_position=per_pos,
            source_counts=src_counts,
        )

    return DividendForecast(
        yield_rate=total_income / total_balance,
        qualified_fraction=total_qualified / total_income,
        per_position=per_pos,
        source_counts=src_counts,
    )
