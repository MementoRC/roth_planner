"""Classic Setup shell — today's 4-tab layout (Command Center / Parameters /
Portfolio / Data bridge), unchanged (Task 8 of the ui-shell-theme-toggle
plan).

This shell is a thin wrapper only: it exists so ``views/shells/__init__.py``
has a uniform ``render(hh)`` entry point across all shells (Classic /
Domains / Hub / Contextual), even though Classic's own implementation still
lives in ``views/setup/__init__.py`` (unchanged, zero behavior difference —
see Owner decision 2 in the plan: Classic is the "zero behavior change,
default" theme).
"""

from __future__ import annotations

from models.household import Household
from views import setup


def render(hh: Household) -> None:
    """Render the Classic Setup layout — delegates unchanged to ``views.setup.render``."""
    setup.render(hh)


__all__ = ["render"]
