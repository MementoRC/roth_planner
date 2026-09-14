"""Regression tests for PDF document-kind routing (classify_pdf_text).

Covers the misrouting bug where a TurboTax Form 1040 export that merely
mentions "Koinly" (e.g. as a crypto-import source line) was classified as a
Koinly crypto tax report instead of a Form 1040, because is_koinly_report was
a bare substring match on "koinly" and ran before is_form_1040.
"""

from __future__ import annotations

from engine.pdf_import import DocKind, classify_pdf_text

FORM_1040_FOOTER_2025 = "Form 1040 (2025)"
KOINLY_TAX_YEAR_2025 = "TAX YEAR 2025"


def test_1040_with_koinly_mention_classifies_as_form_1040() -> None:
    """The user's actual failure: a real 1040 that echoes 'Koinly' as an
    import source must still route to FORM_1040, not KOINLY."""
    pages = [
        "Wages, salaries, tips ... 1 100000\n"
        "Imported from Koinly crypto CSV export\n"
        f"{FORM_1040_FOOTER_2025}"
    ]
    assert classify_pdf_text(pages) == DocKind.FORM_1040


def test_bare_koinly_mention_without_tax_year_is_not_koinly() -> None:
    """'koinly' alone, with no TAX YEAR marker and no 1040 footer, must not
    be classified as a Koinly report -- the detector requires at least what
    the parser requires."""
    pages = ["Some unrelated document that happens to mention koinly once."]
    assert classify_pdf_text(pages) != DocKind.KOINLY


def test_genuine_koinly_report_still_classifies_as_koinly() -> None:
    """Regression guard: a real Koinly report (vendor name + TAX YEAR
    marker) must still classify as KOINLY."""
    pages = [f"Koinly Complete Tax Report\n{KOINLY_TAX_YEAR_2025}\nCapital gains summary"]
    assert classify_pdf_text(pages) == DocKind.KOINLY


def test_genuine_1040_without_koinly_still_classifies_as_form_1040() -> None:
    """Regression guard: a real 1040 with no koinly mention still routes
    correctly."""
    pages = [f"Wages, salaries, tips ... 1 100000\n{FORM_1040_FOOTER_2025}"]
    assert classify_pdf_text(pages) == DocKind.FORM_1040
