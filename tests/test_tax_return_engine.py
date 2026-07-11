"""Tests for tax return engine consumers (parsing + Form 8606 not modeled)."""

import pytest

from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from engine.tax import (
    deductions,
)
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestTaxReturnParsing:
    """Test parsing of TurboTax income/deduction rows from FinExtract."""

    def test_parse_income_rows(self):
        from engine.portfolio_sync import _parse_tax_rows

        rows = [
            {
                "form_label": "Wages and Salaries (W-2)",
                "amount_current": 102225,
                "amount_prior": 118161,
            },
            {"form_label": "Form 1099-NEC", "amount_current": 4150, "amount_prior": None},
            {
                "form_label": "Investments and Savings",
                "amount_current": 92429,
                "amount_prior": 165861,
            },
            {
                "form_label": "IRA, 401(k), Pension Plan Withdrawals (1099-R)",
                "amount_current": 7397,
                "amount_prior": None,
            },
            {"form_label": "1099-SA, HSA, MSA", "amount_current": 895, "amount_prior": 583},
            {
                "form_label": "Miscellaneous Income, 1099-A, 1099-C",
                "amount_current": None,
                "amount_prior": 48401,
            },
        ]
        parsed = _parse_tax_rows(rows, "amount_current")
        assert parsed["wages"] == 102225
        assert parsed["nec_income"] == 4150
        assert parsed["investment_income"] == 92429
        assert parsed["ira_distributions"] == 7397
        assert parsed["hsa_distributions"] == 895
        assert "misc_income" not in parsed  # amount_current is None

    def test_parse_deduction_rows(self):
        from engine.portfolio_sync import _parse_tax_rows

        rows = [
            {"form_label": "HSA, MSA Contributions", "amount_current": 5300, "amount_prior": 5150},
            {
                "form_label": "Traditional and Roth IRA Contributions",
                "amount_current": 8000,
                "amount_prior": 16000,
            },
            {"form_label": "Sales Tax", "amount_current": 1686, "amount_prior": 1881},
            {"form_label": "Foreign Tax Credit", "amount_current": 365, "amount_prior": 355},
        ]
        parsed = _parse_tax_rows(rows, "amount_current")
        assert parsed["hsa_contributions"] == 5300
        assert parsed["ira_contributions"] == 8000
        assert parsed["sales_tax"] == 1686
        assert parsed["foreign_tax_credit"] == 365

    def test_parse_prior_year(self):
        from engine.portfolio_sync import _parse_tax_rows

        rows = [
            {
                "form_label": "Wages and Salaries (W-2)",
                "amount_current": 102225,
                "amount_prior": 118161,
            },
            {
                "form_label": "Investments and Savings",
                "amount_current": 92429,
                "amount_prior": 165861,
            },
        ]
        parsed = _parse_tax_rows(rows, "amount_prior")
        assert parsed["wages"] == 118161
        assert parsed["investment_income"] == 165861

    def test_tax_snapshot_estimated_magi(self):
        from engine.portfolio_sync import TaxReturnSnapshot

        snap = TaxReturnSnapshot(
            wages=102225,
            nec_income=4150,
            investment_income=92429,
            ira_distributions=7397,
            hsa_distributions=895,
            hsa_contributions=5300,
        )
        # total_income = 102225 + 4150 + 92429 + 7397 + 895 = 207096
        assert snap.total_income == 207096
        # SE deduction = NEC × 0.9235 × 15.3% / 2 = NEC × 0.07065 (employer-equiv half)
        # NOT 0.0765 (employee FICA rate) — see IRC §164(f) + §1402(a)
        se_ded = 4150 * 0.07065
        expected = 207096 - 5300 - se_ded
        assert snap.estimated_magi == pytest.approx(expected, abs=1)

    def test_se_deduction_rate_is_employer_equivalent_half(self):
        """Regression: SE deduction must use 7.065% (0.9235 × 15.3% / 2), not 7.65%."""
        from engine.portfolio_sync import TaxReturnSnapshot

        snap = TaxReturnSnapshot(nec_income=10_000)
        # Correct: 10_000 × 0.07065 = 706.50
        # Wrong:   10_000 × 0.0765  = 765.00
        # estimated_magi = total_income - 0 (no HSA) - se_deduction
        expected_magi = snap.total_income - 10_000 * 0.07065
        assert snap.estimated_magi == pytest.approx(expected_magi, abs=0.01)
        # Pin the absolute value so any future constant drift is caught
        assert snap.estimated_magi == pytest.approx(10_000 - 706.50, abs=0.01)

    def test_tax_snapshot_save_load_roundtrip(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import TaxReturnSnapshot, load_tax_snapshot, save_tax_snapshot

        monkeypatch.setattr(portfolio_sync, "_TAX_CACHE_PATH", tmp_path / "tax.json")

        snap = TaxReturnSnapshot(
            wages=100_000,
            investment_income=50_000,
            hsa_contributions=5_000,
            server_available=True,
        )
        save_tax_snapshot(snap)
        loaded = load_tax_snapshot()
        assert loaded is not None
        assert loaded.wages == 100_000
        assert loaded.investment_income == 50_000
        assert loaded.hsa_contributions == 5_000
        assert loaded.server_available is True

    def test_hsa_1099sa_contribution_label_routes_to_deduction(self):
        # H1 operator-precedence fix: "1099-sa" in label AND "contribution" in label
        # must NOT match hsa_distributions (the `contribution` guard must cover both
        # disjuncts).  Pre-fix: `"1099-sa" in label_lower` short-circuits the `and`
        # → matched hsa_distributions.  Post-fix: parenthesised → blocked correctly.
        from engine.portfolio_sync import _parse_tax_rows

        rows = [
            {"form_label": "Form 1099-SA HSA contribution", "amount_current": 750, "amount_prior": 0},
        ]
        parsed = _parse_tax_rows(rows, "amount_current")
        assert "hsa_distributions" not in parsed
        assert parsed["hsa_contributions"] == 750

    def test_fetch_tax_return_preserves_prior_values_when_no_new_data(self, monkeypatch):
        """Regression: re-syncing must MERGE, not replace. Fields FinExtract does
        not return this time must keep their previously-loaded values, not zero out.
        This is the 'Sync TurboTax data erases already-loaded data' bug."""
        from engine.portfolio_sync import TaxReturnSnapshot, fetch_tax_return
        from engine.portfolio_sync import tax_return as tr

        class _FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {}

        monkeypatch.setattr(tr, "_get", lambda *a, **k: _FakeResp())
        monkeypatch.setattr(tr, "_flatten_query_rows", lambda payload: [])

        previous = TaxReturnSnapshot(
            wages=150_000,
            investment_income=20_000,
            ira_contributions=14_000,
            server_available=True,
        )
        result = fetch_tax_return(previous)
        assert result.server_available is True
        # None of these were returned by the (empty) FinExtract response, so they
        # must survive the sync instead of being wiped to 0.
        assert result.wages == 150_000
        assert result.investment_income == 20_000
        assert result.ira_contributions == 14_000

    def test_fetch_tax_return_overwrites_returned_fields_keeps_others(self, monkeypatch):
        """A field FinExtract DOES return is updated; unreturned fields are kept."""
        from engine.portfolio_sync import TaxReturnSnapshot, fetch_tax_return
        from engine.portfolio_sync import tax_return as tr

        class _FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {}

        monkeypatch.setattr(tr, "_get", lambda *a, **k: _FakeResp())

        # _flatten_query_rows is called twice (income, then deductions). Return the
        # wages row only on the first call so _parse_tax_rows doesn't double-count.
        calls = {"n": 0}

        def _fake_flatten(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                return [
                    {
                        "form_label": "Wages and Salaries (W-2)",
                        "amount_current": 175_000,
                        "amount_prior": 0,
                    }
                ]
            return []

        monkeypatch.setattr(tr, "_flatten_query_rows", _fake_flatten)

        previous = TaxReturnSnapshot(
            wages=150_000,
            investment_income=20_000,
            server_available=True,
        )
        result = fetch_tax_return(previous)
        assert result.wages == 175_000  # freshly returned value overwrites
        assert result.investment_income == 20_000  # not returned this sync -> preserved


class TestForm8606NotModeled:
    """Document and lock the 100%-pretax conversion assumption (no Form 8606 basis).

    Per IRC §408(d)(2), if a taxpayer has both pretax and after-tax (basis) dollars
    in a Traditional IRA, every distribution is pro-rated:
        taxable_fraction = pretax_balance / (pretax_balance + basis)

    This engine assumes basis = $0, so every converted dollar is fully taxable.
    See engine/scenario.py 'NOT MODELED: IRA non-deductible basis (Form 8606)' comment.
    """

    def test_conversion_treated_as_fully_pretax(self):
        """Conversions are 100% taxable as ordinary income (no Form 8606 basis pro-rata).

        A $50K conversion with zero other income must produce taxable_income equal to
        $50K minus the standard deduction — i.e., the full conversion amount enters
        the tax base. No basis reduction is applied.
        """
        from dataclasses import replace

        hh = replace(
            Household(grants=[]),
            your_age=61,
            spouse_age=55,
            your_ira=500_000.0,
            spouse_ira=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )
        conversion_amount = 50_000.0
        plan = ConversionPlan(your_conversions={hh.base_year: conversion_amount})
        result = run_scenario(hh, plan, "form8606_pretax", end_age=62)
        yr = result.years[0]

        # The full conversion amount must appear in combined_gross (no basis haircut)
        assert yr.your_conversion == pytest.approx(conversion_amount)
        # Taxable income = conversion_amount - standard deduction (no other income here)
        ded = deductions(hh.your_age, hh.spouse_age, hh.std_deduction, hh.senior_extra)
        expected_taxable = max(conversion_amount - ded, 0.0)
        assert yr.taxable_income == pytest.approx(expected_taxable, rel=1e-6), (
            "Full conversion amount must be taxable — Form 8606 basis pro-rata is not modeled. "
            f"Got taxable_income={yr.taxable_income:,.2f}, expected={expected_taxable:,.2f}"
        )


# ============================================================
#  View-layer filing_status threading regression tests (PR sweeps views)
# ============================================================
