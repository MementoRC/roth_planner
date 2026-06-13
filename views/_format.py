"""View-layer formatting helpers.

Centralizes currency display so changes (e.g. locale, accounting-style negatives)
land in one place instead of 176+ f-string sites scattered across views/.
"""

from __future__ import annotations

import math


def fmt_dollars(value: float | int | None, decimals: int = 0, sign: bool = False) -> str:
    """Format a numeric value as a US dollar string.

    Behavior chosen to be a drop-in replacement for `f"${x:,.0f}"` /
    `f"${x:,.2f}"` / `f"${x:+,.0f}"` patterns currently scattered across views/.

    - None or NaN → "$0" (matches Streamlit's tolerance for missing scenario data).
    - sign=True produces a leading +/- (e.g. "$+1,234" or "$-1,234") for delta
      displays. Mirrors the existing `:+,.0f` format-spec behavior exactly.
    - sign=False uses the default representation; negatives render as "$-1,234".
    """
    if value is None:
        return "$0"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "$0"
    if math.isnan(v) or math.isinf(v):
        return "$0"
    spec = f"{'+' if sign else ''},.{decimals}f"
    return f"${format(v, spec)}"
