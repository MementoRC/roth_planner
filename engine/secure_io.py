"""Secure I/O helpers for PII caches.

Provides :func:`write_pii_json` which atomically creates a file with mode
0o600, closing two attack surfaces:

1. TOCTOU race — ``write_text`` + ``chmod`` leaves a 0644 window between
   creation and permission tightening; ``os.open`` with the mode argument
   means the file is never momentarily world-readable.
2. Symlink-follow attack — ``O_NOFOLLOW`` causes ``os.open`` to raise
   ``OSError`` (ELOOP) if the target path is a symlink, so a pre-planted
   symlink cannot redirect the write to an attacker-controlled location.

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
    the process umask.  ``os.O_NOFOLLOW`` causes ``os.open`` to raise
    ``OSError`` (ELOOP) if *path* is a symlink, closing the symlink-follow
    attack surface.
    """
    payload = json.dumps(obj, indent=indent).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, payload)
    finally:
        os.close(fd)


def read_pii_json(path: Path) -> Any:
    """Read JSON from *path*, refusing to follow a symlink.

    Read-side mirror of :func:`write_pii_json`'s ``O_NOFOLLOW`` protection: a
    pre-planted symlink at *path* makes ``os.open`` raise ``OSError`` (ELOOP),
    so a PII cache read cannot be redirected to an attacker-controlled file.
    Raises ``OSError`` (including the symlink case) or ``json.JSONDecodeError``
    on malformed content — both already handled by the cache-load call sites.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    return json.loads(b"".join(chunks))
