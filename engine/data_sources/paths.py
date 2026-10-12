"""Centralized on-disk cache paths for the Setup / Command Center feature.

Pure module: stdlib only. No streamlit, no other engine imports.

Single source of truth for the three cache-file locations app.py wires
through ``get_household()`` (candidate store, trust choices, committed
baseline) so other call sites (e.g. ``views/setup/command_center.py``) don't
need to redefine or import them from app.py.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

CANDIDATE_STORE_PATH = _REPO_ROOT / ".candidate_store.json"
TRUST_CHOICES_PATH = _REPO_ROOT / ".trust_choices.json"
COMMITTED_PATH = _REPO_ROOT / ".committed_household.json"

__all__ = ["CANDIDATE_STORE_PATH", "COMMITTED_PATH", "TRUST_CHOICES_PATH"]
