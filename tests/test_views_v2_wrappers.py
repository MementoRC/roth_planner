"""Smoke tests for the v2 nav preview wrapper views."""

from __future__ import annotations

import inspect

from models.household import Household


class TestV2NavWrappers:
    """Verify that each v2 wrapper view is importable, callable, and correctly typed."""

    def test_this_year_imports(self):
        """views.this_year must import without error and expose render."""
        from views import this_year

        assert hasattr(this_year, "render")

    def test_this_year_render_callable(self):
        from views import this_year

        assert callable(this_year.render)

    def test_this_year_render_signature(self):
        from views import this_year

        sig = inspect.signature(this_year.render)
        params = list(sig.parameters.values())
        assert len(params) == 1, "render must take exactly one positional arg"
        assert params[0].annotation is Household or params[0].annotation == "Household"

    def test_cliffs_imports(self):
        """views.cliffs must import without error and expose render."""
        from views import cliffs

        assert hasattr(cliffs, "render")

    def test_cliffs_render_callable(self):
        from views import cliffs

        assert callable(cliffs.render)

    def test_cliffs_render_signature(self):
        from views import cliffs

        sig = inspect.signature(cliffs.render)
        params = list(sig.parameters.values())
        assert len(params) == 1, "render must take exactly one positional arg"
        assert params[0].annotation is Household or params[0].annotation == "Household"

    def test_pressure_test_imports(self):
        """views.pressure_test must import without error and expose render."""
        from views import pressure_test

        assert hasattr(pressure_test, "render")

    def test_pressure_test_render_callable(self):
        from views import pressure_test

        assert callable(pressure_test.render)

    def test_pressure_test_render_signature(self):
        from views import pressure_test

        sig = inspect.signature(pressure_test.render)
        params = list(sig.parameters.values())
        assert len(params) == 1, "render must take exactly one positional arg"
        assert params[0].annotation is Household or params[0].annotation == "Household"
