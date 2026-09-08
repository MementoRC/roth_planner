"""Shared numeric clamp helper for Setup-domain widget partials.

Extracted from ``views/setup/_partials/_assumptions.py`` (audit-0823 M3):
the original ``_clamp`` lived only in that module and was applied at its 9
widget sites, but 12 sibling sites in ``_accounts.py``, ``_household.py``
and ``_options.py`` never had access to it and were never protected —
this module is the fix, giving all four partials one shared implementation.
"""

from __future__ import annotations

from typing import TypeVar

_Num = TypeVar("_Num", int, float)


def clamp(value: _Num, lo: _Num, hi: _Num | None = None) -> _Num:
    """Clamp ``value`` into ``[lo, hi]`` (or ``[lo, +inf)`` when ``hi`` is ``None``).

    Cached/uploaded JSON (.user_defaults.json, .tax_pdf_cache.json) can seed a
    widget ``value`` outside its ``[min_value, max_value]`` bounds, and Streamlit
    raises ``StreamlitAPIException`` at render time — crashing the Joint sub-tab on
    load with no user interaction (audit C4). The widget bounds are widened to
    generous limits so no legitimate value is ever out of range; this clamp is a
    final backstop so genuinely corrupt data still cannot crash the render.

    ``hi=None`` supports the min-only call sites (``min_value=0`` widgets with
    no natural upper bound, e.g. account balances and the stock price) added
    across ``_accounts.py``/``_options.py`` as part of audit-0823 M3, so those
    sites aren't forced through a fake ``hi`` sentinel.
    """
    clamped = max(value, lo)
    if hi is not None:
        clamped = min(clamped, hi)
    return clamped
