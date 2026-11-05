"""Acceptance-spec fixtures. Feature files resolve against bdd_features_base_dir (pyproject)."""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/acceptance/" in item.nodeid:
            item.add_marker(pytest.mark.acceptance)
