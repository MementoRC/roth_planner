"""Centralized on-disk cache paths for the Setup / Command Center feature.

Pure module: stdlib only. No streamlit, no other engine imports.

Single source of truth for the cache-file locations app.py wires
through ``get_household()`` (candidate store, trust choices) so other call
sites (e.g. ``views/setup/command_center.py``) don't
need to redefine or import them from app.py.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

TRUST_CHOICES_PATH = _REPO_ROOT / ".trust_choices.json"

__all__ = ["TRUST_CHOICES_PATH"]
