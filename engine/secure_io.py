"""Secure reads of the legacy PII JSON files.

Every store lives in the database now (:mod:`engine.store`). These helpers read
the legacy JSON dotfiles for the one-time import and, after a failed import, on
every call through the read-only fallback (``engine.store.json_readonly``).
Both refuse to follow a symlink (``O_NOFOLLOW``), so a pre-planted link cannot
redirect a read to an attacker-controlled file. The database file gets the same
0600 and no-symlink rules in :mod:`engine.store.sqlite_backend`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def read_pii_bytes(path: Path) -> bytes:
    """Read raw bytes from *path*, refusing to follow a symlink.

    Bytes-returning sibling of :func:`read_pii_json` for callers that need the
    file's raw content rather than parsed JSON. A pre-planted symlink at *path*
    makes ``os.open`` raise ``OSError`` (ELOOP), so the read cannot be
    redirected to an attacker file.
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
    return b"".join(chunks)


def read_pii_json(path: Path) -> Any:
    """Read JSON from *path*, refusing to follow a symlink.

    A pre-planted symlink at *path* makes ``os.open`` raise ``OSError``
    (ELOOP), so a PII read cannot be redirected to an attacker-controlled file.
    Raises ``OSError`` (including the symlink case) or ``json.JSONDecodeError``
    on malformed content. Non-UTF-8 bytes are reported as
    ``json.JSONDecodeError`` too (audit-1018 F-20), so no caller sees a bare
    ``UnicodeDecodeError``.
    """
    raw = read_pii_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise json.JSONDecodeError(f"not valid UTF-8: {exc}", "", exc.start) from exc
    return json.loads(text)
