"""Smoke test: pytest-bdd is installed and importable."""

import pytest_bdd


def test_acceptance_pytest_bdd_loads() -> None:
    assert pytest_bdd.scenarios
