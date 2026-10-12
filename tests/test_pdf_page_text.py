"""Tests for engine/pdf_page_text.py -- LazyPageTexts."""

from __future__ import annotations

import pytest

from engine.pdf_page_text import LazyPageTexts


class FakePage:
    """Records extract_text/close calls; can simulate a blank page (None)."""

    def __init__(self, text: str | None) -> None:
        self._text = text
        self.extract_calls = 0
        self.close_calls = 0

    def extract_text(self) -> str | None:
        self.extract_calls += 1
        return self._text

    def close(self) -> None:
        self.close_calls += 1


def _fake_pages(*texts: str | None) -> list[FakePage]:
    return [FakePage(t) for t in texts]


class TestLen:
    def test_len_does_not_extract(self) -> None:
        pages = _fake_pages("a", "b", "c")
        lazy = LazyPageTexts(pages)
        assert len(lazy) == 3
        assert all(p.extract_calls == 0 for p in pages)


class TestExtractionAndCaching:
    def test_extract_once_and_cache(self) -> None:
        pages = _fake_pages("hello", "world")
        lazy = LazyPageTexts(pages)
        assert lazy[0] == "hello"
        assert lazy[0] == "hello"
        assert pages[0].extract_calls == 1
        assert pages[1].extract_calls == 0

    def test_none_becomes_empty_string(self) -> None:
        pages = _fake_pages(None)
        lazy = LazyPageTexts(pages)
        assert lazy[0] == ""

    def test_close_called_once_after_extract(self) -> None:
        pages = _fake_pages("x")
        lazy = LazyPageTexts(pages)
        assert pages[0].close_calls == 0
        lazy[0]
        assert pages[0].close_calls == 1
        lazy[0]
        assert pages[0].close_calls == 1

    def test_close_not_called_before_access(self) -> None:
        pages = _fake_pages("a", "b")
        LazyPageTexts(pages)
        assert all(p.close_calls == 0 for p in pages)


class TestIndexing:
    def test_negative_index(self) -> None:
        pages = _fake_pages("first", "second", "third")
        lazy = LazyPageTexts(pages)
        assert lazy[-1] == "third"
        assert pages[2].extract_calls == 1
        assert pages[0].extract_calls == 0
        assert pages[1].extract_calls == 0

    def test_index_out_of_range_raises(self) -> None:
        pages = _fake_pages("a")
        lazy = LazyPageTexts(pages)
        with pytest.raises(IndexError):
            lazy[5]

    def test_negative_index_out_of_range_raises(self) -> None:
        pages = _fake_pages("a")
        lazy = LazyPageTexts(pages)
        with pytest.raises(IndexError):
            lazy[-5]

    def test_slice_returns_list_of_extracted_text(self) -> None:
        pages = _fake_pages("a", "b", "c", "d")
        lazy = LazyPageTexts(pages)
        assert lazy[1:3] == ["b", "c"]
        assert pages[1].extract_calls == 1
        assert pages[2].extract_calls == 1
        assert pages[0].extract_calls == 0
        assert pages[3].extract_calls == 0


class TestIteration:
    def test_iteration_order(self) -> None:
        pages = _fake_pages("a", "b", "c")
        lazy = LazyPageTexts(pages)
        assert list(lazy) == ["a", "b", "c"]

    def test_iteration_is_lazy(self) -> None:
        pages = _fake_pages("a", "b", "c")
        lazy = LazyPageTexts(pages)
        it = iter(lazy)
        first = next(it)
        assert first == "a"
        assert pages[0].extract_calls == 1
        assert pages[1].extract_calls == 0
        assert pages[2].extract_calls == 0


class TestExtractedIndices:
    def test_empty_before_access(self) -> None:
        pages = _fake_pages("a", "b")
        lazy = LazyPageTexts(pages)
        assert lazy.extracted_indices == ()

    def test_reflects_accessed_pages_sorted(self) -> None:
        pages = _fake_pages("a", "b", "c")
        lazy = LazyPageTexts(pages)
        lazy[2]
        lazy[0]
        assert lazy.extracted_indices == (0, 2)
