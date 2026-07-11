"""Pytest configuration for roth_planner test suite.

Isolates tests from a developer's local .user_defaults.json/.py by setting
ROTH_PLANNER_IGNORE_USER_DEFAULTS before any app module (e.g. models.household)
is imported, since Household's dataclass field defaults are resolved at
import time via config.loader.load_defaults().
"""

import os

os.environ.setdefault("ROTH_PLANNER_IGNORE_USER_DEFAULTS", "1")

import sys
from pathlib import Path

# Add project root to path so `from engine...` and `from models...` work
sys.path.insert(0, str(Path(__file__).parent.parent))
