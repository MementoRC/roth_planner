"""Tests for the SSA "Your Social Security Statement" (Form SSA-7005) parser.

The fixture below is SYNTHETIC: the layout copies page 1 of an SSA statement
as pdfplumber extracts it -- including the rotated "Age / Retirement Benefits
Start" column heading, which comes out as reversed words (``egA``,
``tnemeriteR`` ...) -- but the name, date of birth and every amount are
invented. No SSN appears anywhere.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from engine.ira import ss_benefit_at_age
from engine.pdf_import import DocKind, PdfImportResult, _dispatch_pdf, classify_pdf_text
from engine.ssa_statement_pdf import (
    SsaStatementParseError,
    benefit_comparison,
    default_ssa_owner,
    is_ssa_statement,
    load_ssa_pdf_estimates,
    parse_ssa_statement_text,
    pick_latest_ssa,
    save_ssa_pdf_estimates,
    statement_age_notice,
)
from engine.store.namespaces import SSA_PDF
from tests.test_netbenefits_statement_pdf import FIDELITY_BROKERAGE_PAGE
from tests.test_netbenefits_statement_pdf import PAGES as NETBENEFITS_PAGES

PAGE_1 = """Your Social Security Statement
JOHN Q EXAMPLE March 4, 2026
Personalized Monthly Retirement Benefit Estimates
Retirement Benefits
(Depending on the Age You Start)
You have earned enough credits to qualify for retirement
benefits.
Your full retirement age is 67, based on your date of
birth: January 2, 1970. As shown in the chart, you can
start your benefits at any time between ages 62 and
70.
These personalized estimates are based on your
earnings to date and assume you continue to earn
$50,000 per year until you start your benefits.
Disability Benefits
be about $2,000. Learn more at ssa.gov/disability.
Survivors Benefits
Minor child: $1,500
Spouse, if benefits start at full retirement age: $2,000
Total family benefits cannot be more than: $3,500
tratS
stifeneB
tnemeriteR
egA
62 $1,400
63 $1,500
64 $1,600
65 $1,730
66 $1,860
67 $2,000
68 $2,160
69 $2,330
70 $2,500
Monthly Benefit Amount
Medicare
You have enough credits to qualify for Medicare at age
65."""

PAGE_2 = """Earnings Record Important Things to Know about Your Social
Work Year Social Security (began 1966)
2023 $40,000 $40,000 fairness-act.html .
2024 $45,000 $45,000
2025 $50,000 $50,000
Taxes Paid
You paid: $10,000 You paid: $2,000
Form SSA-7005-SM-OL (04/25) | Enclosures: Publication No. 05-10706"""

PAGES = [PAGE_1, PAGE_2]

ESTIMATES = {
    62: 1400,
    63: 1500,
    64: 1600,
    65: 1730,
    66: 1860,
    67: 2000,
    68: 2160,
    69: 2330,
    70: 2500,
}


class TestDetection:
    def test_recognizes_an_ssa_statement(self) -> None:
        assert is_ssa_statement("\n".join(PAGES))
        assert classify_pdf_text(PAGES) is DocKind.SSA_STATEMENT

    def test_needs_both_markers(self) -> None:
        assert not is_ssa_statement(PAGE_1)  # no Form SSA-7005 footer
        assert not is_ssa_statement(PAGE_2)  # no statement title

    def test_other_documents_route_as_before(self) -> None:
        assert classify_pdf_text(NETBENEFITS_PAGES) is DocKind.NETBENEFITS
        assert classify_pdf_text([FIDELITY_BROKERAGE_PAGE]) is DocKind.BROKERAGE
        # A 1040 can mention Social Security benefits; it must stay a 1040.
        form_1040 = [
            "Form 1040 (2025)\nSocial security benefits 6a\n"
            "Your Social Security Statement was not attached"
        ]
        assert classify_pdf_text(form_1040) is not DocKind.SSA_STATEMENT


class TestParse:
    def test_parses_holder_date_fra_and_estimates(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        assert stmt.owner_key == "JOHN Q EXAMPLE"
        assert stmt.statement_date == "2026-03-04"
        assert stmt.fra_age == 67
        assert stmt.estimates == ESTIMATES
        assert stmt.fra_monthly == 2000

    def test_reversed_rotated_heading_is_ignored(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        assert set(stmt.estimates) == set(range(62, 71))

    def test_ignores_the_earnings_record_and_survivor_figures(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        # Earnings rows ("2024 $45,000 ...") and survivor/disability amounts
        # must not leak into the per-age table.
        assert all(62 <= age <= 70 for age in stmt.estimates)
        assert not hasattr(stmt, "earnings")

    def test_missing_estimates_raises(self) -> None:
        stripped = [PAGE_1.split("tratS")[0], PAGE_2]
        with pytest.raises(SsaStatementParseError):
            parse_ssa_statement_text(stripped)

    def test_missing_fra_row_raises(self) -> None:
        no_67 = [PAGE_1.replace("67 $2,000\n", ""), PAGE_2]
        with pytest.raises(SsaStatementParseError):
            parse_ssa_statement_text(no_67)

    def test_fra_with_months_derives_the_fra_amount_from_the_whole_year_row(self) -> None:
        # audit-1018 F-17: the age-66 row is a claim 10 months early.
        pages = [PAGE_1.replace("age is 67,", "age is 66 and 10 months,"), PAGE_2]
        stmt = parse_ssa_statement_text(pages)
        assert stmt.fra_age == 66
        assert stmt.fra_months == 10
        assert stmt.fra_monthly == pytest.approx(1860 / (1 - 10 * 5 / 900))

    def test_missing_holder_line_is_none_not_a_guess(self) -> None:
        pages = [PAGE_1.replace("JOHN Q EXAMPLE March 4, 2026", "March 4, 2026"), PAGE_2]
        stmt = parse_ssa_statement_text(pages)
        assert stmt.owner_key is None
        assert stmt.statement_date == "2026-03-04"

    def test_dispatch_collects_the_statement(self) -> None:
        result = PdfImportResult()
        _dispatch_pdf(DocKind.SSA_STATEMENT, "ssa.pdf", PAGES, None, result)
        [stmt] = result.ssa_statements
        assert stmt.fra_monthly == 2000
        assert result.errors == []


class TestStatementAge:
    def test_recent_statement_has_no_notice(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        assert statement_age_notice(stmt, today=date(2026, 10, 1)) is None

    def test_statement_over_a_year_old_gets_a_notice(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        notice = statement_age_notice(stmt, today=date(2027, 6, 1))
        assert notice is not None
        assert "2026-03-04" in notice


class TestComparison:
    def test_rows_put_ssa_next_to_the_planner_formula(self) -> None:
        rows = benefit_comparison(ESTIMATES, fra_monthly=2000, fra_age=67)
        assert [r.age for r in rows] == list(range(62, 71))
        by_age = {r.age: r for r in rows}
        assert by_age[67].ssa_monthly == 2000
        assert by_age[67].planner_monthly == pytest.approx(2000)
        assert by_age[70].planner_monthly == pytest.approx(ss_benefit_at_age(2000, 70, 67) / 12)
        assert by_age[70].difference == pytest.approx(2500 - by_age[70].planner_monthly)

    def test_planner_side_follows_the_planners_own_fra_amount(self) -> None:
        # The comparison never feeds the model: it shows what the planner
        # would use (its own FRA figure) next to SSA's printed number.
        rows = benefit_comparison(ESTIMATES, fra_monthly=1900, fra_age=67)
        assert {r.age: r for r in rows}[67].planner_monthly == pytest.approx(1900)


class TestOwnerAndLatest:
    def test_owner_from_learned_holder_name(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        assert default_ssa_owner(stmt, {"john q example": "spouse"}, "you") == "spouse"

    def test_owner_falls_back_to_instance_owner_then_you(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        assert default_ssa_owner(stmt, {}, "spouse") == "spouse"
        assert default_ssa_owner(stmt, {}, "household") == "you"

    def test_newest_statement_per_holder_wins(self) -> None:
        older = parse_ssa_statement_text([PAGE_1.replace("March 4, 2026", "May 1, 2025"), PAGE_2])
        newer = parse_ssa_statement_text(PAGES)
        assert [s.statement_date for s in pick_latest_ssa([newer, older])] == ["2026-03-04"]


class TestPersistence:
    def test_round_trip_per_owner(self) -> None:
        stmt = parse_ssa_statement_text(PAGES)
        save_ssa_pdf_estimates("spouse", stmt)

        loaded = load_ssa_pdf_estimates("spouse")
        assert loaded is not None
        assert loaded.statement_date == "2026-03-04"
        assert loaded.fra_age == 67
        assert loaded.estimates == ESTIMATES
        assert load_ssa_pdf_estimates("you") is None

    def test_missing_or_corrupt_row_is_none(self, corrupt_row) -> None:
        assert load_ssa_pdf_estimates("you") is None
        corrupt_row(SSA_PDF)
        assert load_ssa_pdf_estimates("you") is None

    def test_saved_document_holds_no_holder_name(self, store) -> None:
        save_ssa_pdf_estimates("you", parse_ssa_statement_text(PAGES))
        assert "EXAMPLE" not in json.dumps(store.get(SSA_PDF))
