"""Per-ticker qualified-vs-ordinary split of the forward dividend estimate.

Pure presentation helper over ``DividendForecast.per_position`` so a view can
show WHICH holdings drive the qualified fraction. Figures are the forecast's
annual dollars, not YTD actuals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class TickerDividendRow:
    ticker: str
    annual_div: float
    qualified: float
    ordinary: float
    qualified_pct: float  # 0-1


@dataclass(frozen=True)
class QualifiedBreakdown:
    rows: tuple[TickerDividendRow, ...]
    total_annual_div: float
    total_qualified: float
    total_ordinary: float
    total_qualified_pct: float  # 0-1; 0.0 when there are no dividends


def qualified_breakdown(per_position: Mapping[str, Mapping[str, float]]) -> QualifiedBreakdown:
    """Split each ticker's forecast dividend into qualified and ordinary dollars.

    Rows are sorted by annual dividend descending (ticker breaks ties); tickers
    with no forecast dividend are dropped. The qualified fraction is clamped to
    0-1, mirroring ``GrowthProfile``. Totals sum the rows, so they equal
    ``DividendForecast.qualified_fraction`` x total income.
    """
    rows = []
    for ticker, data in per_position.items():
        annual = float(data.get("annual_div", 0.0))
        if annual <= 0:
            continue
        frac = max(0.0, min(1.0, float(data.get("qualified", 0.0))))
        rows.append(
            TickerDividendRow(
                ticker=ticker,
                annual_div=annual,
                qualified=annual * frac,
                ordinary=annual * (1.0 - frac),
                qualified_pct=frac,
            )
        )
    rows.sort(key=lambda r: (-r.annual_div, r.ticker))
    total = sum(r.annual_div for r in rows)
    total_q = sum(r.qualified for r in rows)
    return QualifiedBreakdown(
        rows=tuple(rows),
        total_annual_div=total,
        total_qualified=total_q,
        total_ordinary=sum(r.ordinary for r in rows),
        total_qualified_pct=total_q / total if total else 0.0,
    )
