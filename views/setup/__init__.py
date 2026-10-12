"""Top-level views.setup package.

``render()`` (the old 4-tab Command Center / Parameters / Portfolio / Data
bridge composition) was removed when the UI shell was collapsed to
Domains-only; the Setup shell was later split into the pages in
``views/profile_pages.py`` and ``views/data_pages.py``, which compose
Command Center and the partials directly and had no caller for it.
This module still re-exports a handful of names several submodules and
tests import from the package rather than each submodule directly.
"""

from __future__ import annotations

from ._partials import filing_status_from_label
from .parameters import (
    _FILING_STATUS_OPTIONS,
    _render_pdf_1040_import,
)

__all__ = [
    "_FILING_STATUS_OPTIONS",
    "_render_pdf_1040_import",
    "filing_status_from_label",
]
