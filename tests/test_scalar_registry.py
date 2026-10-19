"""Tests for config.scalar_registry (issue #498).

Pins the registry's role as the single source of truth for persisted-scalar
defaults (31 pre-#498 scalars + post-#498 additions): every
SCALAR_KEYS/JOINT_FIELDS entry has (or projects to) a spec,
DEFAULTS' overlapping values match the registry, mutable defaults never
leak between sessions, and the Medicare default matches engine.irmaa.
"""

from __future__ import annotations

import copy

from config.defaults import DEFAULTS
from config.scalar_registry import (
    SCALAR_REGISTRY,
    joint_keys,
    scalar_default,
    scalar_default_by_config_key,
    scalar_keys,
)
from engine.irmaa import BASE_PART_B
from engine.upload_merge import JOINT_FIELDS, SCALAR_KEYS


class TestRegistryProjections:
    def test_scalar_keys_matches_upload_merge(self) -> None:
        assert scalar_keys() == SCALAR_KEYS

    def test_joint_keys_matches_upload_merge(self) -> None:
        assert joint_keys() == JOINT_FIELDS

    def test_every_scalar_key_has_a_spec(self) -> None:
        registry_config_keys = {
            (spec.config_key or spec.session_key) for spec in SCALAR_REGISTRY if spec.in_scalar_keys
        }
        assert set(SCALAR_KEYS) <= registry_config_keys

    def test_joint_fields_is_subset_of_scalar_keys(self) -> None:
        assert set(SCALAR_KEYS) >= JOINT_FIELDS


class TestDefaultsSourcedFromRegistry:
    """The 13 SCALAR_KEYS entries also present in DEFAULTS must come from
    exactly one place — the registry — with unchanged values."""

    _OVERLAPPING = [
        "your_age",
        "spouse_age",
        "your_has_workplace_plan",
        "spouse_has_workplace_plan",
        "spouse_is_sole_beneficiary",
        "your_ira",
        "spouse_ira",
        "your_roth",
        "spouse_roth",
        "your_ss_fra",
        "spouse_ss_fra",
        "living_expenses",
        "stock_price_now",
        "stock_ticker",
    ]

    def test_defaults_values_match_registry(self) -> None:
        for key in self._OVERLAPPING:
            assert DEFAULTS[key] == scalar_default_by_config_key(key), key

    def test_ten_joint_fields_not_in_defaults(self) -> None:
        """The 10 JOINT_FIELDS scalars not already in DEFAULTS (8 pre-#498 plus
        post-#498 net_inv_income and terminal_ira_tax_rate) must stay
        registry-only -- adding them to DEFAULTS was explicitly out of scope."""
        not_in_defaults = JOINT_FIELDS - set(self._OVERLAPPING)
        assert len(not_in_defaults) == 10
        assert not_in_defaults.isdisjoint(DEFAULTS)


class TestScalarDefault:
    def test_medicare_default_matches_base_part_b(self) -> None:
        assert scalar_default("medicare_part_b_base_monthly") == BASE_PART_B / 12

    def test_stock_price_now_alias(self) -> None:
        """session_key 'stock_price' <-> config_key 'stock_price_now'."""
        assert scalar_default("stock_price") == scalar_default_by_config_key("stock_price_now")

    def test_prior_year_magi_default_is_fresh_copy_each_call(self) -> None:
        first = scalar_default("prior_year_magi")
        second = scalar_default("prior_year_magi")
        assert first == {} == second
        assert first is not second
        first["2024"] = 100_000.0
        assert second == {}, "mutating one call's default must not leak into another"

    def test_prior_year_magi_default_matches_registry_spec(self) -> None:
        spec = next(s for s in SCALAR_REGISTRY if s.session_key == "prior_year_magi")
        assert spec.default == {}
        assert spec.in_scalar_keys is False

    def test_aca_benchmark_default_is_none(self) -> None:
        """None means 'derive' -- not a hardcoded flat figure."""
        assert scalar_default("aca_benchmark_premium_annual") is None

    def test_growth_rate_defaults_are_percent_not_fraction(self) -> None:
        assert scalar_default("growth_rate") == 7.0
        assert scalar_default("stock_price_growth_rate") == 7.0


class TestRegistryDeepcopyDoesNotAffectImmutables:
    """Ensure copy.deepcopy on immutable scalar defaults is a harmless no-op."""

    def test_deepcopy_of_int_default_is_equal(self) -> None:
        assert copy.deepcopy(scalar_default("your_age")) == scalar_default("your_age")


# --- Pre-refactor literal pins (review follow-up) --------------------------
# Copied verbatim from `git show development:engine/upload_merge.py`,
# `git show development:config/defaults.py`, and `git show
# development:app.py` -- deliberately NOT derived from
# config.scalar_registry. TestRegistryProjections/TestDefaultsSourcedFromRegistry
# above compare the registry against itself (scalar_keys() vs SCALAR_KEYS,
# both now sourced from the same registry) and would pass even if a registry
# entry were reordered, dropped, re-scoped, or re-valued. These pin against
# the actual pre-#498 values instead.

_PRE_REFACTOR_SCALAR_KEYS = [
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
    "stock_price_growth_rate",
    "your_has_workplace_plan",
    "spouse_has_workplace_plan",
    "spouse_is_sole_beneficiary",
]

# Scalars added AFTER #498 (joint scope, session key == config key). Recorded
# explicitly so the pre-refactor literals below stay untouched.
_POST_498_ADDITIONS = ["net_inv_income", "terminal_ira_tax_rate", "stock_ticker"]
# Their session keys (stock_ticker is held under "_stock_ticker").
_POST_498_SESSION_KEYS = {"net_inv_income", "terminal_ira_tax_rate", "_stock_ticker"}

_PRE_REFACTOR_JOINT_FIELDS = frozenset(
    {
        "filing_status",
        "living_expenses",
        "cpi_assumption",
        "growth_rate",
        "stock_price_now",
        "stock_price_growth_rate",
        "aca_benchmark_premium_annual",
        "aca_enhanced_subsidies_active",
        "advance_aptc_annual",
        "medicare_part_b_base_monthly",
        "spouse_is_sole_beneficiary",
    }
)

# {session_key: expected_default} for all 32 registry entries (session_key
# is the st.session_state slot -- "stock_price", not "stock_price_now"). The
# 13 DEFAULTS-sourced values plus the 18 old SCALAR_KEYS app.py literals plus
# prior_year_magi (seeded in app.py but outside SCALAR_KEYS).
_PRE_REFACTOR_SCALAR_DEFAULTS: dict[str, object] = {
    "your_age": 55,
    "spouse_age": 53,
    "your_ira": 500_000,
    "spouse_ira": 500_000,
    "your_roth": 0,
    "spouse_roth": 0,
    "your_ss_fra": 2_500,
    "spouse_ss_fra": 2_500,
    "your_ss_start_age": 70,
    "spouse_ss_start_age": 70,
    "your_rmd_start_age": 75,
    "spouse_rmd_start_age": 75,
    "your_fra_age": 67,
    "spouse_fra_age": 67,
    "living_expenses": 60_000,
    "stock_price": 100,
    "aca_benchmark_premium_annual": None,
    "aca_enhanced_subsidies_active": False,
    "advance_aptc_annual": 0,
    "medicare_part_b_base_monthly": BASE_PART_B / 12,
    "cpi_assumption": 0.025,
    "filing_status": "MFJ",
    "your_aca": False,
    "spouse_aca": False,
    "your_defer_first_rmd": False,
    "spouse_defer_first_rmd": False,
    "growth_rate": 7.0,
    "stock_price_growth_rate": 7.0,
    "your_has_workplace_plan": True,
    "spouse_has_workplace_plan": False,
    "spouse_is_sole_beneficiary": False,
    "prior_year_magi": {},
}


class TestPreRefactorLiteralPins:
    """Pins independent of config.scalar_registry -- a reordered, dropped,
    re-scoped, or re-valued registry entry must fail these."""

    def test_scalar_keys_matches_pre_refactor_literal_order(self) -> None:
        assert [k for k in SCALAR_KEYS if k not in _POST_498_ADDITIONS] == _PRE_REFACTOR_SCALAR_KEYS
        assert set(SCALAR_KEYS) == set(_PRE_REFACTOR_SCALAR_KEYS) | set(_POST_498_ADDITIONS)

    def test_joint_fields_matches_pre_refactor_literal(self) -> None:
        assert _PRE_REFACTOR_JOINT_FIELDS | frozenset(_POST_498_ADDITIONS) == JOINT_FIELDS

    def test_registry_has_exactly_35_session_keys(self) -> None:
        # 32 pre-#498 session keys plus the post-#498 additions.
        assert {spec.session_key for spec in SCALAR_REGISTRY} == set(
            _PRE_REFACTOR_SCALAR_DEFAULTS
        ) | _POST_498_SESSION_KEYS
        assert len(SCALAR_REGISTRY) == 35

    def test_every_scalar_default_matches_pre_refactor_literal(self) -> None:
        for session_key, expected in _PRE_REFACTOR_SCALAR_DEFAULTS.items():
            actual = scalar_default(session_key)
            assert actual == expected, session_key
            assert type(actual) is type(expected), (
                f"{session_key}: expected type {type(expected)}, got {type(actual)}"
            )


class TestDefaultsLiteralPin:
    """DEFAULTS' 13 overlapping values pinned independent of the registry."""

    def test_defaults_values_pinned_to_pre_refactor_literals(self) -> None:
        expected: dict[str, object] = {
            "your_age": 55,
            "spouse_age": 53,
            "your_has_workplace_plan": True,
            "spouse_has_workplace_plan": False,
            "spouse_is_sole_beneficiary": False,
            "your_ira": 500_000,
            "spouse_ira": 500_000,
            "your_roth": 0,
            "spouse_roth": 0,
            "your_ss_fra": 2_500,
            "spouse_ss_fra": 2_500,
            "living_expenses": 60_000,
            "stock_price_now": 100,
        }
        for key, value in expected.items():
            assert DEFAULTS[key] == value, key
            assert type(DEFAULTS[key]) is type(value), key
