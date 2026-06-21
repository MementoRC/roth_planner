"""Secure I/O helpers for PII caches.

Provides :func:`write_pii_json` which atomically creates a file with mode
0o600, closing the TOCTOU race that ``write_text`` + ``chmod`` leaves open
(the 0644 window between creation and permission tightening).

Pattern mirrors ``engine.data_bridge_keys._write_keyfile`` (PR #172 precedent).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_pii_json(path: Path, obj: Any, *, indent: int = 2) -> None:
    """Write *obj* as JSON to *path* with mode 0o600, atomically.

    ``os.open`` with the mode argument means the file is never momentarily
    world-readable (the old ``write_text`` + ``chmod`` pattern left a 0644
    window between creation and permission tightening).  ``os.fchmod``
    re-asserts the exact mode on the open file descriptor so a force-overwrite
    of a pre-existing looser-permissioned file is corrected independently of
    the process umask.
    """
    payload = json.dumps(obj, indent=indent).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, payload)
    finally:
        os.close(fd)
