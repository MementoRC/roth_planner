"""The synthetic import fixtures in tests/fixtures/synthetic/ read end to end.

These are the files the README there tells you to upload in the browser build
to drive the import checks (Holdings, Import Statements queue, option-grid
refill, Backup & Transfer, Command Center card). Each PDF goes through the
real pdfplumber extraction and content classifier here, so a fixture that
stops parsing -- or a parser that stops reading a layout it used to -- fails
in CI, not in a manual browser pass. Every value is invented.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from engine.bridge_bundle import classify_bundle, parse_v5
from engine.bridge_import import LocalState, apply_import, plan_import
from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.data_bridge_crypto import open_uploaded_payload
from engine.ira_statement_balance import apply_ira_statement, is_ira_record
from engine.netbenefits_statement_pdf import apply_netbenefits_statement
from engine.pdf_import import (
    DocKind,
    PdfImportResult,
    classify_pdf_text,
    extract_pages,
    scan_pdf_documents,
)
from engine.portfolio_sync.shapes import PortfolioSnapshot

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("synthetic_generate", FIXTURES / "generate.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


def _scan(name: str) -> PdfImportResult:
    result = scan_pdf_documents([(name, (FIXTURES / name).read_bytes())])
    assert result.errors == []
    assert result.unrecognized == []
    return result


def _broker_record(name: str) -> BrokerageStatementRecord:
    [rec] = _scan(name).brokerage_records
    return rec


class TestFixturesAreCurrent:
    """The committed files are exactly what generate.py writes."""

    @pytest.mark.parametrize("name", sorted(gen.PDF_FIXTURES))
    def test_pdf_matches_generator(self, name: str) -> None:
        assert (FIXTURES / name).read_bytes() == gen.build_pdf(gen.PDF_FIXTURES[name])

    def test_bundle_matches_generator(self) -> None:
        assert (FIXTURES / gen.BUNDLE_NAME).read_bytes() == gen.bundle_bytes()

    @pytest.mark.parametrize("name", sorted(gen.PDF_FIXTURES))
    def test_pdf_text_survives_extraction(self, name: str) -> None:
        # Words stay separated and every page comes back (12pt Helvetica keeps
        # the space glyph wider than pdfplumber's x-tolerance).
        pages, _creator = extract_pages((FIXTURES / name).read_bytes())
        assert pages == gen.PDF_FIXTURES[name]


class TestStatementPdfs:
    def test_vanguard_roth_ira(self) -> None:
        rec = _broker_record("vanguard_roth_ira_2026q2.pdf")
        assert (rec.broker, rec.account_type, rec.account_number) == (
            "vanguard",
            "roth_ira",
            "XXXX0001",
        )
        assert rec.ending_value == 50_000.00
        assert rec.statement_period_end == "2026-06-30"
        assert rec.owner_key == "Pat Example"
        assert rec.missing_fields == ()

    def test_fidelity_traditional_ira(self) -> None:
        rec = _broker_record("fidelity_traditional_ira_2026q2.pdf")
        assert (rec.broker, rec.account_type, rec.account_number) == (
            "fidelity",
            "traditional_ira",
            "999-000001",
        )
        assert rec.ending_value == 400_000.00
        assert rec.statement_period_end == "2026-06-30"
        assert rec.owner_key == "PAT EXAMPLE"
        assert rec.missing_fields == ()

    def test_ibkr_traditional_ira(self) -> None:
        rec = _broker_record("ibkr_traditional_ira_2026q2.pdf")
        assert (rec.broker, rec.account_type, rec.account_number) == (
            "ibkr",
            "traditional_ira",
            "U00000001",
        )
        assert rec.ending_value == 100_000.00
        assert rec.dividends_taxable_ytd == 400.00
        assert rec.statement_period_end == "2026-06-30"
        assert rec.missing_fields == ()

    def test_fidelity_taxable_prior_year(self) -> None:
        rec = _broker_record("fidelity_taxable_2025.pdf")
        assert (rec.broker, rec.account_type, rec.account_number) == (
            "fidelity",
            "taxable",
            "Z12-345678",
        )
        assert rec.statement_period_end == "2025-12-31"
        assert rec.dividends_taxable_ytd == 3_000.00
        assert rec.dividends_tax_exempt_ytd == 500.00
        assert rec.owner_key == "PAT EXAMPLE"

    def test_netbenefits_403b(self) -> None:
        [stmt] = _scan("netbenefits_403b_2026h1.pdf").netbenefits_statements
        assert stmt.owner_key == "SAM EXAMPLE"
        assert stmt.plan_name == "Example College 403b"
        assert stmt.period_end == "2026-06-30"
        assert stmt.ending_balance == pytest.approx(100_000.00)
        assert stmt.warnings == []
        assert sum(h.value for h in stmt.holdings) == pytest.approx(100_000.00)
        assert sum(stmt.source_balances.values()) == pytest.approx(100_000.00)

    def test_ssa_statement(self) -> None:
        [stmt] = _scan("ssa_statement_2026.pdf").ssa_statements
        assert stmt.owner_key == "PAT EXAMPLE"
        assert stmt.statement_date == "2026-03-02"
        assert stmt.fra_age == 67
        assert stmt.estimates[67] == 2_000.0
        assert sorted(stmt.estimates) == list(range(62, 71))

    def test_form_1040(self) -> None:
        records = _scan("form_1040_2025.pdf").form_1040_records
        rec = records[2025]
        assert rec.agi == 150_000.0
        assert rec.tax_exempt_interest == 1_000.0
        assert rec.magi == 151_000.0

    @pytest.mark.parametrize(
        ("name", "kind"),
        [
            ("vanguard_roth_ira_2026q2.pdf", DocKind.BROKERAGE),
            ("fidelity_traditional_ira_2026q2.pdf", DocKind.BROKERAGE),
            ("ibkr_traditional_ira_2026q2.pdf", DocKind.BROKERAGE),
            ("fidelity_taxable_2025.pdf", DocKind.BROKERAGE),
            ("netbenefits_403b_2026h1.pdf", DocKind.NETBENEFITS),
            ("ssa_statement_2026.pdf", DocKind.SSA_STATEMENT),
            ("form_1040_2025.pdf", DocKind.FORM_1040),
        ],
    )
    def test_each_pdf_classifies_as_its_own_kind(self, name: str, kind: DocKind) -> None:
        pages, _creator = extract_pages((FIXTURES / name).read_bytes())
        assert classify_pdf_text(pages) is kind


class TestHoldingsFromStatements:
    """Applying the statement PDFs builds the Holdings the browser check expects."""

    def test_ira_and_403b_statements_set_each_owner_total(self) -> None:
        snap = PortfolioSnapshot()
        for name in (
            "fidelity_traditional_ira_2026q2.pdf",
            "ibkr_traditional_ira_2026q2.pdf",
            "vanguard_roth_ira_2026q2.pdf",
        ):
            rec = _broker_record(name)
            assert is_ira_record(rec)
            snap = apply_ira_statement(snap, rec, "you").snapshot
        [stmt] = _scan("netbenefits_403b_2026h1.pdf").netbenefits_statements
        snap = apply_netbenefits_statement(snap, stmt, "spouse").snapshot

        def total(owner: str, *, roth: bool) -> float:
            return sum(
                a.total_value
                for a in snap.accounts
                if a.owner == owner and (a.is_roth if roth else a.is_pretax)
            )

        assert len(snap.accounts) == 4
        assert total("you", roth=False) == pytest.approx(500_000.00)
        assert total("you", roth=True) == pytest.approx(50_000.00)
        assert total("spouse", roth=False) == pytest.approx(100_000.00)


class TestBackupBundle:
    def _privkey(self) -> bytes:
        return base64.b64decode(gen.TEST_PRIVKEY_B64)

    def test_enc_decrypts_to_the_plain_bundle(self) -> None:
        plaintext = open_uploaded_payload(
            (FIXTURES / gen.BUNDLE_ENC_NAME).read_bytes(), self._privkey()
        )
        assert plaintext == (FIXTURES / gen.BUNDLE_NAME).read_bytes()

    def test_readme_shows_the_test_key(self) -> None:
        assert gen.TEST_PRIVKEY_B64 in (FIXTURES / "README.md").read_text()

    def test_plain_json_upload_passes_through(self) -> None:
        raw = (FIXTURES / gen.BUNDLE_NAME).read_bytes()
        assert open_uploaded_payload(raw, None) == raw

    def test_import_round_trip_restores_accounts_grants_and_ytd(self) -> None:
        raw = json.loads(
            open_uploaded_payload((FIXTURES / gen.BUNDLE_ENC_NAME).read_bytes(), self._privkey())
        )
        assert classify_bundle(raw) == "v5"
        bundle = parse_v5(raw)
        local = LocalState()
        plan = plan_import(bundle, local)
        assert set(plan.slots) == {"you", "spouse"}
        assert plan.slots["you"].grants_replaced

        result = apply_import(bundle, plan, local, selected=frozenset(plan.slots), operator="you")

        accounts = {(a.owner, a.account_name): a.total_value for a in result.snapshot.accounts}
        assert accounts == {
            ("you", "Example Traditional IRA"): 400_000.0,
            ("you", "Example Roth IRA"): 50_000.0,
            ("spouse", "Example Spouse IRA"): 300_000.0,
            ("household", "Example Joint Brokerage"): 200_000.0,
        }
        # The grants are what refill the option-exercise grid after an import.
        assert [g.grant_id for g in result.snapshot.equity_grants] == ["ACME-2019", "ACME-2020"]
        assert result.scalar_updates["_user_grant_strikes"] == {"2019": 50.0, "2020": 60.0}
        assert result.scalar_updates["your_ira"] == 400_000
        assert result.ytd is not None
        assert result.ytd.by_owner["you"]["wages_ytd"] == 50_000.0
        assert result.ytd.by_owner["spouse"]["wages_ytd"] == 40_000.0
