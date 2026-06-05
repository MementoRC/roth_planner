"""Smoke tests for views.setup module."""

from __future__ import annotations

import inspect

from models.household import Household


def test_setup_module_imports():
    """views.setup must import without error."""
    from views import setup

    assert hasattr(setup, "render")


def test_render_signature():
    """render must accept a Household and return None."""
    from views import setup

    sig = inspect.signature(setup.render)
    params = list(sig.parameters.values())
    assert len(params) == 1, "render must take exactly one positional arg"
    assert params[0].annotation is Household or params[0].annotation == "Household"


def test_render_is_callable():
    from views import setup

    assert callable(setup.render)
