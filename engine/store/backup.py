"""Dated safety copies of the personal files before a risky transition.

Leaving demo mode is the first point at which the app writes the personal
settings file and database again. A copy taken there means a bad write can be
undone without a filesystem snapshot. Copies go to
``<folder>/.json_backup/<YYYY-MM-DD-HHMMSS>-<label>/`` (mode 0700), the folder
the one-time JSON import already uses (:mod:`engine.store.migrate_json`).
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from engine.store.migrate_json import BACKUP_DIR_NAME

_log = logging.getLogger(__name__)


def backup_dir_for(folder: Path, label: str, now: datetime) -> Path:
    """The dated backup folder for *label* under *folder*."""
    return folder / BACKUP_DIR_NAME / f"{now.strftime('%Y-%m-%d-%H%M%S')}-{label}"


def _make_private_dir(target: Path) -> None:
    for directory in (target.parent, target):
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)


def backup_files(paths: Iterable[Path], *, label: str, now: datetime) -> list[Path]:
    """Copy each existing file into its folder's dated backup; return the copies.

    A SQLite database (``*.db``) is copied with SQLite's online backup, so a
    database open in WAL mode is copied consistently. Best-effort: a failure is
    logged and never raised (a page must still render).
    """
    copies: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        target = backup_dir_for(path.parent, label, now)
        try:
            _make_private_dir(target)
            dest = target / path.name
            if path.suffix == ".db":
                _copy_database(path, dest)
            else:
                shutil.copy2(path, dest)
            os.chmod(dest, 0o600)
        except (OSError, sqlite3.Error) as exc:
            _log.warning("could not back up %s to %s: %s", path.name, target, exc)
            continue
        copies.append(dest)
    return copies


def _copy_database(source: Path, dest: Path) -> None:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
