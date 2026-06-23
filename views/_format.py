"""View-layer formatting helpers.

Centralizes currency display so changes (e.g. locale, accounting-style negatives)
land in one place instead of 176+ f-string sites scattered across views/.
"""

from __future__ import annotations

import math
from typing import Literal

# ---------------------------------------------------------------------------
# Canonical UI caption strings shared across multiple views
# ---------------------------------------------------------------------------

FORM_8606_CAPTION = (
    "ℹ️ Assumes $0 Form 8606 basis — all Trad IRA dollars treated as pretax. "
    "If you have non-deductible contributions tracked on Form 8606, "
    "taxable conversion income will be lower than shown."
)


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


def fmt_dollars_short(
    value: float | int | None,
    decimals: int = 1,
    suffix: Literal["M", "K", "auto"] = "M",
) -> str:
    """Format a numeric value as a short dollar string with K/M suffix.

    Drop-in replacement for `f"${x/1e6:.Nf}M"` and `f"${x/1000:.Nf}K"` patterns
    in domain-summary displays.

    - suffix="M" (default, dominant existing pattern): divide by 1e6, append "M".
    - suffix="K": divide by 1000, append "K".
    - suffix="auto": pick by magnitude. >=1e6 → "M", >=1e3 → "K", else plain
      (no suffix). Useful for new sites where the magnitude is unknown.
    - None or NaN → "$0".
    """
    if value is None:
        return "$0"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "$0"
    if math.isnan(v) or math.isinf(v):
        return "$0"

    if suffix == "auto":
        abs_v = abs(v)
        if abs_v >= 1_000_000:
            return f"${v / 1_000_000:,.{decimals}f}M"
        if abs_v >= 1_000:
            return f"${v / 1_000:,.{decimals}f}K"
        return f"${v:,.{decimals}f}"
    if suffix == "K":
        return f"${v / 1_000:,.{decimals}f}K"
    return f"${v / 1_000_000:,.{decimals}f}M"


def fmt_pct(value: float | None, decimals: int = 1, sign: bool = False) -> str:
    """Format a fraction (0.0419) as a percent string ("4.2%").

    Mirrors fmt_dollars conventions: None/NaN coerce to a zero-valued render
    ("0.0%" with default decimals=1, or "0%" with decimals=0). The input
    `value` is a FRACTION; the helper multiplies by 100 internally so callers
    pass 0.0419 (not 4.19) to render "4.2%".

    Args:
        value: fraction to render (e.g., 0.0419 for 4.19%).
        decimals: digits after the decimal point (default 1).
        sign: include explicit + sign for non-negative values (default False).
    """
    if value is None or (isinstance(value, float) and value != value):
        value = 0.0
    fmt_spec = f"{{:+.{decimals}f}}" if sign else f"{{:.{decimals}f}}"
    return fmt_spec.format(value * 100) + "%"
