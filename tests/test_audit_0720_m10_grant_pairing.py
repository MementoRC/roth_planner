"""Regression test for audit-0720 finding M10.

views/portfolio.py's "vs. Planner Defaults" comparison table paired
``snap.equity_grants[i]`` with ``hh.grants[i]`` purely by positional index
instead of grant identity, so the two rows misalign whenever the lists
differ in order/length (e.g. FinExtract's 2019 grant compared against the
Planner's 2020 default after PR #374 dropped the 2019 grant from
``hh.grants``).

Step 1 proves the pre-fix positional pairing misaligns (RED). Step 2 tests
the extracted pure ``_pair_grants`` helper directly, which is the preferred,
robust approach per the finding note.
"""

from __future__ import annotations

from engine.portfolio_sync import EquityGrant
from models.grants import StockGrant

# --- Step 1: reproduce the positional-pairing bug (mirrors the pre-fix inline
# zip-by-index logic in views/portfolio.py render()) -----------------------


def _positional_pairing(
    snap_grants: list[EquityGrant], planner_grants: list[StockGrant]
) -> list[tuple[EquityGrant, StockGrant | None]]:
    """Literal copy of the buggy pre-fix pairing in views/portfolio.py render()."""
    return [
        (g, planner_grants[i] if i < len(planner_grants) else None)
        for i, g in enumerate(snap_grants)
    ]


def _grants_2019_2020_2021() -> tuple[list[EquityGrant], list[StockGrant]]:
    snap_grants = [
        EquityGrant(
            grant_id="",
            grant_type="NQO",
            grant_date="2019-03-01",
            shares_granted=650,
            outstanding=650,
            current_value=50_000.0,
        ),
        EquityGrant(
            grant_id="",
            grant_type="NQO",
            grant_date="2020-03-01",
            shares_granted=400,
            outstanding=400,
            current_value=30_000.0,
        ),
        EquityGrant(
            grant_id="",
            grant_type="NQO",
            grant_date="2021-03-01",
            shares_granted=300,
            outstanding=300,
            current_value=20_000.0,
        ),
    ]
    # PR #374: the 2019 grant was dropped from hh.grants (planner order).
    planner_grants = [
        StockGrant(year=2020, strike=130.0, shares=400, expiry_year=2030),
        StockGrant(year=2021, strike=169.0, shares=300, expiry_year=2031),
    ]
    return snap_grants, planner_grants


def test_positional_pairing_misaligns_2019_finextract_with_2020_planner() -> None:
    """RED evidence: the pre-fix positional zip pairs the 2019 FinExtract row
    with the 2020 Planner Default row -- a wrong pairing."""
    snap_grants, planner_grants = _grants_2019_2020_2021()
    pairs = _positional_pairing(snap_grants, planner_grants)

    assert pairs[0][0].grant_date == "2019-03-01"
    # BUG: index-0 planner grant is year 2020, not a 2019 counterpart.
    assert pairs[0][1] is not None
    assert pairs[0][1].year == 2020  # wrong pairing reproduced


# --- Step 2: the extracted pure helper (post-fix) --------------------------


def test_pair_grants_matches_by_year_identity() -> None:
    from views.portfolio import _pair_grants

    snap_grants, planner_grants = _grants_2019_2020_2021()
    pairs = _pair_grants(snap_grants, planner_grants)

    by_snap_year = {
        (int(g.grant_date.split("-")[0]) if g else None, p.year if p else None) for g, p in pairs
    }
    assert (2019, None) in by_snap_year  # no planner counterpart
    assert (2020, 2020) in by_snap_year
    assert (2021, 2021) in by_snap_year
    assert len(pairs) == 3


def test_pair_grants_includes_planner_only_grant_with_blank_snapshot_side() -> None:
    from views.portfolio import _pair_grants

    snap_grants = [
        EquityGrant(
            grant_id="",
            grant_type="NQO",
            grant_date="2021-03-01",
            shares_granted=300,
            outstanding=300,
            current_value=20_000.0,
        )
    ]
    planner_grants = [
        StockGrant(year=2020, strike=130.0, shares=400, expiry_year=2030),
        StockGrant(year=2021, strike=169.0, shares=300, expiry_year=2031),
    ]
    pairs = _pair_grants(snap_grants, planner_grants)

    assert len(pairs) == 2
    planner_only = [p for g, p in pairs if g is None]
    assert len(planner_only) == 1
    assert planner_only[0].year == 2020
