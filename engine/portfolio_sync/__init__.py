"""engine.portfolio_sync — package facade.

Re-exports every symbol previously importable from the monolithic
engine/portfolio_sync.py module so existing callers keep working unchanged.
"""

from __future__ import annotations

from .awards import fetch_equity_awards, fetch_shares
from .classify import (
    _classify_account,
    _classify_symbol,
    _parse_quantity,
    _resolve_override,
    _resolve_owner_hint,
)
from .client import BASE_URL, _flatten_query_rows, _headers, _load_token
from .dividends import apply_dividends_rollup, fetch_dividends_rollup
from .exercises import (
    _grant_id_substring_match,
    _normalize_grant_id,
    _parse_equity_sales_lots,
    _parse_option_exercises_rows,
    apply_option_exercises,
    fetch_option_exercises,
    fetch_option_exercises_with_cache,
)
from .holdings import (
    _derive_ttm_dividends,
    fetch_holdings,
    merge_snapshots,
    positions_for_forecast,
    positions_for_forecast_multi,
)
from .magi import apply_magi, fetch_magi
from .portfolio import _CACHE_PATH, fetch_portfolio, load_snapshot, save_snapshot
from .shapes import (
    ASSET_CLASS,
    EXPECTED_RETURNS,
    AccountSummary,
    DividendsRollupSnapshot,
    EquityGrant,
    Holding,
    MagiSnapshot,
    OptionExercisesSnapshot,
    PortfolioSnapshot,
    SSABenefitEstimate,
    SSASnapshot,
)
from .social_security import (
    _SSA_CACHE_PATH,
    fetch_ssa_benefit_estimates,
    fetch_ssa_snapshot,
    load_ssa_snapshot,
    match_fra_estimate,
    save_ssa_snapshot,
)
from .ytd import (
    _YTD_CACHE_PATH,
    fetch_ytd_snapshot,
    load_ytd_snapshot,
    save_ytd_snapshot,
)

__all__ = [
    "ASSET_CLASS",
    "AccountSummary",
    "BASE_URL",
    "DividendsRollupSnapshot",
    "EXPECTED_RETURNS",
    "EquityGrant",
    "Holding",
    "MagiSnapshot",
    "OptionExercisesSnapshot",
    "PortfolioSnapshot",
    "SSABenefitEstimate",
    "SSASnapshot",
    "_CACHE_PATH",
    "_SSA_CACHE_PATH",
    "_YTD_CACHE_PATH",
    "_classify_account",
    "_classify_symbol",
    "_derive_ttm_dividends",
    "_flatten_query_rows",
    "_grant_id_substring_match",
    "_headers",
    "_load_token",
    "_normalize_grant_id",
    "_parse_equity_sales_lots",
    "_parse_option_exercises_rows",
    "_parse_quantity",
    "_resolve_override",
    "_resolve_owner_hint",
    "apply_dividends_rollup",
    "apply_magi",
    "apply_option_exercises",
    "fetch_dividends_rollup",
    "fetch_equity_awards",
    "fetch_holdings",
    "fetch_magi",
    "fetch_option_exercises",
    "fetch_option_exercises_with_cache",
    "fetch_portfolio",
    "fetch_shares",
    "fetch_ssa_benefit_estimates",
    "fetch_ssa_snapshot",
    "fetch_ytd_snapshot",
    "load_snapshot",
    "load_ssa_snapshot",
    "load_ytd_snapshot",
    "match_fra_estimate",
    "merge_snapshots",
    "positions_for_forecast",
    "positions_for_forecast_multi",
    "save_snapshot",
    "save_ssa_snapshot",
    "save_ytd_snapshot",
]

# --- Test-monkeypatch propagation hook ---
# Each reexport name resolves to a binding in the package namespace and a
# separate binding inside its owning sub-module (where the function/constant
# is actually used). Test code that does `monkeypatch.setattr(engine.portfolio_sync, X, ...)`
# only updates the package binding — the sub-module would otherwise still see
# the original. The custom module __setattr__ below forwards writes for every
# reexported symbol to the sub-module that owns it.
import sys as _sys
from types import ModuleType as _ModuleType

from . import (
    awards as _awards,
)
from . import (
    classify as _classify,
)
from . import (
    client as _client,
)
from . import (
    dividends as _dividends,
)
from . import (
    exercises as _exercises,
)
from . import (
    holdings as _holdings,
)
from . import (
    magi as _magi,
)
from . import (
    portfolio as _portfolio,
)
from . import (
    shapes as _shapes,
)
from . import (
    social_security as _social_security,
)
from . import (
    ytd as _ytd,
)

_REEXPORT_OWNERS: dict[str, _ModuleType] = {
    "ASSET_CLASS": _shapes,
    "AccountSummary": _shapes,
    "BASE_URL": _client,
    "DividendsRollupSnapshot": _shapes,
    "EXPECTED_RETURNS": _shapes,
    "EquityGrant": _shapes,
    "Holding": _shapes,
    "MagiSnapshot": _shapes,
    "OptionExercisesSnapshot": _shapes,
    "PortfolioSnapshot": _shapes,
    "SSABenefitEstimate": _shapes,
    "SSASnapshot": _shapes,
    "_CACHE_PATH": _portfolio,
    "_SSA_CACHE_PATH": _social_security,
    "_YTD_CACHE_PATH": _ytd,
    "_classify_account": _classify,
    "_classify_symbol": _classify,
    "_derive_ttm_dividends": _holdings,
    "_flatten_query_rows": _client,
    "_grant_id_substring_match": _exercises,
    "_headers": _client,
    "_load_token": _client,
    "_normalize_grant_id": _exercises,
    "_parse_equity_sales_lots": _exercises,
    "_parse_option_exercises_rows": _exercises,
    "_parse_quantity": _classify,
    "_resolve_override": _classify,
    "_resolve_owner_hint": _classify,
    "apply_dividends_rollup": _dividends,
    "apply_magi": _magi,
    "apply_option_exercises": _exercises,
    "fetch_dividends_rollup": _dividends,
    "fetch_equity_awards": _awards,
    "fetch_holdings": _holdings,
    "fetch_magi": _magi,
    "fetch_option_exercises": _exercises,
    "fetch_option_exercises_with_cache": _exercises,
    "fetch_portfolio": _portfolio,
    "fetch_shares": _awards,
    "fetch_ssa_benefit_estimates": _social_security,
    "fetch_ssa_snapshot": _social_security,
    "fetch_ytd_snapshot": _ytd,
    "load_snapshot": _portfolio,
    "load_ssa_snapshot": _social_security,
    "load_ytd_snapshot": _ytd,
    "match_fra_estimate": _social_security,
    "merge_snapshots": _holdings,
    "positions_for_forecast": _holdings,
    "positions_for_forecast_multi": _holdings,
    "save_snapshot": _portfolio,
    "save_ssa_snapshot": _social_security,
    "save_ytd_snapshot": _ytd,
}


class _PortfolioSyncPackage(_ModuleType):
    """Custom package class that forwards reexport writes to owning sub-modules."""

    def __setattr__(self, name, value):  # type: ignore[override]
        _ModuleType.__setattr__(self, name, value)
        target = _REEXPORT_OWNERS.get(name)
        if target is not None:
            _ModuleType.__setattr__(target, name, value)


_sys.modules[__name__].__class__ = _PortfolioSyncPackage
