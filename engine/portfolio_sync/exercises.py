"""Stock option exercises + equity sales lots fetch/parse + apply."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

from models.ytd_income import YTDSnapshot

if TYPE_CHECKING:
    from models.household import Household

from .client import _flatten_query_rows, _get
from .shapes import OptionExercisesSnapshot, PortfolioSnapshot


def fetch_option_exercises() -> OptionExercisesSnapshot:
    """Fetch NQO order_detail_summary rows from FinExtract equity_compensation domain.

    Returns OptionExercisesSnapshot with server_available=False on transport error.
    404 (no batches yet) returns server_available=True with empty fields.

    Requests mode=history to aggregate ALL scrape batches. Each UBS order-detail-modal
    scrape = one batch; without mode=history only the latest batch is returned.
    Falls back to legacy _flatten_query_rows when the server does not return batches
    (older FinExtract builds that do not honour mode=history).
    """
    try:
        resp = _get(
            "/query/equity_compensation",
            params={"data_type": "order_detail_summary", "mode": "history"},
            timeout=5,
        )
        if resp.status_code == 404:
            return OptionExercisesSnapshot(server_available=True)
        if resp.status_code != 200:
            return OptionExercisesSnapshot(server_available=False, error=f"HTTP {resp.status_code}")
        data = resp.json()
        # Prefer mode=history shape: {"batches": [{batch_id, captured_at, rows: [...]}, ...]}
        batches = data.get("batches") if isinstance(data, dict) else None
        if isinstance(batches, list) and batches:
            all_rows: list[dict[str, Any]] = []
            latest_captured_at = ""
            for batch in batches:
                if not isinstance(batch, dict):
                    continue
                batch_rows = batch.get("rows") or []
                if isinstance(batch_rows, list):
                    all_rows.extend(r for r in batch_rows if isinstance(r, dict))
                ts = batch.get("captured_at") or ""
                if isinstance(ts, str) and ts > latest_captured_at:
                    latest_captured_at = ts
            return _parse_option_exercises_rows(all_rows, captured_at=latest_captured_at)
        # Fallback: legacy single-batch shape ({rows:[...]} or {institutions:{...}})
        rows = _flatten_query_rows(data)
        captured_at = ""
        if isinstance(data.get("institutions"), dict):
            for batch in data["institutions"].values():
                if isinstance(batch, dict) and batch.get("captured_at"):
                    captured_at = str(batch["captured_at"])
                    break
        return _parse_option_exercises_rows(rows, captured_at=captured_at)
    except (requests.RequestException, ValueError) as e:
        return OptionExercisesSnapshot(server_available=False, error=str(e))


def _parse_option_exercises_rows(
    rows: list[dict[str, Any]], captured_at: str = ""
) -> OptionExercisesSnapshot:
    """Aggregate per-row ordinary spread from UBS order_detail_summary rows."""
    snap = OptionExercisesSnapshot(server_available=True, captured_at=captured_at)
    for row in rows:
        try:
            grant_price = float(row.get("grant_price") or 0)
            qty = float(row.get("execution_quantity") or 0)
            gross = float(row.get("gross_proceeds") or 0)
        except (TypeError, ValueError):
            snap.warnings.append(
                f"row skipped: non-numeric fields in {row.get('grant_number', '?')}"
            )
            continue
        if qty <= 0 or grant_price <= 0:
            # Cancelled or zero-execution row; skip silently
            continue
        spread = gross - (grant_price * qty)
        if spread < 0:
            snap.warnings.append(
                f"negative spread for grant {row.get('grant_number', '?')}:"
                f" gross={gross}, strike*qty={grant_price * qty}"
            )
            continue
        snap.total_spread += spread
        snap.rows_count += 1
        grant_id = str(row.get("grant_number") or "").strip()
        if grant_id:
            snap.by_grant_id[grant_id] = snap.by_grant_id.get(grant_id, 0.0) + spread
            # Accumulate sale auxiliary info for display fallback when HH join fails
            raw_date = str(row.get("grant_date") or "")
            grant_year = int(raw_date[:4]) if len(raw_date) >= 4 and raw_date[:4].isdigit() else 0
            existing = snap.sale_info_by_grant.get(grant_id, {})
            snap.sale_info_by_grant[grant_id] = {
                "grant_year": existing.get("grant_year") or grant_year,
                "strike": existing.get("strike") or grant_price,
                "shares_ytd": existing.get("shares_ytd", 0) + int(qty),
            }
    return snap


def _parse_equity_sales_lots(
    lots: list[dict[str, Any]], captured_at: str = ""
) -> OptionExercisesSnapshot:
    """Parse equity_sales.lots from .portfolio_cache.json (FinExtract PRs #19/#20/#21).

    Each lot is one (sale, tax-lot) tuple. lots[*].execution_quantity is a
    numeric string (must cast); other numeric fields are already numbers.
    Math identical to _parse_option_exercises_rows:
    spread = gross_proceeds - (grant_price * execution_quantity).
    """
    snap = OptionExercisesSnapshot(server_available=True, captured_at=captured_at)
    for lot in lots:
        try:
            grant_price = float(lot.get("grant_price") or 0)
            qty_raw = lot.get("execution_quantity") or 0
            # Cast string numerics; tolerate int/float too; preserve full precision for spread
            qty_f = float(qty_raw) if qty_raw not in ("", None) else 0.0
            gross = float(lot.get("gross_proceeds") or 0)
        except (TypeError, ValueError):
            snap.warnings.append(
                f"lot skipped: non-numeric fields in {lot.get('grant_number', '?')}"
            )
            continue
        if qty_f <= 0 or grant_price <= 0:
            continue
        spread = gross - (grant_price * qty_f)
        if spread < 0:
            snap.warnings.append(
                f"negative spread for grant {lot.get('grant_number', '?')}: "
                f"gross={gross}, strike*qty={grant_price * qty_f}"
            )
            continue
        snap.total_spread += spread
        snap.rows_count += 1
        grant_id = str(lot.get("grant_number") or "").strip()
        if grant_id:
            snap.by_grant_id[grant_id] = snap.by_grant_id.get(grant_id, 0.0) + spread
            # Accumulate sale auxiliary info for display fallback when HH join fails
            raw_date = str(lot.get("grant_date") or "")
            grant_year = int(raw_date[:4]) if len(raw_date) >= 4 and raw_date[:4].isdigit() else 0
            existing = snap.sale_info_by_grant.get(grant_id, {})
            snap.sale_info_by_grant[grant_id] = {
                "grant_year": existing.get("grant_year") or grant_year,
                "strike": existing.get("strike") or grant_price,
                "shares_ytd": existing.get("shares_ytd", 0) + int(qty_f),
            }
    return snap


def fetch_option_exercises_with_cache(
    snapshot: PortfolioSnapshot | None = None,
) -> OptionExercisesSnapshot:
    """Read equity_sales from portfolio cache first (FinExtract PRs #19/#20/#21);
    fall back to /query?mode=history for legacy caches missing equity_sales.
    """
    # Primary path: cache-based equity_sales
    if snapshot is not None:
        lots = getattr(snapshot, "equity_sales_lots", None) or []
        if lots:
            captured_at = getattr(snapshot, "order_detail_summary_captured_at", "") or ""
            return _parse_equity_sales_lots(lots, captured_at=captured_at)
    # Fallback: legacy cache without equity_sales — hit /query
    return fetch_option_exercises()


def _normalize_grant_id(gid: str) -> str:
    """Normalize grant_id for tolerant matching: strip whitespace, uppercase, drop non-alphanumeric."""
    if not gid:
        return ""
    return "".join(ch for ch in str(gid).strip().upper() if ch.isalnum())


def _grant_id_substring_match(
    raw_norm: str, known_norm: dict[str, str]
) -> tuple[str | None, list[str]]:
    """Bidirectional substring match for grant_id prefix/suffix mismatches.

    Handles cases like UBS 'grant_number=197825' vs FinExtract
    'equity_awards.grant_id=N0000197825' (numeric core same, alpha-zero
    prefix differs). Matches the household grant_id whose normalized form
    contains (or is contained in) raw_norm, scoring candidates by the length
    of the KNOWN grant_id itself (``len(norm)``) so the genuinely longest
    household grant_id wins — NOT ``max(len(norm), len(raw_norm))``, which is
    constant across all candidates whenever raw_norm is the longer string
    (e.g. a custodian raw grant_number containing multiple known grant_ids as
    substrings) and so degenerates to picking whichever candidate is first in
    dict-iteration order (audit-0720 M7).

    Skips matches when EITHER side is shorter than 3 chars (too risky).

    Returns ``(matched_grant_id, ambiguous_grant_ids)``. ``matched_grant_id``
    is ``None`` when there is no match, OR when two-or-more DISTINCT
    household grant_ids genuinely tie for the longest match — that tie is
    real ambiguity, not a "longest match", so the caller must not silently
    attribute; ``ambiguous_grant_ids`` lists the tied candidates in that case
    so the caller can warn.
    """
    if not raw_norm or len(raw_norm) < 3:
        return None, []
    best_len = -1
    best_originals: list[str] = []
    for norm, original in known_norm.items():
        if not norm or len(norm) < 3:
            continue
        if raw_norm in norm or norm in raw_norm:
            score = len(norm)
            if score > best_len:
                best_len = score
                best_originals = [original]
            elif score == best_len and original not in best_originals:
                best_originals.append(original)
    if len(best_originals) == 1:
        return best_originals[0], []
    if len(best_originals) > 1:
        return None, best_originals
    return None, []


def apply_option_exercises(
    ytd: YTDSnapshot,
    exercises: OptionExercisesSnapshot,
    hh: Household,
) -> YTDSnapshot:
    """Merge OptionExercisesSnapshot into YTDSnapshot. Mutates in place; returns same ytd.

    Sets ytd.nqo_exercise_ytd to the total spread. Stashes per-grant-id breakdown on
    a new attribute ytd._option_exercises_by_grant (consumed by PR2 headroom subtract).
    Performs grant_id -> EquityGrant join with normalized tolerant matching; remaps
    by_grant_id keys to household grant_id format when a normalized match exists.
    Genuinely unmatched grant_ids emit a warning and retain their raw key.
    """
    if not exercises.server_available:
        return ytd
    ytd.nqo_exercise_ytd = exercises.total_spread
    if hh and hh.grants:
        # Build normalized lookup: normalized_id -> household grant_id
        known_norm: dict[str, str] = {}
        for g in hh.grants:
            gid_val = getattr(g, "grant_id", None)
            if gid_val:
                known_norm[_normalize_grant_id(str(gid_val))] = str(gid_val)
        # Remap by_grant_id keys to household format where a normalized match exists
        remapped: dict[str, float] = {}
        for raw_gid, spread in exercises.by_grant_id.items():
            if raw_gid in known_norm.values():
                # Literal match — use raw_gid as-is
                remapped[raw_gid] = remapped.get(raw_gid, 0.0) + spread
                continue
            norm = _normalize_grant_id(raw_gid)
            if norm and norm in known_norm:
                household_gid = known_norm[norm]
                remapped[household_gid] = remapped.get(household_gid, 0.0) + spread
                continue
            # Tier 3: bidirectional substring match (handles prefix/suffix mismatches)
            fallback, ambiguous = _grant_id_substring_match(norm, known_norm)
            if fallback:
                remapped[fallback] = remapped.get(fallback, 0.0) + spread
            elif ambiguous:
                # Two-or-more distinct household grant_ids genuinely tie for
                # the longest substring match — do NOT silently guess; keep
                # the raw key and warn (audit-0720 M7).
                remapped[raw_gid] = remapped.get(raw_gid, 0.0) + spread
                exercises.warnings.append(
                    f"grant_id {raw_gid} ambiguously matches multiple household "
                    f"grants {sorted(ambiguous)} (normalized: {norm}); not "
                    "attributed to any single grant"
                )
            else:
                # Genuinely unmatched — keep raw key and warn
                remapped[raw_gid] = remapped.get(raw_gid, 0.0) + spread
                exercises.warnings.append(
                    f"grant_id {raw_gid} not matched in household grants (normalized: {norm})"
                )
        exercises.by_grant_id = remapped
    # Stash for consumption by views; not dataclass fields to avoid breaking save/load
    ytd._option_exercises_by_grant = dict(exercises.by_grant_id)  # type: ignore[attr-defined]  # noqa: SLF001
    ytd._option_exercises_sale_info = dict(exercises.sale_info_by_grant)  # type: ignore[attr-defined]  # noqa: SLF001
    return ytd
