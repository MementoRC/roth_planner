"""Single source of truth for persisted scalar setup-field defaults.

Issue #498: scalar defaults were scattered across three places that had to
be kept in sync by hand — ``engine.upload_merge.SCALAR_KEYS`` /
``JOINT_FIELDS`` (the export/import contract), ``config.defaults.DEFAULTS``
(13 of the 31 pre-#498 scalars), and ~18 hardcoded literal ``setdefault`` calls in
``app.py::_seed_session_state`` plus assorted ``.get(key, literal)``
fallbacks in ``app.py`` and the Setup views. This module is the ONE place
those defaults are declared; everything else derives from or is routed
through it.

Streamlit-free (safe to import from ``config``, ``engine``, and views).
``engine.irmaa.BASE_PART_B`` is a leaf import — ``engine.irmaa`` only
imports ``engine.tax_indexing``, neither of which imports back into
``config`` — so importing it here does not create a cycle.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Literal

from engine.irmaa import BASE_PART_B
from models.legacy_keys import current_key


@dataclass(frozen=True)
class ScalarSpec:
    """One persisted scalar setup field's canonical default + shape.

    ``session_key`` is the ``st.session_state`` slot actually read/written.
    ``config_key`` is the ``.user_defaults.json`` / ``config.defaults.DEFAULTS``
    / ``SCALAR_KEYS`` name when it differs from ``session_key`` — today only
    ``stock_price_now`` (config) <-> ``stock_price`` (session) and
    ``stock_ticker`` <-> ``_stock_ticker``. Defaults to
    ``session_key`` when unset (see :func:`session_key_for_config_key`).

    ``scope`` is ``"joint"`` for household-wide values that cannot
    legitimately differ between the two people in this model (mirrors
    ``engine.upload_merge.JOINT_FIELDS``), else ``"per_person"``.

    ``in_scalar_keys`` is False only for ``prior_year_magi``: it is seeded
    alongside the other scalars in ``app.py`` but is deliberately NOT part
    of the persisted-scalar export/import contract (``SCALAR_KEYS``) — it is
    governed via ``Source.PDF``/``BUNDLE`` candidates instead.
    """

    session_key: str
    default: object
    scope: Literal["joint", "per_person"]
    config_key: str | None = None
    in_scalar_keys: bool = True
    note: str = ""


SCALAR_REGISTRY: tuple[ScalarSpec, ...] = (
    ScalarSpec("your_age", 55, "per_person"),
    ScalarSpec("spouse_age", 53, "per_person"),
    ScalarSpec("your_ira", 500_000, "per_person"),
    ScalarSpec("spouse_ira", 500_000, "per_person"),
    ScalarSpec("your_roth", 0, "per_person"),
    ScalarSpec("spouse_roth", 0, "per_person"),
    ScalarSpec("your_ss_fra", 2_500, "per_person"),
    ScalarSpec("spouse_ss_fra", 2_500, "per_person"),
    ScalarSpec("your_ss_start_age", 70, "per_person"),
    ScalarSpec("spouse_ss_start_age", 70, "per_person"),
    ScalarSpec("your_rmd_start_age", 75, "per_person"),
    ScalarSpec("spouse_rmd_start_age", 75, "per_person"),
    ScalarSpec("your_fra_age", 67, "per_person"),
    ScalarSpec("spouse_fra_age", 67, "per_person"),
    ScalarSpec("living_expenses", 60_000, "joint"),
    ScalarSpec(
        "net_inv_income",
        0,
        "joint",
        note="Manual NII add-on ($/yr) on top of modeled dividends/gains; edited only in Setup › Assumptions.",
    ),
    ScalarSpec(
        "stock_price",
        100,
        "joint",
        config_key="stock_price_now",
        note="Session key 'stock_price'; config/SCALAR_KEYS name 'stock_price_now'.",
    ),
    ScalarSpec(
        "aca_benchmark_premium_annual",
        None,
        "joint",
        note=(
            "None means 'derive' (national-average SLCSP via "
            "engine.aca.derive_couple_benchmark_annual); an explicit float "
            "(including 0.0) is a household override used verbatim."
        ),
    ),
    ScalarSpec("aca_enhanced_subsidies_active", False, "joint"),
    ScalarSpec("advance_aptc_annual", 0, "joint"),
    ScalarSpec(
        "medicare_part_b_base_monthly",
        BASE_PART_B / 12,
        "joint",
        note="Annual BASE_PART_B (engine.irmaa) / 12.",
    ),
    ScalarSpec("cpi_assumption", 0.025, "joint"),
    ScalarSpec("filing_status", "MFJ", "joint"),
    ScalarSpec("your_aca", False, "per_person"),
    ScalarSpec("spouse_aca", False, "per_person"),
    ScalarSpec("your_defer_first_rmd", False, "per_person"),
    ScalarSpec("spouse_defer_first_rmd", False, "per_person"),
    ScalarSpec(
        "growth_rate",
        7.0,
        "joint",
        note="Percent (7.0 == 7%); models.household.Household.growth_rate stores the fraction 0.07.",
    ),
    ScalarSpec(
        "stock_price_growth_rate",
        7.0,
        "joint",
        note="Percent, same convention as growth_rate.",
    ),
    ScalarSpec("your_has_workplace_plan", True, "per_person"),
    ScalarSpec("spouse_has_workplace_plan", False, "per_person"),
    ScalarSpec("spouse_is_sole_beneficiary", False, "joint"),
    ScalarSpec(
        "terminal_ira_tax_rate",
        24.0,
        "joint",
        note=(
            "Percent (24.0 == 24%), clamped to TERMINAL_IRA_TAX_RATE_BOUNDS. The "
            "exercise optimizer's tax rate on IRA money left at the end of the "
            "projection (audit-1008 F-07); the engine takes the fraction "
            "(engine.exercise_optimizer.DEFAULT_TERMINAL_IRA_TAX_RATE)."
        ),
    ),
    ScalarSpec(
        "_stock_ticker",
        "ACME",
        "joint",
        config_key="stock_ticker",
        note=(
            "Employer ticker (audit-1018 F-34): session key '_stock_ticker', "
            "config/SCALAR_KEYS name 'stock_ticker'. Travels in the export bundle "
            "so a receiver classifies the sender's employer stock as qualified equity."
        ),
    ),
    ScalarSpec(
        "prior_year_magi",
        {},
        "joint",
        in_scalar_keys=False,
        note="Not part of SCALAR_KEYS; governed via Source.PDF/BUNDLE candidates.",
    ),
)

# Allowed range, in percent, for terminal_ira_tax_rate. Shared by the Assumptions
# input, the optimizer panels and optimizer_settings() so an imported or
# hand-edited value outside it can never reach a widget or the engine.
TERMINAL_IRA_TAX_RATE_BOUNDS: tuple[float, float] = (0.0, 50.0)

_BY_SESSION_KEY: dict[str, ScalarSpec] = {spec.session_key: spec for spec in SCALAR_REGISTRY}
_BY_CONFIG_KEY: dict[str, ScalarSpec] = {
    (spec.config_key or spec.session_key): spec for spec in SCALAR_REGISTRY if spec.in_scalar_keys
}


def scalar_default(session_key: str) -> object:
    """Return a fresh copy of *session_key*'s canonical default.

    ``copy.deepcopy`` guards the one mutable default (``prior_year_magi``'s
    ``{}``) so sessions never share a mutable object; it is a no-op for the
    scalar (immutable) defaults. A pre-rename key name
    (:data:`models.legacy_keys.LEGACY_KEY_DIGESTS`) resolves to its current one.
    """
    return copy.deepcopy(_BY_SESSION_KEY[current_key(session_key)].default)


def scalar_default_by_config_key(config_key: str) -> object:
    """Same as :func:`scalar_default`, keyed by the SCALAR_KEYS/config name."""
    return copy.deepcopy(_BY_CONFIG_KEY[current_key(config_key)].default)


def session_key_for_config_key(config_key: str) -> str:
    """The ``st.session_state`` slot for a SCALAR_KEYS/config name.

    ``stock_price_now`` -> ``stock_price`` and ``stock_ticker`` -> ``_stock_ticker``;
    every other scalar is 1:1.
    """
    spec = _BY_CONFIG_KEY.get(current_key(config_key))
    return spec.session_key if spec is not None else config_key


def scalar_keys() -> list[str]:
    """``SCALAR_KEYS`` projection: config-key names, in registry order."""
    return [spec.config_key or spec.session_key for spec in SCALAR_REGISTRY if spec.in_scalar_keys]


def joint_keys() -> frozenset[str]:
    """``JOINT_FIELDS`` projection: config-key names of joint-scope scalars."""
    return frozenset(
        (spec.config_key or spec.session_key)
        for spec in SCALAR_REGISTRY
        if spec.in_scalar_keys and spec.scope == "joint"
    )
