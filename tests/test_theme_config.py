"""The shipped theme (.streamlit/config.toml) keeps WCAG-readable contrast."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml"
WHITE = "#ffffff"


def _theme() -> dict:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))["theme"]


def _luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance of ``#rrggbb``."""
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_theme_follows_system() -> None:
    assert "base" not in _theme()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_palette_contrast(mode: str) -> None:
    p = _theme()[mode]
    # Streamlit draws white text on primary buttons.
    assert _contrast(WHITE, p["primaryColor"]) >= 4.5
    assert _contrast(p["primaryColor"], p["backgroundColor"]) >= 3.0
    assert _contrast(p["textColor"], p["backgroundColor"]) >= 7.0
    assert _contrast(p["textColor"], p["secondaryBackgroundColor"]) >= 4.5
