"""Lazy per-page text extraction over pdfplumber-like page objects.

Why this exists: in the browser build (Pyodide/stlite), pdfplumber text
extraction costs roughly 0.5s per page. The 1040 and Koinly parsers each
only need 2 early pages out of documents that can run to 32-288 pages (they
locate a page by scanning for a footer/heading and stop at the first match).
Building the eager ``[page.extract_text() or "" for page in pdf.pages]``
list pays the full per-page cost for every page in the document up front,
even though only a couple of pages are ever read.

``LazyPageTexts`` wraps a sequence of page objects (anything exposing
``extract_text()`` and, optionally, ``close()`` — i.e. pdfplumber's
``Page``) and defers extraction to first access, caching the result so a
page is never re-extracted. ``len()`` is free (page-tree only, no content
parsing). Closing a page after extraction flushes its layout caches,
mirroring what an eager loop would do implicitly by dropping the reference.

Pure Python — no Streamlit import, no top-level pdfplumber import. The
constructor accepts any sequence of objects duck-typed to pdfplumber's
``Page`` (``extract_text() -> str | None`` and an optional ``close()``), so
this module stays importable without pdfplumber installed.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, overload


class LazyPageTexts(Sequence[str]):
    """Lazily-extracting, caching view over a sequence of PDF page objects.

    Behaves like the eager ``list[str]`` of per-page text the parsers used
    to receive: ``len()``, integer indexing (positive and negative), slicing,
    and iteration all work. Unlike the eager list, ``page.extract_text()``
    is only called for pages that are actually accessed, and only once per
    page (the result is cached). After a page's text is extracted, the
    underlying page object's ``close()`` is called if present, to release
    its layout caches — mirroring the eager loop, which implicitly drops
    the page reference after extraction.
    """

    def __init__(self, pages: Sequence[Any]) -> None:
        self._pages = pages
        self._cache: dict[int, str] = {}

    def __len__(self) -> int:
        return len(self._pages)

    def _normalize_index(self, index: int) -> int:
        length = len(self._pages)
        normalized = index + length if index < 0 else index
        if not 0 <= normalized < length:
            raise IndexError(f"page index {index} out of range for {length} pages")
        return normalized

    def _extract(self, index: int) -> str:
        if index not in self._cache:
            page = self._pages[index]
            text = page.extract_text()
            self._cache[index] = text or ""
            close = getattr(page, "close", None)
            if callable(close):
                close()
        return self._cache[index]

    @overload
    def __getitem__(self, index: int) -> str: ...

    @overload
    def __getitem__(self, index: slice) -> list[str]: ...

    def __getitem__(self, index: int | slice) -> str | list[str]:
        if isinstance(index, slice):
            return [self._extract(i) for i in range(*index.indices(len(self._pages)))]
        return self._extract(self._normalize_index(index))

    def __iter__(self) -> Iterator[str]:
        for i in range(len(self._pages)):
            yield self._extract(i)

    @property
    def extracted_indices(self) -> tuple[int, ...]:
        """Sorted indices whose text has been extracted so far (diagnostics/tests)."""
        return tuple(sorted(self._cache.keys()))
