"""Tests for engine/tax_return_pdf.py — TurboTax 1040 PDF parser."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from engine.irmaa import irmaa_surcharge
from engine.tax_return_pdf import (
    ANCHORS,
    Form1040ParseError,
    Form1040Record,
    compute_irmaa_magi,
    compute_magi,
    load_pdf_tax_records,
    parse_form_1040_text,
    save_pdf_tax_records,
)

# ---------------------------------------------------------------------------
# Synthetic page-text fixtures
# ---------------------------------------------------------------------------

# Minimal 1040 page text that satisfies all required anchors for 2023.
_F1040_2023 = """\
Department of the Treasury — Internal Revenue Service
Form 1040 (2023)         U.S. Individual Income Tax Return
Filing Status  Single  Married filing jointly  Head of household (HOH)

2a  Tax-exempt interest . .  2a  2,511   b  Taxable interest  2b  1,000
3a  Qualified dividends . .  3a     500   b  Ordinary dividends  3b  1,200
6   Social security benefits  6b  12,000
11  Subtract line 10 from line 9. This is your adjusted gross income  162,433
"""

# Schedule 1 page text with a FEIE entry.
_SCH1_2023 = """\
SCHEDULE 1 (Form 1040)
Schedule 1  (Form 1040)   Additional Income and Adjustments
8d  Foreign earned income exclusion  8d  3,000
"""

# 1040 page for 2024 with comma-formatted numbers.
_F1040_2024 = """\
Form 1040 (2024)  U.S. Individual Income Tax Return

2a  Tax-exempt interest . .  2a  3,000   b  Taxable interest  2b  500
3a  Qualified dividends . .  3a  1,000   b  Ordinary dividends  3b  2,000
6   Social security benefits  6b  0
11  Subtract line 10 from line 9. This is your adjusted gross income  200,000
"""

# Schedule 1 for 2024 — no FEIE.
_SCH1_2024 = """\
Schedule 1  (Form 1040)   Additional Income and Adjustments
"""

# 1040 page with no Schedule 1 in the bundle.
_F1040_NO_SCH1 = _F1040_2023  # reuse 2023 page

# A filler page that contains no useful markers.
_FILLER_PAGE = "This is a worksheet page with no 1040 content."


def _pages_2023_with_feie() -> list[str]:
    """Synthetic bundle: filler, Form 1040 (2023), another filler, Schedule 1."""
    return [_FILLER_PAGE, _F1040_2023, _FILLER_PAGE, _SCH1_2023]


def _pages_2023_no_sch1() -> list[str]:
    """Synthetic bundle: only Form 1040 (2023), no Schedule 1 page."""
    return [_FILLER_PAGE, _F1040_NO_SCH1, _FILLER_PAGE]


def _pages_2024() -> list[str]:
    """Synthetic bundle: Form 1040 (2024) + Schedule 1."""
    return [_F1040_2024, _SCH1_2024, _FILLER_PAGE]


def _pages_no_1040() -> list[str]:
    """Bundle with no Form 1040 marker at all."""
    return [_FILLER_PAGE, _FILLER_PAGE]


def _pages_unsupported_year() -> list[str]:
    """Bundle with a Form 1040 for an unsupported year (2019)."""
    return ["Form 1040 (2019)  U.S. Individual Income Tax Return\n11  100,000"]


# ---------------------------------------------------------------------------
# TestComputeMagi
# ---------------------------------------------------------------------------


class TestComputeMagi:
    def test_basic_addition(self) -> None:
        assert compute_magi(100_000.0, 2_000.0, 0.0) == 102_000.0

    def test_feie_added(self) -> None:
        assert compute_magi(100_000.0, 0.0, 5_000.0) == 105_000.0

    def test_all_components(self) -> None:
        assert compute_magi(162_433.0, 2_511.0, 3_000.0) == pytest.approx(167_944.0)

    def test_taxable_ss_not_double_counted(self) -> None:
        # SS is already in AGI — compute_magi does not take it as input
        # Confirm the signature has 3 params, not 4
        sig = inspect.signature(compute_magi)
        assert list(sig.parameters) == ["agi", "tax_exempt_interest", "feie"]

    def test_zero_adds(self) -> None:
        assert compute_magi(50_000.0, 0.0, 0.0) == 50_000.0


# ---------------------------------------------------------------------------
# TestComputeIrmaaMagi — audit HIGH: prior_year_magi (IRMAA-scoped) must NOT
# receive the FEIE-inclusive Roth/ACA-flavor MAGI.
# ---------------------------------------------------------------------------


class TestComputeIrmaaMagi:
    def test_excludes_feie(self) -> None:
        """Unlike compute_magi, FEIE must NOT be added back (42 U.S.C. §1395r(i)(4))."""
        assert compute_irmaa_magi(100_000.0, 0.0) == 100_000.0

    def test_signature_has_no_feie_param(self) -> None:
        sig = inspect.signature(compute_irmaa_magi)
        assert list(sig.parameters) == ["agi", "tax_exempt_interest"]

    def test_tax_exempt_interest_still_added(self) -> None:
        assert compute_irmaa_magi(100_000.0, 2_000.0) == 102_000.0

    def test_diverges_from_compute_magi_when_feie_present(self) -> None:
        """The whole point of the fix: the two flavors diverge when FEIE != 0."""
        agi, tei, feie = 200_000.0, 0.0, 20_000.0
        assert compute_magi(agi, tei, feie) == 220_000.0
        assert compute_irmaa_magi(agi, tei) == 200_000.0

    def test_concrete_2296_80_surcharge_discrepancy(self) -> None:
        """AGI=$200,000 + FEIE=$20,000: the audit's concrete failure case.

        Feeding the FEIE-inclusive flavor (compute_magi -> $220,000) into the
        IRMAA slot crosses the 2026 Tier-1 MFJ threshold ($218,000) and
        fabricates a $2,296.80/year surcharge. The IRMAA-correct flavor
        (compute_irmaa_magi -> $200,000) stays below Tier 1 -> $0 surcharge.
        """
        agi, feie = 200_000.0, 20_000.0
        wrong_magi = compute_magi(agi, 0.0, feie)
        correct_magi = compute_irmaa_magi(agi, 0.0)

        assert wrong_magi == 220_000.0
        assert correct_magi == 200_000.0

        wrong_surcharge = irmaa_surcharge(wrong_magi)
        correct_surcharge = irmaa_surcharge(correct_magi)

        assert wrong_surcharge == pytest.approx(2_296.80, abs=0.01)
        assert correct_surcharge == 0.0


# ---------------------------------------------------------------------------
# TestParsedForm2023
# ---------------------------------------------------------------------------


class TestParsedForm2023:
    def test_happy_path_values(self) -> None:
        rec = parse_form_1040_text(_pages_2023_with_feie())
        assert rec.tax_year == 2023
        assert rec.agi == pytest.approx(162_433.0)
        assert rec.tax_exempt_interest == pytest.approx(2_511.0)
        assert rec.feie == pytest.approx(3_000.0)
        assert rec.magi == pytest.approx(162_433.0 + 2_511.0 + 3_000.0)

    def test_qualified_dividends(self) -> None:
        rec = parse_form_1040_text(_pages_2023_with_feie())
        assert rec.qualified_dividends == pytest.approx(500.0)

    def test_ordinary_dividends(self) -> None:
        rec = parse_form_1040_text(_pages_2023_with_feie())
        assert rec.ordinary_dividends == pytest.approx(1_200.0)

    def test_taxable_ss(self) -> None:
        rec = parse_form_1040_text(_pages_2023_with_feie())
        assert rec.taxable_ss == pytest.approx(12_000.0)

    def test_filing_status_none(self) -> None:
        # Parser leaves filing_status=None; UI confirms later
        rec = parse_form_1040_text(_pages_2023_with_feie())
        assert rec.filing_status is None

    def test_source_and_version(self) -> None:
        rec = parse_form_1040_text(_pages_2023_with_feie())
        assert rec.source == "pdf"
        assert rec.parser_version == "1.0.0"

    def test_provenance_page_indices(self) -> None:
        rec = parse_form_1040_text(_pages_2023_with_feie())
        # Filler page is index 0; 1040 is index 1; Schedule 1 is index 3
        assert rec.provenance["f1040_page_index"] == 1
        assert rec.provenance["sch1_page_index"] == 3

    def test_provenance_form_revision(self) -> None:
        rec = parse_form_1040_text(_pages_2023_with_feie())
        assert rec.provenance["form_revision"] == "Form 1040 (2023)"

    def test_provenance_pages_total(self) -> None:
        pages = _pages_2023_with_feie()
        rec = parse_form_1040_text(pages)
        assert rec.provenance["pdf_pages_total"] == len(pages)

    def test_captured_at_is_iso(self) -> None:
        from datetime import datetime

        rec = parse_form_1040_text(_pages_2023_with_feie())
        # Should parse without error
        dt = datetime.fromisoformat(rec.captured_at)
        assert dt.year >= 2026

    def test_pdf_creator_forwarded(self) -> None:
        rec = parse_form_1040_text(_pages_2023_with_feie(), pdf_creator="Intuit FPS Engine v4")
        assert rec.provenance["pdf_creator"] == "Intuit FPS Engine v4"

    def test_comma_formatted_agi(self) -> None:
        # 162,433 parsed correctly to float
        rec = parse_form_1040_text(_pages_2023_with_feie())
        assert rec.agi == 162_433.0


# ---------------------------------------------------------------------------
# TestParsedForm2024
# ---------------------------------------------------------------------------


class TestParsedForm2024:
    def test_agi_2024(self) -> None:
        rec = parse_form_1040_text(_pages_2024())
        assert rec.tax_year == 2024
        assert rec.agi == pytest.approx(200_000.0)

    def test_tax_exempt_interest_2024(self) -> None:
        rec = parse_form_1040_text(_pages_2024())
        assert rec.tax_exempt_interest == pytest.approx(3_000.0)

    def test_feie_zero_when_sch1_has_no_entry(self) -> None:
        # Schedule 1 present but no 8d line → feie = 0.0
        rec = parse_form_1040_text(_pages_2024())
        assert rec.feie == 0.0

    def test_magi_2024(self) -> None:
        rec = parse_form_1040_text(_pages_2024())
        assert rec.magi == pytest.approx(200_000.0 + 3_000.0 + 0.0)

    def test_year_2024_anchors_present(self) -> None:
        assert 2024 in ANCHORS


# ---------------------------------------------------------------------------
# TestMissingSchedule1
# ---------------------------------------------------------------------------


class TestMissingSchedule1:
    def test_feie_zero_no_error(self) -> None:
        rec = parse_form_1040_text(_pages_2023_no_sch1())
        assert rec.feie == 0.0

    def test_sch1_page_index_none(self) -> None:
        rec = parse_form_1040_text(_pages_2023_no_sch1())
        assert rec.provenance["sch1_page_index"] is None

    def test_other_fields_still_parsed(self) -> None:
        rec = parse_form_1040_text(_pages_2023_no_sch1())
        assert rec.agi == pytest.approx(162_433.0)
        assert rec.tax_exempt_interest == pytest.approx(2_511.0)


# ---------------------------------------------------------------------------
# TestParseErrors
# ---------------------------------------------------------------------------


class TestParseErrors:
    def test_no_form_1040_raises(self) -> None:
        with pytest.raises(Form1040ParseError, match="No 'Form 1040"):
            parse_form_1040_text(_pages_no_1040())

    def test_unsupported_year_raises(self) -> None:
        with pytest.raises(Form1040ParseError, match="2019.*not supported"):
            parse_form_1040_text(_pages_unsupported_year())

    def test_empty_pages_raises(self) -> None:
        with pytest.raises(Form1040ParseError):
            parse_form_1040_text([])

    def test_missing_required_agi_raises(self) -> None:
        # A 1040 page that has the year marker but is missing the AGI line
        broken = ["Form 1040 (2023)\n2a  Tax-exempt interest . .  2a  100\n"]
        with pytest.raises(Form1040ParseError, match="agi"):
            parse_form_1040_text(broken)


# ---------------------------------------------------------------------------
# TestToFromDict
# ---------------------------------------------------------------------------


class TestToFromDict:
    def _make_record(self) -> Form1040Record:
        return parse_form_1040_text(_pages_2023_with_feie())

    def test_round_trip_fields(self) -> None:
        rec = self._make_record()
        restored = Form1040Record.from_dict(rec.to_dict())
        assert restored.tax_year == rec.tax_year
        assert restored.agi == rec.agi
        assert restored.tax_exempt_interest == rec.tax_exempt_interest
        assert restored.feie == rec.feie
        assert restored.magi == rec.magi
        assert restored.taxable_ss == rec.taxable_ss
        assert restored.qualified_dividends == rec.qualified_dividends
        assert restored.ordinary_dividends == rec.ordinary_dividends
        assert restored.filing_status == rec.filing_status
        assert restored.captured_at == rec.captured_at
        assert restored.source == rec.source
        assert restored.parser_version == rec.parser_version

    def test_provenance_preserved(self) -> None:
        rec = self._make_record()
        restored = Form1040Record.from_dict(rec.to_dict())
        assert restored.provenance == rec.provenance

    def test_to_dict_is_json_serialisable(self) -> None:
        rec = self._make_record()
        # Should not raise
        serialised = json.dumps(rec.to_dict())
        assert '"tax_year": 2023' in serialised

    def test_from_dict_handles_missing_optional_keys(self) -> None:
        d: dict[str, Any] = {
            "tax_year": 2023,
            "agi": 100_000.0,
            "tax_exempt_interest": 0.0,
            "taxable_ss": 0.0,
            "qualified_dividends": 0.0,
            "ordinary_dividends": 0.0,
            "feie": 0.0,
            "magi": 100_000.0,
            "filing_status": None,
            "captured_at": "2026-06-09T12:00:00+00:00",
        }
        rec = Form1040Record.from_dict(d)
        assert rec.source == "pdf"
        assert rec.parser_version == "1.0.0"
        assert rec.provenance == {}


# ---------------------------------------------------------------------------
# TestCacheRoundTrip
# ---------------------------------------------------------------------------


class TestCacheRoundTrip:
    def test_save_and_load(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cache_file = tmp_path / "test_pdf_cache.json"
        monkeypatch.setattr("engine.tax_return_pdf._PDF_TAX_CACHE_PATH", cache_file)
        rec = parse_form_1040_text(_pages_2023_with_feie())
        save_pdf_tax_records({2023: rec})
        loaded = load_pdf_tax_records()
        assert 2023 in loaded
        assert loaded[2023].agi == rec.agi
        assert loaded[2023].magi == rec.magi
        assert loaded[2023].provenance == rec.provenance

    def test_load_missing_file_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "engine.tax_return_pdf._PDF_TAX_CACHE_PATH",
            tmp_path / "nonexistent.json",
        )
        assert load_pdf_tax_records() == {}

    def test_load_corrupt_file_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_file = tmp_path / "corrupt.json"
        cache_file.write_text("not valid json {{{")
        monkeypatch.setattr("engine.tax_return_pdf._PDF_TAX_CACHE_PATH", cache_file)
        assert load_pdf_tax_records() == {}

    def test_keys_stored_as_strings(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cache_file = tmp_path / "str_keys.json"
        monkeypatch.setattr("engine.tax_return_pdf._PDF_TAX_CACHE_PATH", cache_file)
        rec = parse_form_1040_text(_pages_2023_with_feie())
        save_pdf_tax_records({2023: rec})
        raw: dict[str, Any] = json.loads(cache_file.read_text())
        assert "2023" in raw
        assert isinstance(list(raw.keys())[0], str)

    def test_multiple_years_round_trip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache_file = tmp_path / "multi.json"
        monkeypatch.setattr("engine.tax_return_pdf._PDF_TAX_CACHE_PATH", cache_file)
        rec2023 = parse_form_1040_text(_pages_2023_with_feie())
        rec2024 = parse_form_1040_text(_pages_2024())
        save_pdf_tax_records({2023: rec2023, 2024: rec2024})
        loaded = load_pdf_tax_records()
        assert set(loaded.keys()) == {2023, 2024}
        assert loaded[2024].agi == pytest.approx(200_000.0)


# ---------------------------------------------------------------------------
# TestRealisticTurboTaxLineLayout
# Regression fixtures that mirror actual TurboTax PDF text layout:
#   <n>  <label> ......dots...... <n>  <value>
# The repeated line-number token must NOT be captured as the value.
# ---------------------------------------------------------------------------

# Realistic TurboTax 1040 page: every labeled line has the line number repeated
# after the dot leaders before the value, matching what pdfplumber extracts from
# a real TurboTax export.
_F1040_REALISTIC = """\
Department of the Treasury — Internal Revenue Service
Form 1040 (2023)         U.S. Individual Income Tax Return

2a  Tax-exempt interest ..........................  2a          4,200
    b  Taxable interest ..........................  2b          1,800
3a  Qualified dividends ..........................  3a          8,750
    b  Ordinary dividends ........................  3b         15,300
6   Social security benefits .....................  6b         18,000
11  Subtract line 10 from line 9. This is your adjusted gross income ......  11    287,654
"""

# Schedule 1 realistic: line label then dots then repeated line id then value.
_SCH1_REALISTIC = """\
SCHEDULE 1 (Form 1040)
Schedule 1  (Form 1040)   Additional Income and Adjustments

8d  Foreign earned income exclusion .............  8d          6,500
"""


def _pages_realistic() -> list[str]:
    """Bundle with realistic TurboTax dot-leader + repeated-line-number layout."""
    return [_F1040_REALISTIC, _SCH1_REALISTIC]


class TestRealisticTurboTaxLineLayout:
    """Guard against capturing the repeated line-number token instead of the value."""

    def test_agi_skips_repeated_11(self) -> None:
        # "...adjusted gross income ...... 11    287,654" — must NOT return 11
        rec = parse_form_1040_text(_pages_realistic())
        assert rec.agi == pytest.approx(287_654.0)

    def test_agi_not_captured_as_line_number(self) -> None:
        # Explicit guard: value must be far larger than any line number
        rec = parse_form_1040_text(_pages_realistic())
        assert rec.agi > 100, "AGI parsed as line number (11) instead of actual value"

    def test_tax_exempt_interest_realistic(self) -> None:
        rec = parse_form_1040_text(_pages_realistic())
        assert rec.tax_exempt_interest == pytest.approx(4_200.0)

    def test_qualified_dividends_realistic(self) -> None:
        rec = parse_form_1040_text(_pages_realistic())
        assert rec.qualified_dividends == pytest.approx(8_750.0)

    def test_ordinary_dividends_realistic(self) -> None:
        rec = parse_form_1040_text(_pages_realistic())
        assert rec.ordinary_dividends == pytest.approx(15_300.0)

    def test_taxable_ss_realistic(self) -> None:
        rec = parse_form_1040_text(_pages_realistic())
        assert rec.taxable_ss == pytest.approx(18_000.0)

    def test_feie_realistic(self) -> None:
        rec = parse_form_1040_text(_pages_realistic())
        assert rec.feie == pytest.approx(6_500.0)

    def test_magi_realistic(self) -> None:
        # MAGI = AGI + tax_exempt_interest + feie
        rec = parse_form_1040_text(_pages_realistic())
        assert rec.magi == pytest.approx(287_654.0 + 4_200.0 + 6_500.0)


# ---------------------------------------------------------------------------
# TestParseCurrency — security-2: negative / parenthesized AGI support
# ---------------------------------------------------------------------------


class TestParseCurrency:
    """Unit tests for _parse_currency sign/paren handling."""

    def test_positive(self) -> None:
        from engine.tax_return_pdf import _parse_currency

        assert _parse_currency("5,000") == pytest.approx(5000.0)

    def test_negative_dash(self) -> None:
        from engine.tax_return_pdf import _parse_currency

        assert _parse_currency("-5,000") == pytest.approx(-5000.0)

    def test_negative_parens(self) -> None:
        from engine.tax_return_pdf import _parse_currency

        assert _parse_currency("(5,000)") == pytest.approx(-5000.0)

    def test_dollar_sign_stripped(self) -> None:
        from engine.tax_return_pdf import _parse_currency

        assert _parse_currency("$1,234") == pytest.approx(1234.0)

    def test_trailing_dot_stripped(self) -> None:
        from engine.tax_return_pdf import _parse_currency

        assert _parse_currency("1000.") == pytest.approx(1000.0)
