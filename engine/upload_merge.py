"""Pure helpers for the two-primary-planners upload cross-mapping model (PR D).

No Streamlit dependency — safe to import in tests and engine modules.
"""

from __future__ import annotations

from engine.portfolio_sync import PortfolioSnapshot
from engine.portfolio_sync.classify import _resolve_override

ALLOWED_ACCOUNT_TYPES = frozenset({"trad_ira", "roth_ira", "brokerage", "hsa", "403b"})

# Canonical list of persisted scalar setup fields (.user_defaults.json <->
# session_state). This is the single source of truth for "which scalar
# fields does the app persist" — consumed by build_user_defaults_session_updates
# below, by views/setup/_state.py's _user_defaults_from_session (export), and
# by app.py's _seed_session_state (import on session start) so all three stay
# in sync. See audit 2026-07-13: app.py used to hand-list only ~10 of these,
# hardcoding the rest (e.g. filing_status, growth_rate) and silently
# discarding persisted values on every fresh session.
SCALAR_KEYS: list[str] = [
    "your_age",
    "spouse_age",
    "your_ira",
    "spouse_ira",
    "your_roth",
    "spouse_roth",
    "your_ss_fra",
    "spouse_ss_fra",
    "your_ss_start_age",
    "spouse_ss_start_age",
    "your_rmd_start_age",
    "spouse_rmd_start_age",
    "your_fra_age",
    "spouse_fra_age",
    "living_expenses",
    "stock_price_now",
    "aca_benchmark_premium_annual",
    "aca_enhanced_subsidies_active",
    "advance_aptc_annual",
    "medicare_part_b_base_monthly",
    "cpi_assumption",
    "filing_status",
    "your_aca",
    "spouse_aca",
    "your_defer_first_rmd",
    "spouse_defer_first_rmd",
    "growth_rate",
    "txn_price_growth_rate",
    "your_has_workplace_plan",
    "spouse_has_workplace_plan",
    "spouse_is_sole_beneficiary",
]


def build_user_defaults_session_updates(data: dict, *, as_spouse: bool) -> dict:
    """Compute session_state updates from a .user_defaults.json payload.

    Pure function — returns a ``{session_key: value}`` dict without writing to
    state. Used by ``_apply_user_defaults_to_session`` in app.py and exercised
    directly in tests.

    When ``as_spouse=False`` (default), the file represents the receiver's own
    data — all known scalar keys and ``grant_strikes`` are passed through.
    When ``as_spouse=True``, the file represents the spouse's data (their
    planner export from their own perspective); only ``your_age``/``your_ira``/
    ``your_ss_fra`` are extracted and cross-mapped to the receiver's
    ``spouse_age``/``spouse_ira``/``spouse_ss_fra`` slots. The spouse's view of
    the receiver, joint fields, and grant data are deliberately discarded
    — the receiver's own data is authoritative for those slots, and only the
    receiver has TXN NQO grants in this household model.
    """
    updates: dict = {}
    if as_spouse:
        spouse_field_map = {
            "your_age": "spouse_age",
            "your_ira": "spouse_ira",
            "your_roth": "spouse_roth",
            "your_ss_fra": "spouse_ss_fra",
            "your_ss_start_age": "spouse_ss_start_age",
            "your_rmd_start_age": "spouse_rmd_start_age",
            "your_fra_age": "spouse_fra_age",
            "your_aca": "spouse_aca",
            "your_defer_first_rmd": "spouse_defer_first_rmd",
            "your_has_workplace_plan": "spouse_has_workplace_plan",
        }
        for file_k, sess_k in spouse_field_map.items():
            if file_k in data:
                updates[sess_k] = data[file_k]
        return updates
    for k in SCALAR_KEYS:
        if k in data:
            sess_key = "txn_price" if k == "stock_price_now" else k
            updates[sess_key] = data[k]
    if "grant_strikes" in data:
        updates["_user_grant_strikes"] = data["grant_strikes"]
    if "account_type_overrides" in data and isinstance(data["account_type_overrides"], dict):
        valid: dict = {}
        for acct, entry in data["account_type_overrides"].items():
            acct_type, _owner = _resolve_override(entry)
            if acct_type in ALLOWED_ACCOUNT_TYPES:
                valid[acct] = entry
        updates["account_type_overrides"] = valid
    # prior_year_magi is deliberately NOT included here: bundle MAGI is
    # routed through CandidateStore candidates (Source.BUNDLE) via
    # extract_bundle_magi() + record_magi_candidates(), called by the Data
    # Bridge view — never a full session_state["prior_year_magi"] replace
    # (audit defect #2, "contradictory MAGI policy").
    # survivor is a joint field (not spouse-specific); pass through as-is when not as_spouse
    if "survivor" in data and not as_spouse:
        updates["survivor"] = data["survivor"]
        # Reflect an uploaded survivor scenario in the Enable checkbox so the setup
        # render keeps it instead of re-nulling it (audit C9 / ui-streamlit-5).
        updates["_survivor_enabled"] = bool(data["survivor"])
    # inherited_iras is a joint field; each entry carries its own owner field
    if "inherited_iras" in data and not as_spouse:
        updates["inherited_iras"] = data["inherited_iras"]
    return updates


def extract_bundle_magi(data: dict) -> dict[int, float]:
    """Return a Data Bridge bundle's ``prior_year_magi`` dict as ``{int: float}``.

    Pure extraction only — does not write to ``Household`` or session state.
    Keys arrive as strings from JSON; ``None``/empty-string values are
    dropped, but a genuine ``0.0`` MAGI year is kept (audit UU5-UI-04).
    Callers (the Data Bridge view) route the result through
    ``engine.data_sources.record.record_magi_candidates`` as ``Source.BUNDLE``
    candidates for Command Center review, instead of the old full
    session_state replace (audit defect #2).
    """
    raw = data.get("prior_year_magi")
    if not raw:
        return {}
    return {int(k): float(v) for k, v in raw.items() if v is not None and v != ""}


def derive_ira_balances(snap: PortfolioSnapshot) -> tuple[float, float]:
    """Return (your_pretax_total, spouse_pretax_total) from a snapshot, filtered by owner.

    Both values are floats summed across pretax accounts. Used by app.py to
    populate Household.your_ira / Household.spouse_ira without the
    owner-blind double-count that snap.pretax_total would cause when the
    snapshot contains spouse-owned accounts from a cross-mapped upload (PR #39).
    """
    your_total = sum(a.total_value for a in snap.accounts if a.owner == "you" and a.is_pretax)
    spouse_total = sum(a.total_value for a in snap.accounts if a.owner == "spouse" and a.is_pretax)
    return float(your_total), float(spouse_total)


def derive_roth_balances(snap: PortfolioSnapshot) -> tuple[float, float]:
    """Return (your_roth_total, spouse_roth_total) from a snapshot, filtered by owner.

    Both values are floats summed across Roth IRA accounts. Mirrors
    derive_ira_balances but filters a.is_roth instead of a.is_pretax.
    Used by app.py and views/setup/portfolio.py to populate
    Household.your_roth / Household.spouse_roth.
    """
    your_total = sum(a.total_value for a in snap.accounts if a.owner == "you" and a.is_roth)
    spouse_total = sum(a.total_value for a in snap.accounts if a.owner == "spouse" and a.is_roth)
    return float(your_total), float(spouse_total)
