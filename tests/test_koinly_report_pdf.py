"""Tests for engine.koinly_report_pdf -- Koinly crypto tax-report PDF parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.koinly_report_pdf import (
    INCOME_CATEGORIES,
    KoinlyParseError,
    KoinlyReport,
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

    def test_load_missing_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
