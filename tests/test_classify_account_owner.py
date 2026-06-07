"""Tests for Bug E: per-account owner in account_type_overrides.

Covers _resolve_override + _classify_account with the nested override form,
backwards-compat with the legacy flat form, and malformed-override fallthrough.
"""

import pytest


class TestResolveOverride:
    """Unit tests for the _resolve_override helper."""

    def test_flat_string_returns_type_and_you(self) -> None:
        from engine.portfolio_sync import _resolve_override

        assert _resolve_override("trad_ira") == ("trad_ira", "you")

    def test_nested_dict_full_returns_type_and_owner(self) -> None:
        from engine.portfolio_sync import _resolve_override

        assert _resolve_override({"type": "trad_ira", "owner": "spouse"}) == ("trad_ira", "spouse")

    def test_nested_dict_missing_owner_defaults_to_you(self) -> None:
        from engine.portfolio_sync import _resolve_override

        assert _resolve_override({"type": "roth_ira"}) == ("roth_ira", "you")

    def test_nested_dict_missing_type_returns_empty_string(self) -> None:
        from engine.portfolio_sync import _resolve_override

        acct_type, owner = _resolve_override({"owner": "spouse"})
        assert acct_type == ""
        assert owner == "spouse"


class TestClassifyAccountOwner:
    """_classify_account with the extended override schema."""

    # ------------------------------------------------------------------ #
    # Case 1: legacy flat override — backwards-compat                     #
    # ------------------------------------------------------------------ #
    def test_legacy_flat_override_returns_type_and_you(self) -> None:
        from engine.portfolio_sync import _classify_account

        result = _classify_account("U1234567", overrides={"U1234567": "trad_ira"})
        assert result == ("trad_ira", "you")

    # ------------------------------------------------------------------ #
    # Case 2: new nested override — carries owner                         #
    # ------------------------------------------------------------------ #
    def test_nested_override_returns_type_and_spouse(self) -> None:
        from engine.portfolio_sync import _classify_account

        overrides = {"U1234567": {"type": "trad_ira", "owner": "spouse"}}
        result = _classify_account("U1234567", overrides=overrides)
        assert result == ("trad_ira", "spouse")

    def test_nested_override_owner_you_explicit(self) -> None:
        from engine.portfolio_sync import _classify_account

        overrides = {"U9876543": {"type": "roth_ira", "owner": "you"}}
        result = _classify_account("U9876543", overrides=overrides)
        assert result == ("roth_ira", "you")

    # ------------------------------------------------------------------ #
    # Case 3: no override — substring scan, owner="you"                   #
    # ------------------------------------------------------------------ #
    def test_no_override_falls_through_to_substring_scan(self) -> None:
        from engine.portfolio_sync import _classify_account

        acct_type, owner = _classify_account("Rollover IRA233813501")
        assert acct_type == "trad_ira"
        assert owner == "you"

    def test_override_miss_falls_through_to_substring_scan(self) -> None:
        from engine.portfolio_sync import _classify_account

        overrides = {"U1234567": {"type": "trad_ira", "owner": "spouse"}}
        acct_type, owner = _classify_account("Rollover IRA233813501", overrides=overrides)
        assert acct_type == "trad_ira"
        assert owner == "you"

    # ------------------------------------------------------------------ #
    # Case 4: malformed nested override (no type) — falls through         #
    # ------------------------------------------------------------------ #
    def test_malformed_override_missing_type_falls_through(self) -> None:
        from engine.portfolio_sync import _classify_account

        # {"owner": "spouse"} has no "type" key → _resolve_override returns ""
        # → the `if acct_type:` guard skips it → substring scan runs
        overrides: dict[str, str | dict[str, str]] = {"U1234567": {"owner": "spouse"}}
        acct_type, owner = _classify_account("U1234567", overrides=overrides)
        # "U1234567" has no ira/roth/403b/hsa substring → brokerage
        assert acct_type == "brokerage"
        assert owner == "you"

    # ------------------------------------------------------------------ #
    # Mixed flat + nested overrides in the same dict                      #
    # ------------------------------------------------------------------ #
    def test_mixed_flat_and_nested_overrides(self) -> None:
        from engine.portfolio_sync import _classify_account

        overrides: dict[str, str | dict[str, str]] = {
            "U1111111": "trad_ira",  # legacy flat
            "U2222222": {"type": "roth_ira", "owner": "spouse"},  # nested
        }
        assert _classify_account("U1111111", overrides=overrides) == ("trad_ira", "you")
        assert _classify_account("U2222222", overrides=overrides) == ("roth_ira", "spouse")
