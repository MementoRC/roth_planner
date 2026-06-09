"""Pure helpers for the two-primary-planners upload cross-mapping model (PR D).

No Streamlit dependency — safe to import in tests and engine modules.
"""

from __future__ import annotations

from engine.portfolio_sync import PortfolioSnapshot


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
            "your_ss_fra": "spouse_ss_fra",
            "your_ss_start_age": "spouse_ss_start_age",
            "your_rmd_start_age": "spouse_rmd_start_age",
            "your_fra_age": "spouse_fra_age",
        }
        for file_k, sess_k in spouse_field_map.items():
            if file_k in data:
                updates[sess_k] = data[file_k]
        return updates
    scalar_keys = [
        "your_age",
        "spouse_age",
        "your_ira",
        "spouse_ira",
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
        "medicare_part_b_base_monthly",
    ]
    for k in scalar_keys:
        if k in data:
            sess_key = "txn_price" if k == "stock_price_now" else k
            updates[sess_key] = data[k]
    if "grant_strikes" in data:
        updates["_user_grant_strikes"] = data["grant_strikes"]
    if "account_type_overrides" in data:
        updates["account_type_overrides"] = data["account_type_overrides"]
    if "prior_year_magi" in data:
        # Keys arrive as strings from JSON; cast to int at load time
        updates["prior_year_magi"] = {
            int(k): float(v) for k, v in data["prior_year_magi"].items() if v
        }
    # survivor is a joint field (not spouse-specific); pass through as-is when not as_spouse
    if "survivor" in data and not as_spouse:
        updates["survivor"] = data["survivor"]
    # inherited_iras is a joint field; each entry carries its own owner field
    if "inherited_iras" in data and not as_spouse:
        updates["inherited_iras"] = data["inherited_iras"]
    return updates


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
