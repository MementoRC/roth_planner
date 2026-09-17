"""Tests for engine.koinly_report_pdf -- Koinly crypto tax-report PDF parser."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, overload

import pytest

from engine.koinly_report_pdf import (
    INCOME_CATEGORIES,
    KoinlyParseError,
    KoinlyReport,
    _find_page,
    _parse_currency,
    extract_owner_key,
    load_koinly_report,
    parse_koinly_text,
    save_koinly_report,
)

_CG_PAGE = """TAX YEAR 2026
Capital gains summary
Number of disposals 700
Short term 1
Long term 699
Proceeds from sales $1.43
Short term $0.00
Long term $1.43
Acquisition costs $3.45
Short term $0.00
Long term $3.45
Profits, before losses $0.48
Short term $0.00
Long term $0.48
Losses $2.50
Short term $0.00
Long term $2.50
Net gains $-2.02
Short term $0.00
Long term $-2.02
"""

_INCOME_PAGE = """TAX YEAR 2026
Income summary Expenses summary
Airdrop $0.00 Margin fee $0.00
Fork $0.00 Loan fee $0.00
Mining $0.00 Other fee $0.00
Reward $384.45 Cost $0.27
Salary $0.00 Total $0.27
Lending interest $0.00
Other income $0.00
Total $384.45
"""


class TestParseCurrency:
    def test_negative_after_dollar(self) -> None:
        assert _parse_currency("$-2.02") == pytest.approx(-2.02)

    def test_thousands(self) -> None:
        assert _parse_currency("$12,345.67") == pytest.approx(12345.67)

    def test_parenthesized(self) -> None:
        assert _parse_currency("(5,000)") == pytest.approx(-5000.0)


class TestParseKoinlyText:
    def test_happy_path(self) -> None:
        rec = parse_koinly_text([_CG_PAGE, _INCOME_PAGE])
        assert rec.tax_year == 2026
        assert rec.crypto_stcg == pytest.approx(0.0)
        assert rec.crypto_ltcg == pytest.approx(-2.02)
        assert rec.crypto_income == pytest.approx(384.45)

    def test_income_category_breakdown_in_provenance(self) -> None:
        rec = parse_koinly_text([_CG_PAGE, _INCOME_PAGE])
        by_cat = rec.provenance["income_by_category"]
        assert by_cat["Reward"] == pytest.approx(384.45)
        assert set(by_cat) == set(INCOME_CATEGORIES)

    def test_multi_category_income_sum(self) -> None:
        income_page = (
            "TAX YEAR 2026\nIncome summary\n"
            "Airdrop $100.00\nFork $0.00\nMining $50.00\nReward $384.45\n"
            "Salary $0.00\nLending interest $25.55\nOther income $0.00\nTotal $560.00\n"
        )
        rec = parse_koinly_text([_CG_PAGE, income_page])
        assert rec.crypto_income == pytest.approx(560.0)
        assert "income_total_mismatch" not in rec.provenance

    def test_income_total_mismatch_note(self) -> None:
        income_page = (
            "TAX YEAR 2026\nIncome summary\n"
            "Airdrop $0.00\nFork $0.00\nMining $0.00\nReward $384.45\n"
            "Salary $0.00\nLending interest $0.00\nOther income $0.00\nTotal $999.00\n"
        )
        rec = parse_koinly_text([_CG_PAGE, income_page])
        assert rec.crypto_income == pytest.approx(384.45)
        assert "income_total_mismatch" in rec.provenance

    def test_zero_income(self) -> None:
        income_page = (
            "TAX YEAR 2026\nIncome summary\n"
            "Airdrop $0.00\nFork $0.00\nMining $0.00\nReward $0.00\n"
            "Salary $0.00\nLending interest $0.00\nOther income $0.00\nTotal $0.00\n"
        )
        rec = parse_koinly_text([_CG_PAGE, income_page])
        assert rec.crypto_income == pytest.approx(0.0)

    def test_positive_net_gains(self) -> None:
        cg = _CG_PAGE.replace(
            "Net gains $-2.02\nShort term $0.00\nLong term $-2.02",
            "Net gains $5,000.00\nShort term $1,200.00\nLong term $3,800.00",
        )
        rec = parse_koinly_text([cg, _INCOME_PAGE])
        assert rec.crypto_stcg == pytest.approx(1200.0)
        assert rec.crypto_ltcg == pytest.approx(3800.0)

    def test_missing_tax_year_raises(self) -> None:
        with pytest.raises(KoinlyParseError, match="TAX YEAR"):
            parse_koinly_text(
                [
                    "Capital gains summary\nNet gains $0.00\nShort term $0.00\n"
                    "Long term $0.00\nIncome summary\nReward $0.00\nTotal $0.00\n"
                ]
            )

    def test_missing_capital_gains_page_raises(self) -> None:
        with pytest.raises(KoinlyParseError, match="Capital gains summary"):
            parse_koinly_text(["TAX YEAR 2026\nIncome summary\nReward $0.00\nTotal $0.00\n"])

    def test_missing_income_page_raises(self) -> None:
        with pytest.raises(KoinlyParseError, match="Income summary"):
            parse_koinly_text([_CG_PAGE])


class TestKoinlyCache:
    def test_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.koinly_report_pdf as mod

        monkeypatch.setattr(mod, "_KOINLY_CACHE_PATH", tmp_path / ".koinly_cache.json")
        rec = KoinlyReport(
            tax_year=2026,
            crypto_stcg=0.0,
            crypto_ltcg=-2.02,
            crypto_income=384.45,
            captured_at="2026-07-12T00:00:00+00:00",
        )
        save_koinly_report(rec)
        loaded = load_koinly_report()
        assert loaded is not None
        assert loaded.crypto_income == pytest.approx(384.45)
        assert loaded.crypto_ltcg == pytest.approx(-2.02)
        assert loaded.tax_year == 2026

    def test_load_missing_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.koinly_report_pdf as mod

        monkeypatch.setattr(mod, "_KOINLY_CACHE_PATH", tmp_path / "nope.json")
        assert load_koinly_report() is None


_REAL_SAMPLE = (
    Path(__file__).resolve().parent.parent.parent
    / "PDF-Statements"
    / "koinly_2026_complete_tax_report_July.pdf"
)


@pytest.mark.skipif(not _REAL_SAMPLE.exists(), reason="real Koinly sample PDF not present")
def test_parse_real_koinly_sample() -> None:
    from engine.koinly_report_pdf import parse_koinly_pdf

    rec = parse_koinly_pdf(_REAL_SAMPLE.read_bytes())
    assert rec.tax_year == 2026
    assert rec.crypto_stcg == pytest.approx(0.0, abs=0.01)
    assert rec.crypto_ltcg == pytest.approx(-2.02, abs=0.01)
    assert rec.crypto_income == pytest.approx(384.45, abs=0.01)


class TestExtractOwnerKey:
    def test_extracts_name_from_cover_page(self) -> None:
        cover = "Complete Tax Report\nPrepared for Claude R Cirba\nTAX YEAR 2026\n"
        assert extract_owner_key([cover, _CG_PAGE, _INCOME_PAGE]) == "Claude R Cirba"

    def test_extracts_email_when_no_name_line(self) -> None:
        cover = "Complete Tax Report\nclaude.cirba@example.com\nTAX YEAR 2026\n"
        assert extract_owner_key([cover, _CG_PAGE, _INCOME_PAGE]) == "claude.cirba@example.com"

    def test_returns_none_when_absent(self) -> None:
        assert extract_owner_key([_CG_PAGE, _INCOME_PAGE]) is None


# ---------------------------------------------------------------------------
# TestExtractOwnerKeyIncrementalWalk — extract_owner_key's page-by-page
# early-stop walk must return EXACTLY what the old eager
# "\n".join(pages) + regex search returned, in every boundary shape, while
# reading as few pages as the proof in its docstring allows.
# ---------------------------------------------------------------------------


class TestExtractOwnerKeyIncrementalWalk:
    def test_owner_on_early_page_stops_within_one_page(self) -> None:
        # Owner text on page index 1 of 90, with more content following it on
        # the SAME page — the match ends well before the prefix end, so the
        # walk finalizes at page 1 itself; page 2 is never read.
        pages = [_FILLER_KOINLY_PAGE] * 90
        pages[1] = "Prepared for Jane Doe\nMore cover-page text follows.\n"
        recording = _RecordingPages(pages)

        result = extract_owner_key(recording)
        expected = extract_owner_key(pages)

        assert result == expected == "Jane Doe"
        assert max(recording.accessed) == 1

    def test_boundary_trailing_anchor_no_whitespace_yet(self) -> None:
        # Page 5 ends with literal "Prepared for" and NOTHING after it (no
        # trailing whitespace); the name resumes on page 6. `\s+` requires
        # >=1 char it doesn't have yet, so the page-5-only prefix has no
        # match at all (not even a same-position partial) — the walk must
        # continue to page 6, where the page-join "\n" supplies the `\s+`
        # and the name completes. Result equals the full-join result.
        pages = [_FILLER_KOINLY_PAGE] * 90
        pages[5] = "Cover filler text ending with Prepared for"
        pages[6] = "Jane Six\nMore cover text.\n"
        recording = _RecordingPages(pages)

        result = extract_owner_key(recording)
        expected = extract_owner_key(pages)

        assert result == expected == "Jane Six"
        assert max(recording.accessed) == 6

    def test_boundary_name_plus_more_page_content_stops_at_newline(self) -> None:
        # Page 5 ends with "Prepared for Jane" (no trailing newline); page 6
        # opens with more text. `.+` cannot cross the page-join "\n", so the
        # name is "Jane" only — in BOTH the old full-join code and here.
        # After page 5, the match runs to the prefix end exactly (m.end() ==
        # len(prefix)), so the walk reads page 6 before finalizing, but the
        # match itself does not grow.
        pages = [_FILLER_KOINLY_PAGE] * 90
        pages[5] = "Cover filler text.\nPrepared for Jane"
        pages[6] = " Doe continues here.\nMore cover text.\n"
        recording = _RecordingPages(pages)

        result = extract_owner_key(recording)
        expected = extract_owner_key(pages)

        assert result == expected == "Jane"
        assert max(recording.accessed) == 6

    def test_owner_absent_email_present_late_matches_full_join(self) -> None:
        # No "Prepared for" anywhere -> the name walk must read every page.
        # The email walk then finds a match on page 85 and stops there
        # (entirely newline-free pattern, cannot cross a page boundary).
        pages = [_FILLER_KOINLY_PAGE] * 90
        pages[85] = "Contact: jane.doe@example.com\n"
        recording = _RecordingPages(pages)

        result = extract_owner_key(recording)
        expected = extract_owner_key(pages)

        assert result == expected == "jane.doe@example.com"

    def test_neither_name_nor_email_present_matches_full_join(self) -> None:
        pages = [_FILLER_KOINLY_PAGE] * 90
        recording = _RecordingPages(pages)

        result = extract_owner_key(recording)
        expected = extract_owner_key(pages)

        assert result is None
        assert result == expected
        assert max(recording.accessed) == 89

    def test_name_on_last_page_no_trailing_newline_matches_full_join(self) -> None:
        # Name on the very last page with nothing after it at all: `.+`
        # still matches to the true end of string, matching the old
        # full-join behaviour (there's no next page to distinguish "end of
        # this page" from "end of everything").
        pages = [_FILLER_KOINLY_PAGE] * 90
        pages[-1] = "Prepared for Last Page Owner"
        recording = _RecordingPages(pages)

        result = extract_owner_key(recording)
        expected = extract_owner_key(pages)

        assert result == expected == "Last Page Owner"
        assert max(recording.accessed) == 89


def test_extract_income_two_column_page_picks_income_total_not_expenses():
    # Real pdfplumber flattening of the side-by-side Income/Expenses summary page:
    # the Expenses column's "Total $0.27" appears BEFORE the income "Total $384.45"
    # because the Expenses column is shorter. The parser must report the INCOME
    # total ($384.45 == summed categories), not the Expenses total ($0.27).
    income_text = (
        "TAX YEAR 2026\n"
        "Income summary Expenses summary\n"
        "Airdrop $0.00\n"
        "Margin fee $0.00\n"
        "Fork $0.00\n"
        "Loan fee $0.00\n"
        "Mining $0.00\n"
        "Other fee $0.00\n"
        "Reward $384.45\n"
        "Cost $0.27\n"
        "Salary $0.00\n"
        "Total $0.27\n"
        "Lending interest $0.00\n"
        "Other income $0.00\n"
        "Total $384.45\n"
        "Generated by Koinly 4 (93)\n"
    )
    from engine.koinly_report_pdf import _extract_income

    summed, per_category, reported_total = _extract_income(income_text)
    assert summed == pytest.approx(384.45)
    assert per_category["Reward"] == pytest.approx(384.45)
    assert reported_total == pytest.approx(384.45)  # income Total, NOT the $0.27 expenses total


# ---------------------------------------------------------------------------
# TestLazyPageAccess — parse_koinly_text and _find_page must work over any
# Sequence[str] (not just list[str]) and only touch the pages they need.
# ---------------------------------------------------------------------------

_FILLER_KOINLY_PAGE = "This is a filler page with no Koinly section markers.\n"


class _RecordingPages(Sequence[str]):
    """Wraps a plain list[str]; logs every index accessed via __getitem__.

    A real ``Sequence[str]`` (not just a duck-typed stand-in) that reports
    exactly which indices were touched, so tests can assert on laziness
    directly without pulling in pdfplumber/LazyPageTexts itself.
    """

    def __init__(self, pages: list[str]) -> None:
        self._pages = pages
        self.accessed: list[int] = []

    def __len__(self) -> int:
        return len(self._pages)

    @overload
    def __getitem__(self, index: int) -> str: ...

    @overload
    def __getitem__(self, index: slice) -> list[str]: ...

    def __getitem__(self, index: int | slice) -> str | list[str]:
        if isinstance(index, slice):
            indices = range(*index.indices(len(self._pages)))
            self.accessed.extend(indices)
            return [self._pages[i] for i in indices]
        normalized = index + len(self._pages) if index < 0 else index
        self.accessed.append(normalized)
        return self._pages[normalized]

    def __iter__(self) -> Iterator[str]:
        for i in range(len(self._pages)):
            yield self[i]


def _report_sans_captured_at(report: KoinlyReport) -> dict[str, Any]:
    d = report.to_dict()
    d.pop("captured_at", None)
    return d


class TestFindPageLazyAccess:
    """_find_page's first-match+break scan (the section lookups on the parse
    path) only needs pages up to the matching index."""

    def test_capital_gains_summary_only_reads_needed_pages(self) -> None:
        pages = [_FILLER_KOINLY_PAGE] * 90
        pages[1] = _CG_PAGE
        recording = _RecordingPages(pages)

        result = _find_page(recording, "Capital gains summary")

        assert result == _CG_PAGE
        assert max(recording.accessed) <= 1

    def test_income_summary_only_reads_needed_pages(self) -> None:
        pages = [_FILLER_KOINLY_PAGE] * 90
        pages[3] = _INCOME_PAGE
        recording = _RecordingPages(pages)

        result = _find_page(recording, "Income summary")

        assert result == _INCOME_PAGE
        assert max(recording.accessed) <= 3


class TestParseKoinlyTextLazySequence:
    def test_equivalent_result_via_recording_sequence(self) -> None:
        # 90-page bundle: Capital gains summary (+ year marker) at index 1,
        # Income summary at index 3.
        pages = [_FILLER_KOINLY_PAGE] * 90
        pages[1] = _CG_PAGE
        pages[3] = _INCOME_PAGE
        recording = _RecordingPages(pages)

        result = parse_koinly_text(recording)
        expected = parse_koinly_text(pages)

        assert _report_sans_captured_at(result) == _report_sans_captured_at(expected)
        # NOTE: no "Prepared for" name or email pattern appears anywhere in
        # this bundle, so extract_owner_key's incremental walk (see its
        # docstring) never finds an early-final match and must read through
        # to the last page before falling back to a full-prefix search —
        # this is the one case where the early-stop optimisation cannot help.
        # The year marker / Capital gains / Income summary lookups are
        # independently lazy (see TestFindPageLazyAccess) but that saving is
        # masked here by the owner-key step that runs after them.
        assert max(recording.accessed) == 89

    def test_owner_key_found_on_late_page_still_extracted(self) -> None:
        # Owner text on a late page (index 80 of 90) must still be found —
        # and, with the early-stop walk, the read now stops at page 80
        # instead of continuing through to page 89.
        pages = [_FILLER_KOINLY_PAGE] * 90
        pages[1] = _CG_PAGE
        pages[3] = _INCOME_PAGE
        pages[80] = "Prepared for Claude R Cirba\n"
        recording = _RecordingPages(pages)

        result = parse_koinly_text(recording)
        expected = parse_koinly_text(pages)

        assert result.owner_key == "Claude R Cirba"
        assert _report_sans_captured_at(result) == _report_sans_captured_at(expected)
        assert max(recording.accessed) == 80


# ---------------------------------------------------------------------------
# TestParseKoinlyPdfWrapper — the pdfplumber-facing wrapper must extract text
# lazily (via LazyPageTexts) and produce identical results to the pure parser.
# ---------------------------------------------------------------------------


class _FakeKoinlyPdfPage:
    def __init__(self, text: str) -> None:
        self._text = text
        self.extract_calls = 0

    def extract_text(self) -> str:
        self.extract_calls += 1
        return self._text

    def close(self) -> None:
        pass


class _FakeKoinlyPdf:
    def __init__(self, pages: list[_FakeKoinlyPdfPage]) -> None:
        self.pages = pages

    def __enter__(self) -> _FakeKoinlyPdf:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class TestParseKoinlyPdfWrapper:
    def test_wrapper_matches_pure_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        texts = [_FILLER_KOINLY_PAGE, _CG_PAGE, _FILLER_KOINLY_PAGE, _INCOME_PAGE]
        fake_pages = [_FakeKoinlyPdfPage(t) for t in texts]
        fake_pdf = _FakeKoinlyPdf(fake_pages)

        def fake_open(_stream: object) -> _FakeKoinlyPdf:
            return fake_pdf

        monkeypatch.setattr("pdfplumber.open", fake_open)

        from engine.koinly_report_pdf import parse_koinly_pdf

        result = parse_koinly_pdf(b"irrelevant-bytes")
        expected = parse_koinly_text(texts)

        assert _report_sans_captured_at(result) == _report_sans_captured_at(expected)
        assert all(p.extract_calls <= 1 for p in fake_pages)
