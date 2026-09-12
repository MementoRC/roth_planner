"""Pure grid logic for the Option Exercise Planner view (NO Streamlit imports).

Normalizes edited share cells against each grant's expiry bound and computes the
live 'remaining' readout — extracted so enforcement + arithmetic are unit-testable
without a Streamlit AppTest.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.grants import StockGrant, aggregate_by_key


@dataclass
class GridNormalization:
    shares_by_key: dict[str, dict[int, int]]  # cleaned, expiry-bounded, positive-only
    remaining_by_key: dict[str, int]  # grant.shares - scheduled
    out_of_range: list[tuple[StockGrant, int, int]]  # (grant, year, entered) past expiry


def normalize_grid_edits(
    grants: list[StockGrant],
    years: list[int],
    raw_by_key: dict[str, dict[int, int]],
) -> GridNormalization:
    """Clamp edited cells to each grant's ``expiry_year``, drop non-positive
    counts, cap the running total across ``years`` at ``grant.shares`` (audit-
    0721 C11 — a per-cell-only bound let the SUM across years exceed the
    grant, fabricating income downstream), and compute remaining. A positive
    count in a year past the grant's expiry is rejected (recorded in
    ``out_of_range``) and excluded from the schedule — the view can't
    hard-disable individual data_editor cells, so the bound is enforced on
    read here. The aggregate cap is applied in ``years`` order, so later
    years are clamped down first (deterministic, favors earlier exercises).

    Grants are collapsed through ``aggregate_by_key`` first: ``key()`` is
    content-based, so two grants with an empty ``grant_id`` that share
    year+strike+expiry_year are ONE lot (e.g. a single award split across two
    importer rows). Without the collapse each colliding grant got an
    independent full ``grant.shares`` budget and the last one's cleaned cells
    overwrote the first's, under-counting the lot's capacity and its remaining
    shares (audit-0823 GRID-KEY-COLLISION). This is the 4th consumer to route
    through the shared resolver, alongside ``ExerciseSchedule.income_for``/
    ``default_at_expiry`` and ``engine.exercise_optimizer``."""
    shares_by_key: dict[str, dict[int, int]] = {}
    remaining_by_key: dict[str, int] = {}
    out_of_range: list[tuple[StockGrant, int, int]] = []
    for grant in aggregate_by_key(grants):
        key = grant.key()
        cells = raw_by_key.get(key, {})
        cleaned: dict[int, int] = {}
        scheduled_total = 0
        for year in years:
            n = int(cells.get(year, 0) or 0)
            if n <= 0:
                continue
            if year > grant.expiry_year:
                out_of_range.append((grant, year, n))
                continue
            budget = grant.shares - scheduled_total
            if budget <= 0:
                continue
            n = min(n, budget)
            cleaned[year] = n
            scheduled_total += n
        shares_by_key[key] = cleaned
        remaining_by_key[key] = grant.shares - sum(cleaned.values())
    return GridNormalization(shares_by_key, remaining_by_key, out_of_range)
