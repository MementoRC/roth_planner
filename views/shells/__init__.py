"""``views/shells/`` — the Setup-domain page layout.

Domains is the only surviving shell (Classic/Hub/Contextual/Wizard were
retired when the UI shell was collapsed to Domains-only). ``render_setup``
is the single entry point ``app.py`` calls for the Setup page.
"""

from __future__ import annotations

from models.household import Household

from . import domains_shell


def render_setup(hh: Household) -> None:
    """Render the Setup domain via the Domains shell."""
    domains_shell.render(hh)


__all__ = ["render_setup"]
