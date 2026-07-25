"""``views/shells/`` — alternate live-swappable Setup-domain page layouts
(Task 8+ of the ui-shell-theme-toggle plan).

Each shell's ``render(hh)`` composes the SAME five composable partials
extracted in Tasks 3-7 (``views/setup/_partials/``) — no widget ``key=``
or ``value=`` sourcing is forked between shells (Owner decisions 4/5 in
``docs/superpowers/plans/2026-07-24-ui-shell-theme-toggle.md``): only the
surrounding page layout (tabs vs. expanders vs. a future wizard/status-bar)
differs.

``THEMES`` and :func:`render_setup` are the dispatcher a future ``app.py``
theme selector (Task 10) will call. As of Task 9, all four themes —
``"Classic"``, ``"Domains"``, ``"Hub"``, and ``"Contextual"`` — are
implemented.
"""

from __future__ import annotations

from collections.abc import Callable

from models.household import Household

from . import classic_shell, contextual_shell, domains_shell, hub_shell

THEMES = ["Classic", "Domains", "Hub", "Contextual"]

_RENDERERS: dict[str, Callable[[Household], None]] = {
    "Classic": classic_shell.render,
    "Domains": domains_shell.render,
    "Hub": hub_shell.render,
    "Contextual": contextual_shell.render,
}


def render_setup(hh: Household, theme: str) -> None:
    """Render the Setup domain using the shell matching *theme*.

    *theme* must be one of :data:`THEMES`; any other value raises
    ``ValueError``.
    """
    renderer = _RENDERERS.get(theme)
    if renderer is not None:
        renderer(hh)
        return
    raise ValueError(f"Unknown UI theme: {theme!r}. Must be one of {THEMES}.")


__all__ = ["THEMES", "render_setup"]
