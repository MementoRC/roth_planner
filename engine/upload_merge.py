"""Pure helpers for the two-primary-planners upload cross-mapping model (PR D).

No Streamlit dependency — safe to import in tests and engine modules.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from config.scalar_registry import joint_keys as _joint_keys
from config.scalar_registry import scalar_default_by_config_key as _scalar_default
from config.scalar_registry import scalar_keys as _scalar_keys
from config.scalar_registry import session_key_for_config_key as _session_key_for
from engine.portfolio_sync import PortfolioSnapshot
from engine.portfolio_sync.classify import _resolve_override
from models.legacy_keys import migrate_legacy_keys

ALLOWED_ACCOUNT_TYPES = frozenset({"trad_ira", "roth_ira", "brokerage", "hsa", "403b"})

# Canonical list of persisted scalar setup fields (.user_defaults.json <->
# session_state). This is the single source of truth for "which scalar
# fields does the app persist" — consumed by build_user_defaults_session_updates
# below, by views/setup/_state.py's _user_defaults_from_session (export), and
# by app.py's _seed_session_state (import on session start) so all three stay
# in sync. See audit 2026-07-13: app.py used to hand-list only ~10 of these,
# hardcoding the rest (e.g. filing_status, growth_rate) and silently
# discarding persisted values on every fresh session.
#
# Derived from config.scalar_registry.SCALAR_REGISTRY (issue #498) — that
# module is now the single source of truth for defaults + shape; this is a
# projection of it, in registry order, pinned against the pre-#498 literal
# list in tests/test_scalar_registry.py::TestPreRefactorLiteralPins.
SCALAR_KEYS: list[str] = _scalar_keys()

# Household-wide scalars: shared values that cannot legitimately differ between
# the two people in this model. Everything else in SCALAR_KEYS is per-person —
# either a `your_*` field (cross-mapped below) or a `spouse_*` field (the
# sender's view of the RECEIVER, deliberately dropped).
#
# These are the fields the as_spouse cross-map used to discard outright, which
# left a receiver who had never set them computing on config/defaults.py's
# synthetic Acme demo values: filing_status moves every bracket,
# living_expenses drives the withdrawal waterfall, and the ACA/Medicare pair
# drives subsidy and surcharge math.
#
# Derived from config.scalar_registry (issue #498); see SCALAR_KEYS above.
JOINT_FIELDS: frozenset[str] = _joint_keys()

# Joint keys carried outside the scalar registry, with their "never set" value.
_EXTRA_JOINT_UNSET: dict[str, object] = {"survivor": None, "inherited_iras": []}


def sanitize_living_expenses(value: Any) -> Any:
    """A usable annual-living-expenses figure from an untrusted source.

    Negative numbers clamp to 0 (the Setup widget's floor); NaN and infinity
    fall back to the registry default. ``json.loads`` accepts bare
    ``NaN``/``Infinity``, so an uploaded or hand-edited settings file can
    otherwise seed them straight into session state (audit-1018 F-33
    follow-up). Non-numeric values are returned untouched.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    if not math.isfinite(value):
        return _scalar_default("living_expenses")
    return max(value, 0)


def _sanitized(updates: dict) -> dict:
    """Run the numeric sanitizers over a session-updates dict (in place)."""
    key = _session_key_for("living_expenses")
    if key in updates:
        updates[key] = sanitize_living_expenses(updates[key])
    return updates


def receiver_joint_value_is_set(key: str, persisted: Mapping[str, object]) -> bool:
    """True when the receiver deliberately set joint field *key*.

    A key absent from ``.user_defaults.json`` was never set. A key that holds
    the app's own demo default does not count as set either (audit-1008 F-04):
    the Backup page and every Setup autosave write EVERY seeded scalar to that
    file, so mere presence said nothing -- one visit to Backup & Transfer made
    every import keep the demo filing status, expenses, survivor scenario and
    inherited IRAs as "your own values". The cost is that a receiver who
    deliberately chose a value equal to the default takes the sender's value
    for that shared field; the import preview lists it before Apply.
    """
    if key not in persisted:
        return False
    value = persisted[key]
    if key in _EXTRA_JOINT_UNSET:
        unset = _EXTRA_JOINT_UNSET[key]
        return not (value == unset or value is None)
    try:
        default = _scalar_default(key)
    except KeyError:
        return True
    return value != default


def build_user_defaults_session_updates(
    data: dict,
    *,
    as_spouse: bool,
    receiver_persisted: Mapping[str, object] | None = None,
) -> dict:
    """Compute session_state updates from a .user_defaults.json payload.

    Pure function — returns a ``{session_key: value}`` dict without writing to
    state. Used by ``_apply_user_defaults_to_session`` in app.py and exercised
    directly in tests.

    When ``as_spouse=False`` (default), the file represents the receiver's own
    data — all known scalar keys and ``grant_strikes`` are passed through.
    When ``as_spouse=True``, the file represents the spouse's data (their
    planner export from their own perspective); the ten ``your_*`` fields are
    cross-mapped to the receiver's ``spouse_*`` slots. The spouse's view of the
    receiver (their ``spouse_*`` keys) and their grant data stay discarded —
    the receiver's own data is authoritative there, and only the receiver has
    employer NQO grants in this household model.

    ``receiver_persisted`` is the receiver's raw ``.user_defaults.json``
    (:func:`config.loader.load_raw_user_defaults`) and unlocks the JOINT_FIELDS
    rule. Those are shared household values, and dropping them wholesale left a
    receiver who had never set them computing on demo defaults while believing
    they had imported real data. For each joint field the bundle carries:

    * not set by the receiver (see :func:`receiver_joint_value_is_set`: absent
      from the persisted file, or still the demo default) -> take the sender's
      value;
    * set and different -> keep the receiver's deliberate choice and append
      to the ``_bundle_joint_conflicts`` list so the UI can surface it;
    * set and equal -> nothing to do.

    The PERSISTED FILE is the test, not session state (``app.py``'s
    ``_seed_session_state`` fills every scalar before any import runs, so
    nothing is ever absent there).

    Omitting ``receiver_persisted`` keeps the historical behaviour of dropping
    every joint field, so callers that have not been updated are unaffected.

    Pre-rename key names in either payload (an old settings file or v4
    bundle) are read under their current names.
    """
    data = migrate_legacy_keys(data)
    if receiver_persisted is not None:
        receiver_persisted = migrate_legacy_keys(receiver_persisted)
    updates: dict = {}
    if as_spouse:
        spouse_field_map = {
            "your_age": "spouse_age",
            "your_ira": "spouse_ira",
            "your_roth": "spouse_roth",
            "your_ss_fra": "spouse_ss_fra",
            "your_ss_start_age": "spouse_ss_start_age",
            "your_rmd_start_age": "spouse_rmd_start_age",
            "your_rmd_age_chosen": "spouse_rmd_age_chosen",
            "your_fra_age": "spouse_fra_age",
            "your_aca": "spouse_aca",
            "your_defer_first_rmd": "spouse_defer_first_rmd",
            "your_has_workplace_plan": "spouse_has_workplace_plan",
        }
        for file_k, sess_k in spouse_field_map.items():
            if file_k in data:
                updates[sess_k] = data[file_k]
        if receiver_persisted is not None:
            conflicts: list[dict] = []
            for file_k in sorted(JOINT_FIELDS):
                if file_k not in data:
                    continue
                sess_k = _session_key_for(file_k)
                if not receiver_joint_value_is_set(file_k, receiver_persisted):
                    # Never set (absent, or still the demo default) => the only
                    # real value in the household is the sender's.
                    updates[sess_k] = data[file_k]
                elif receiver_persisted[file_k] != data[file_k]:
                    # Persisted => a deliberate choice. Keep it, but say so: a
                    # silent divergence on a field that is shared by definition
                    # is how both sides end up confidently wrong.
                    conflicts.append(
                        {
                            "field": file_k,
                            "mine": receiver_persisted[file_k],
                            "theirs": data[file_k],
                        }
                    )
            if conflicts:
                updates["_bundle_joint_conflicts"] = conflicts
        return _sanitized(updates)
    for k in SCALAR_KEYS:
        if k in data:
            updates[_session_key_for(k)] = data[k]
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
    return _sanitized(updates)


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


def synced_account_sums(snap: PortfolioSnapshot, owner: str, kind: str) -> dict[str, float]:
    """Return {account_type: summed total_value} for the snapshot accounts that
    feed one balance field: *owner* ('you'/'spouse'), *kind* 'pretax'
    (trad_ira + 403b) or 'roth' (roth_ira). Empty when none feed the field.
    """
    wanted = {"pretax": ("trad_ira", "403b"), "roth": ("roth_ira",)}[kind]
    return {
        t: float(
            sum(a.total_value for a in snap.accounts if a.owner == owner and a.account_type == t)
        )
        for t in wanted
        if any(a.owner == owner and a.account_type == t for a in snap.accounts)
    }


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
