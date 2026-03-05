"""Pytest configuration — ensure project root is on sys.path."""

import sys
from pathlib import Path

# Add project root to path so `from engine...` and `from models...` work
sys.path.insert(0, str(Path(__file__).parent.parent))
