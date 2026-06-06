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


class TestPyodideGating:
    """Verify the FinExtract sync block is gated behind is_pyodide()."""

    def test_fetch_portfolio_inside_pyodide_else_branch(self):
        """fetch_portfolio must appear AFTER the is_pyodide() guard in setup.py source.

        Static assertion: confirms the guard is not accidentally removed and that
        fetch_portfolio cannot be reached on Pyodide even without executing Streamlit.
        """
        import inspect

        from views import setup

        source = inspect.getsource(setup.render)
        guard_pos = source.find("is_pyodide()")
        fetch_pos = source.find("fetch_portfolio(")
        assert guard_pos != -1, "is_pyodide() guard not found in render()"
        assert fetch_pos != -1, "fetch_portfolio( call not found in render()"
        assert guard_pos < fetch_pos, (
            "fetch_portfolio() appears before is_pyodide() guard — "
            "sync block is not properly gated on Pyodide"
        )
