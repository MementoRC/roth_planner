"""Tests for YTD snapshot wiring — fetch, dividend split, MAGI, federal tax estimate."""

import pytest

from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from engine.tax import (
    federal_tax,
)
from models.household import Household
from models.ytd_income import IncomeEvent, sum_income_events


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestYTDSnapshot:
    """Test YTD income data model properties."""

    def test_ltcg_not_in_ordinary(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(ltcg_ytd=200_000, stcg_ytd=10_000, wages_ytd=50_000)
        # LTCG should NOT be in ordinary income
        assert ytd.total_ordinary_income == approx(60_000)  # wages + stcg only
        # But should be in MAGI
        assert ytd.magi_ytd == approx(260_000)

    def test_stcg_in_ordinary(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(stcg_ytd=30_000)
        assert ytd.total_ordinary_income == approx(30_000)

    def test_magi_includes_all(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            wages_ytd=100_000,
            ltcg_ytd=200_000,
            stcg_ytd=10_000,
            ordinary_dividends_ytd=5_000,
            interest_ytd=3_000,
            ira_conversions_ytd=20_000,
        )
        expected = 100_000 + 200_000 + 10_000 + 5_000 + 3_000 + 20_000
        assert ytd.magi_ytd == approx(expected)

    def test_investment_income_for_niit(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            ltcg_ytd=150_000,
            stcg_ytd=20_000,
            ordinary_dividends_ytd=10_000,
            interest_ytd=5_000,
            wages_ytd=80_000,
        )
        # Investment income: LTCG + STCG + dividends + interest (no wages)
        assert ytd.total_investment_income == approx(185_000)

    def test_gain_event_properties(self):
        from models.ytd_income import RealizedGainEvent

        event = RealizedGainEvent(
            date="2026-03-15",
            description="TXN stop-loss",
            proceeds=250_000,
            cost_basis=150_000,
            holding_period="long",
            account_name="Schwab Brokerage",
        )
        assert event.gain_loss == approx(100_000)
        assert event.is_ltcg is True

        short_event = RealizedGainEvent(
            date="2026-03-15",
            description="AAPL sale",
            proceeds=50_000,
            cost_basis=45_000,
            holding_period="short",
        )
        assert short_event.gain_loss == approx(5_000)
        assert short_event.is_ltcg is False

    def test_total_ordinary_income_includes_ordinary_dividends_not_qualified(self):
        """Ordinary dividends are taxed as ordinary income; qualified are LTCG-rate only."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            wages_ytd=50_000,
            ordinary_dividends_ytd=3_000,
            qualified_dividends_ytd=2_000,
        )
        # ordinary income = wages + ordinary_dividends; qualified excluded
        assert ytd.total_ordinary_income == approx(53_000)
        # sum property still works
        assert ytd.dividends_ytd == approx(5_000)

    def test_total_ordinary_income_includes_interest(self):
        """Interest is fully ordinary income and must be included in total_ordinary_income."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=50_000, interest_ytd=3_000)
        assert ytd.total_ordinary_income == approx(53_000)

    def test_ltcg_stack_walk_uses_interest_inclusive_base(self):
        """Regression: interest_ytd shifts the LTCG bracket boundary AFTER std-ded subtraction.

        The fix to estimate_ytd_federal_tax subtracts the standard deduction from ordinary
        income before stack-walking LTCG brackets.  interest_ytd must therefore be included
        in the pre-deduction ordinary base so it survives the subtraction and still pushes
        taxable_ordinary above the 0%-LTCG threshold.

        Arithmetic (both spouses <65, STD_DEDUCTION_MFJ=32_200, LTCG_THRESHOLDS_MFJ[0]=98_900):
          wages chosen so that:
            without interest: taxable_ordinary = wages - STD_DEDUCTION_MFJ = threshold - 5_000
            with    interest: taxable_ordinary = wages + interest - STD_DEDUCTION_MFJ = threshold + 5_000

          With interest: ltcg_start=threshold+5_000, ltcg_end=threshold+25_000
            ltcg_at_15 = (threshold+25_000) - (threshold+5_000) = 20_000  → tax = 3_000
          Without interest: ltcg_start=threshold-5_000, ltcg_end=threshold+15_000
            ltcg_at_15 = (threshold+15_000) - threshold = 15_000  → tax = 2_250

        The interest is the discriminator: it pushes more LTCG out of the 0%-band.
        """
        from engine.tax import LTCG_THRESHOLDS_MFJ, STD_DEDUCTION_MFJ, estimate_ytd_federal_tax
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        hh = Household(your_age=61, spouse_age=55, your_ira=500_000, spouse_ira=500_000)

        # wages chosen so taxable_ordinary without interest = threshold - 5_000
        wages = STD_DEDUCTION_MFJ + LTCG_THRESHOLDS_MFJ[0] - 5_000
        interest = 10_000.0
        ltcg = 20_000.0

        # --- with interest ---
        # taxable_ordinary = wages + interest - STD_DEDUCTION_MFJ = LTCG_THRESHOLDS_MFJ[0] + 5_000
        # ltcg_start = threshold+5_000, ltcg_end = threshold+25_000
        # ltcg_at_15 = 20_000 → tax = 3_000
        ytd = YTDSnapshot(wages_ytd=wages, interest_ytd=interest, ltcg_ytd=ltcg)
        result = estimate_ytd_federal_tax(ytd, hh)
        assert result.ltcg_tax == approx(ltcg * 0.15)

        # --- anchor: without interest ---
        # taxable_ordinary = wages - STD_DEDUCTION_MFJ = LTCG_THRESHOLDS_MFJ[0] - 5_000
        # ltcg_start = threshold-5_000, ltcg_end = threshold+15_000
        # ltcg_at_15 = (threshold+15_000) - threshold = 15_000 → tax = 2_250
        ytd_no_interest = YTDSnapshot(wages_ytd=wages, ltcg_ytd=ltcg)
        result_no_interest = estimate_ytd_federal_tax(ytd_no_interest, hh)
        taxable_ord_no_int = wages - STD_DEDUCTION_MFJ
        expected_ltcg_at_15_no_int = (taxable_ord_no_int + ltcg - LTCG_THRESHOLDS_MFJ[0]) * 0.15
        assert result_no_interest.ltcg_tax == approx(expected_ltcg_at_15_no_int)
        assert result_no_interest.ltcg_tax < result.ltcg_tax


class TestAboveTheLineAdjustments:
    """HSA + deductible-IRA contributions are above-the-line: reduce AGI/MAGI."""

    def test_defaults_to_zero(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot()
        assert ytd.hsa_contribution_ytd == 0.0
        assert ytd.deductible_ira_contribution_ytd == 0.0
        assert ytd.above_the_line_adjustments_ytd == 0.0

    def test_above_the_line_adjustments_ytd_sums_both_fields(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(hsa_contribution_ytd=5_150.0, deductible_ira_contribution_ytd=16_000.0)
        assert ytd.above_the_line_adjustments_ytd == approx(21_150.0)

    def test_total_ordinary_income_reduced_by_adjustments(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            wages_ytd=100_000.0,
            hsa_contribution_ytd=5_150.0,
            deductible_ira_contribution_ytd=16_000.0,
        )
        assert ytd.total_ordinary_income == approx(100_000.0 - 21_150.0)

    def test_magi_ytd_reduced_by_adjustments(self):
        from models.ytd_income import YTDSnapshot

        base_kwargs = {
            "wages_ytd": 100_000.0,
            "stcg_ytd": 10_000.0,
            "ltcg_ytd": 20_000.0,
            "ordinary_dividends_ytd": 3_000.0,
            "interest_ytd": 1_000.0,
        }
        ytd_no_adj = YTDSnapshot(**base_kwargs)
        ytd_with_adj = YTDSnapshot(
            **base_kwargs,
            hsa_contribution_ytd=5_150.0,
            deductible_ira_contribution_ytd=16_000.0,
        )
        assert ytd_no_adj.magi_ytd - ytd_with_adj.magi_ytd == approx(21_150.0)

    def test_niit_magi_ytd_reduced_by_same_amount(self):
        from models.ytd_income import YTDSnapshot

        base_kwargs = {
            "wages_ytd": 100_000.0,
            "ltcg_ytd": 20_000.0,
            "tax_exempt_interest_ytd": 2_000.0,
        }
        ytd_no_adj = YTDSnapshot(**base_kwargs)
        ytd_with_adj = YTDSnapshot(
            **base_kwargs,
            hsa_contribution_ytd=5_150.0,
            deductible_ira_contribution_ytd=16_000.0,
        )
        assert ytd_no_adj.niit_magi_ytd - ytd_with_adj.niit_magi_ytd == approx(21_150.0)

    def test_total_investment_income_unchanged_by_adjustments(self):
        """HSA/IRA contributions are not investment income; NIIT base must be unaffected."""
        from models.ytd_income import YTDSnapshot

        ytd_no_adj = YTDSnapshot(ltcg_ytd=50_000.0, interest_ytd=2_000.0)
        ytd_with_adj = YTDSnapshot(
            ltcg_ytd=50_000.0,
            interest_ytd=2_000.0,
            hsa_contribution_ytd=5_150.0,
            deductible_ira_contribution_ytd=16_000.0,
        )
        assert ytd_with_adj.total_investment_income == approx(ytd_no_adj.total_investment_income)


class TestNonNegativeContributionClamp:
    """Audit 2026-07-13 (R1+R2): hsa/deductible-ira contributions must clamp to >= 0.

    These fields are SUBTRACTED in above_the_line_adjustments_ytd; a negative
    entry (e.g. a widget lacking min_value) would flip from reducing income to
    inflating it. The clamp lives at the model level (__post_init__) so the
    invariant holds regardless of widget config.
    """

    def test_negative_hsa_contribution_clamped_to_zero(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(hsa_contribution_ytd=-2_000.0)
        assert ytd.hsa_contribution_ytd == 0.0

    def test_negative_deductible_ira_contribution_clamped_to_zero(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(deductible_ira_contribution_ytd=-16_000.0)
        assert ytd.deductible_ira_contribution_ytd == 0.0

    def test_negative_hsa_contribution_does_not_inflate_ordinary_income(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=100_000.0, hsa_contribution_ytd=-2_000.0)
        assert ytd.total_ordinary_income == approx(100_000.0)

    def test_negative_deductible_ira_contribution_does_not_inflate_magi(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=100_000.0, deductible_ira_contribution_ytd=-16_000.0)
        assert ytd.magi_ytd == approx(100_000.0)

    def test_positive_contributions_pass_through_unchanged(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(hsa_contribution_ytd=5_150.0, deductible_ira_contribution_ytd=16_000.0)
        assert ytd.hsa_contribution_ytd == 5_150.0
        assert ytd.deductible_ira_contribution_ytd == 16_000.0

    def test_negative_ltcg_and_stcg_still_allowed_not_clamped(self):
        """Regression guard: the new clamp must NOT extend to loss fields (PR #368)."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(ltcg_ytd=-5_000.0, stcg_ytd=-1_000.0)
        assert ytd.ltcg_ytd == -5_000.0
        assert ytd.stcg_ytd == -1_000.0


class TestCryptoFields:
    """Koinly-aligned crypto YTD fields: STCG, LTCG, and staking/DeFi/airdrop income.

    crypto_stcg_ytd  -> ordinary brackets + MAGI + NIIT (identical to stcg_ytd).
    crypto_ltcg_ytd  -> MAGI + NIIT, NOT ordinary brackets (identical to ltcg_ytd).
    crypto_income_ytd -> ordinary brackets + MAGI, NOT NIIT (staking-as-NII unsettled;
    conservative to exclude from the investment-income base).
    """

    def test_defaults_to_zero(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot()
        assert ytd.crypto_stcg_ytd == 0.0
        assert ytd.crypto_ltcg_ytd == 0.0
        assert ytd.crypto_income_ytd == 0.0

    def test_crypto_stcg_hits_ordinary_magi_and_niit(self):
        from models.ytd_income import YTDSnapshot

        base = YTDSnapshot(wages_ytd=100_000.0)
        with_stcg = YTDSnapshot(wages_ytd=100_000.0, crypto_stcg_ytd=10_000.0)

        assert with_stcg.total_ordinary_income - base.total_ordinary_income == approx(10_000.0)
        assert with_stcg.magi_ytd - base.magi_ytd == approx(10_000.0)
        assert with_stcg.total_investment_income - base.total_investment_income == approx(10_000.0)

    def test_crypto_ltcg_hits_magi_and_niit_not_ordinary(self):
        from models.ytd_income import YTDSnapshot

        base = YTDSnapshot(wages_ytd=100_000.0)
        with_ltcg = YTDSnapshot(wages_ytd=100_000.0, crypto_ltcg_ytd=20_000.0)

        assert with_ltcg.total_ordinary_income == approx(base.total_ordinary_income)
        assert with_ltcg.magi_ytd - base.magi_ytd == approx(20_000.0)
        assert with_ltcg.total_investment_income - base.total_investment_income == approx(20_000.0)

    def test_crypto_income_hits_ordinary_and_magi_not_niit(self):
        from models.ytd_income import YTDSnapshot

        base = YTDSnapshot(wages_ytd=100_000.0)
        with_income = YTDSnapshot(wages_ytd=100_000.0, crypto_income_ytd=5_000.0)

        assert with_income.total_ordinary_income - base.total_ordinary_income == approx(5_000.0)
        assert with_income.magi_ytd - base.magi_ytd == approx(5_000.0)
        assert with_income.total_investment_income == approx(base.total_investment_income)

    def test_niit_magi_increases_by_sum_of_all_three(self):
        from models.ytd_income import YTDSnapshot

        base = YTDSnapshot(wages_ytd=100_000.0, tax_exempt_interest_ytd=2_000.0)
        all_crypto = YTDSnapshot(
            wages_ytd=100_000.0,
            tax_exempt_interest_ytd=2_000.0,
            crypto_stcg_ytd=10_000.0,
            crypto_ltcg_ytd=20_000.0,
            crypto_income_ytd=5_000.0,
        )
        assert all_crypto.niit_magi_ytd - base.niit_magi_ytd == approx(35_000.0)


class TestCryptoFieldsCacheRoundtrip:
    """save_ytd_snapshot/load_ytd_snapshot must preserve crypto fields, with migration."""

    def test_roundtrip_preserves_new_fields(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import YTDSnapshot

        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", tmp_path / "ytd_crypto.json")

        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=80_000.0,
            crypto_stcg_ytd=10_000.0,
            crypto_ltcg_ytd=20_000.0,
            crypto_income_ytd=5_000.0,
        )
        save_ytd_snapshot(ytd)
        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.crypto_stcg_ytd == 10_000.0
        assert loaded.crypto_ltcg_ytd == 20_000.0
        assert loaded.crypto_income_ytd == 5_000.0

    def test_cache_missing_new_keys_migrates_to_zero(self, tmp_path, monkeypatch):
        """Pre-existing caches lacking the new keys must load without raising."""
        import json

        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import YTDSnapshot

        cache_path = tmp_path / "ytd_legacy_crypto.json"
        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", cache_path)

        save_ytd_snapshot(YTDSnapshot(tax_year=2026, wages_ytd=50_000.0))
        data = json.loads(cache_path.read_text())
        data.pop("crypto_stcg_ytd", None)
        data.pop("crypto_ltcg_ytd", None)
        data.pop("crypto_income_ytd", None)
        cache_path.write_text(json.dumps(data))

        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.crypto_stcg_ytd == 0.0
        assert loaded.crypto_ltcg_ytd == 0.0
        assert loaded.crypto_income_ytd == 0.0


class TestYTDDividendSplit:
    """Tests for the qualified/ordinary YTD dividend split."""

    def test_backward_compat_property(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot(
            qualified_dividends_ytd=500.0,
            ordinary_dividends_ytd=300.0,
        )
        assert snap.dividends_ytd == 800.0

    def test_zero_split(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot()
        assert snap.dividends_ytd == 0.0
        assert snap.qualified_dividends_ytd == 0.0
        assert snap.ordinary_dividends_ytd == 0.0

    def test_niit_includes_both_dividend_types(self):
        from models.ytd_income import YTDSnapshot

        snap = YTDSnapshot(
            qualified_dividends_ytd=500.0,
            ordinary_dividends_ytd=300.0,
            ltcg_ytd=1000.0,
            interest_ytd=200.0,
        )
        # total_investment_income = ltcg + stcg + dividends (qual + ord) + interest
        # = 1000 + 0 + 800 + 200 = 2000
        assert snap.total_investment_income == pytest.approx(2000.0)

    def test_scenario_year_dividend_split_fields_and_compat(self):
        """YearResult carries split fields; ytd_dividends is backward-compat aggregate."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(
            tax_year=2026,
            qualified_dividends_ytd=1_000,
            ordinary_dividends_ytd=500,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        assert yr2026.ytd_qualified_dividends == approx(1_000)
        assert yr2026.ytd_ordinary_dividends == approx(500)
        # backward-compat aggregate
        assert yr2026.ytd_dividends == approx(1_500)


class TestFetchMagi:
    """Verify fetch_magi + apply_magi end-to-end (A3 — prior-year MAGI consumer)."""

    # ------------------------------------------------------------------
    # fetch_magi tests
    # ------------------------------------------------------------------

    def test_fetch_magi_happy_path_returns_dict(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_magi

        payload = {
            "year": 2024,
            "filing_status": "MFJ",
            "agi": 180_000.0,
            "magi": 183_000.0,
            "tax_exempt_interest": 3_000.0,
            "ss_taxable_amount": 0.0,
            "foreign_earned_income_exclusion": 0.0,
            "source": "turbotax",
        }

        class _FakeResp:
            status_code = 200

            def json(self):
                return payload

            def raise_for_status(self):
                pass

        monkeypatch.setattr(req, "get", lambda *a, **kw: _FakeResp())
        result = fetch_magi(2024)
        assert isinstance(result, dict)
        assert result["year"] == 2024
        assert result["magi"] == 183_000.0

    def test_fetch_magi_404_returns_none(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_magi

        class _FakeResp:
            status_code = 404

            def raise_for_status(self):
                pass

        monkeypatch.setattr(req, "get", lambda *a, **kw: _FakeResp())
        assert fetch_magi(2020) is None

    def test_fetch_magi_network_error_returns_none(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_magi

        def _raise(*args, **kwargs):
            raise req.exceptions.ConnectionError("refused")

        monkeypatch.setattr(req, "get", _raise)
        assert fetch_magi(2024) is None

    def test_fetch_magi_malformed_shape_returns_none(self, monkeypatch):
        import requests as req

        from engine.portfolio_sync import fetch_magi

        class _FakeList:
            status_code = 200

            def json(self):
                return [{"year": 2024}]

            def raise_for_status(self):
                pass

        monkeypatch.setattr(req, "get", lambda *a, **kw: _FakeList())
        assert fetch_magi(2024) is None


class TestApplyBrokerageStatementRecords:
    def test_overlays_investment_fields_only(self):
        from engine.brokerage_statement_pdf import BrokerageStatementRecord
        from engine.portfolio_sync.ytd import apply_brokerage_statement_records
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=50_000.0, interest_ytd=0.0)
        rec = BrokerageStatementRecord(
            account_number="111-1111",
            broker="schwab",
            account_type="taxable",
            statement_period_end="2026-06-30",
            interest_taxable_ytd=18.56,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=4846.82,
            dividends_tax_exempt_ytd=78.74,
            stcg_net_ytd=19.11,
            ltcg_net_ytd=283895.77,
            captured_at="2026-07-10T00:00:00+00:00",
        )
        result = apply_brokerage_statement_records(ytd, {"111-1111": rec})
        assert result.interest_ytd == 18.56
        assert result.tax_exempt_interest_ytd == 78.74
        assert result.ordinary_dividends_ytd == 4846.82
        assert result.wages_ytd == 50_000.0  # untouched

    def test_caller_must_pre_filter_non_taxable(self):
        # This test documents the contract, not new behavior: passing a
        # non-taxable record here WOULD get summed, because filtering happens
        # in partition_by_account_type, not here. The real safeguard is that
        # views/ytd_income.py never constructs taxable_by_account from
        # anything but partition_by_account_type's "taxable" output.
        from engine.brokerage_statement_pdf import BrokerageStatementRecord
        from engine.portfolio_sync.ytd import apply_brokerage_statement_records
        from models.ytd_income import YTDSnapshot

        roth_rec = BrokerageStatementRecord(
            account_number="XXXX7368",
            broker="vanguard",
            account_type="roth_ira",
            statement_period_end="2026-06-30",
            interest_taxable_ytd=0.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=283.86,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-10T00:00:00+00:00",
        )
        result = apply_brokerage_statement_records(YTDSnapshot(), {"XXXX7368": roth_rec})
        assert result.ordinary_dividends_ytd == 283.86  # would be wrong in production --
        # this is exactly why views/ytd_income.py must call partition_by_account_type
        # first and only ever pass the "taxable" dict here.


class TestTaxExemptInterestSaveLoad:
    """PR-3: tax_exempt_interest_ytd must survive a save_ytd_snapshot/load round-trip."""

    def test_save_load_roundtrip_preserves_tax_exempt_interest(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import YTDSnapshot

        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", tmp_path / "ytd_muni.json")

        ytd = YTDSnapshot(tax_year=2026, wages_ytd=80_000.0, tax_exempt_interest_ytd=6_000.0)
        save_ytd_snapshot(ytd)
        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.tax_exempt_interest_ytd == 6_000.0, (
            f"Expected tax_exempt_interest_ytd=6000 after round-trip; got {loaded.tax_exempt_interest_ytd}"
        )


class TestAboveTheLineAdjustmentsCacheRoundtrip:
    """save_ytd_snapshot/load_ytd_snapshot must preserve HSA/IRA fields, with migration."""

    def test_roundtrip_preserves_new_fields(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import YTDSnapshot

        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", tmp_path / "ytd_atl.json")

        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=80_000.0,
            hsa_contribution_ytd=5_150.0,
            deductible_ira_contribution_ytd=16_000.0,
        )
        save_ytd_snapshot(ytd)
        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.hsa_contribution_ytd == 5_150.0
        assert loaded.deductible_ira_contribution_ytd == 16_000.0

    def test_cache_missing_new_keys_migrates_to_zero(self, tmp_path, monkeypatch):
        """Pre-existing caches lacking the new keys must load without raising."""
        import json

        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import YTDSnapshot

        cache_path = tmp_path / "ytd_legacy.json"
        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", cache_path)

        save_ytd_snapshot(YTDSnapshot(tax_year=2026, wages_ytd=50_000.0))
        data = json.loads(cache_path.read_text())
        data.pop("hsa_contribution_ytd", None)
        data.pop("deductible_ira_contribution_ytd", None)
        cache_path.write_text(json.dumps(data))

        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.hsa_contribution_ytd == 0.0
        assert loaded.deductible_ira_contribution_ytd == 0.0


class TestEstimateYtdFederalTax:
    """Tests for engine.tax.estimate_ytd_federal_tax."""

    def _hh(self) -> "Household":
        from models.household import Household

        return Household(your_age=61, spouse_age=55, your_ira=500_000, spouse_ira=500_000)

    def test_zero_income_returns_all_zeros(self):
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot()
        result = estimate_ytd_federal_tax(ytd, self._hh())
        assert result.ordinary_tax == 0.0
        assert result.ltcg_tax == 0.0
        assert result.niit == 0.0
        assert result.total == 0.0
        assert result.effective_rate == 0.0

    def test_pure_wages_no_ltcg(self):
        """W-2 wages only — ordinary_tax matches bracket calc on taxable (not gross) income, ltcg_tax=0."""
        from engine.tax import STD_DEDUCTION_MFJ, estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        wages = 150_000.0
        ytd = YTDSnapshot(wages_ytd=wages)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        # updated: F19/F1 — std deduction applied before bracket walk; ordinary_tax uses
        # taxable_ordinary = wages - std_ded, not gross wages.
        assert result.ordinary_tax == pytest.approx(federal_tax(wages - STD_DEDUCTION_MFJ))
        assert result.ltcg_tax == 0.0
        assert result.niit == 0.0
        assert result.total == pytest.approx(result.ordinary_tax)

    def test_mix_wages_and_ltcg_uses_preferential_rate(self):
        """Taxable ordinary below LTCG 0%-threshold → LTCG at 0%; above → 15%.

        Standard deduction ($32,200 MFJ, no seniors) is subtracted before the
        LTCG stack-walk per IRC §1(h)(1). ltcg_start = max(wages - std_ded, 0).
        """
        from engine.tax import STD_DEDUCTION_MFJ, estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        # taxable_ordinary = 50,000 - 32,200 = 17,800 → ltcg_end = 27,800 < 96,700 → 0%
        ytd_zero = YTDSnapshot(wages_ytd=50_000.0, ltcg_ytd=10_000.0)
        r_zero = estimate_ytd_federal_tax(ytd_zero, self._hh())
        assert r_zero.ltcg_tax == pytest.approx(0.0)

        # Rev. Proc. 2025-32 §3.03: 0%/15% threshold = $98,900 (MFJ).
        # wages = 32,200 + 99,900 = 132,100 → taxable_ordinary = 99,900 > 98,900 threshold
        # ltcg_start = 99,900, ltcg_end = 119,900 → all $20K above threshold → 15%
        ytd_15 = YTDSnapshot(wages_ytd=STD_DEDUCTION_MFJ + 99_900, ltcg_ytd=20_000.0)
        r_15 = estimate_ytd_federal_tax(ytd_15, self._hh())
        assert r_15.ltcg_tax == pytest.approx(20_000.0 * 0.15)

    def test_above_niit_threshold_niit_nonzero(self):
        """MAGI above $250K with investment income → NIIT non-zero."""
        from engine.niit import NIIT_RATE, NIIT_THRESHOLD_MFJ
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        wages = NIIT_THRESHOLD_MFJ + 20_000  # $270K
        ltcg = 15_000.0
        ytd = YTDSnapshot(wages_ytd=float(wages), ltcg_ytd=ltcg)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        # magi_excess = 20_000; NII = ltcg = 15_000 → niit = 15_000 * 0.038
        assert result.niit == pytest.approx(min(ltcg, 20_000.0) * NIIT_RATE)

    def test_marginal_bracket_and_room_correct(self):
        """Marginal bracket and room-to-next-bracket are correct for mid-bracket income."""
        from engine.tax import BRACKETS_MFJ, STD_DEDUCTION_MFJ, estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        # Put wages midway through the 12% bracket (24_800–100_800 taxable)
        wages = 60_000.0  # taxable_ordinary = 60_000 - 32_200 = 27_800 → inside 12% bracket
        ytd = YTDSnapshot(wages_ytd=wages)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        assert result.marginal_bracket_pct == pytest.approx(0.12)
        # updated: F14 — room measured from taxable income (wages - std_ded), not gross wages.
        # room = BRACKETS_MFJ[1][0] - (wages - STD_DEDUCTION_MFJ) = 100_800 - 27_800 = 73_000
        taxable = wages - STD_DEDUCTION_MFJ
        assert result.room_to_next_bracket == pytest.approx(BRACKETS_MFJ[1][0] - taxable)

    def test_ltcg_tax_when_stack_crosses_15pct_threshold(self):
        """User scenario: $27K ordinary + $283K LTCG + $2,977 qual-div.

        Rev. Proc. 2025-32 §3.03: 0%/15% threshold = $98,900 (MFJ).
        std_ded = $32,200 (MFJ, no seniors). Wages ($27K) only absorb $27K of
        std_ded, leaving $5,200 unused. Per IRC §1(h) (audit-0805 C1), unused
        standard deduction offsets capital gain, not just ordinary income —
        it does NOT get stacked as taxable gain.
        taxable_ordinary = max(27K - 32.2K, 0) = $0.
        ltcg_start = $0.
        ltcg_end = (27K + 283K + 2,977) - 32.2K = $280,777
            (stack total $285,977 less the $5,200 unused std_ded absorbed
            by the gain, per C1).
        ltcg_at_15 = min($280,777, $613,700) - max($0, $98,900) = $280,777 - $98,900 = $181,877.
        ltcg_tax = $181,877 x 0.15 = $27,281.55.

        Reconciliation vs. pre-fix (defective) golden: the old code stacked
        the $5,200 unused deduction as taxable 15%-band gain instead of
        letting it offset the gain, overstating tax by
        $5,200 x 0.15 = $780.00 ($28,061.55 -> $27,281.55).
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(
            wages_ytd=27_000.0,
            ltcg_ytd=283_000.0,
            qualified_dividends_ytd=2_977.0,
        )
        result = estimate_ytd_federal_tax(ytd, self._hh())
        assert result.ltcg_tax == pytest.approx(181_877.0 * 0.15, abs=1.0)

    def test_ltcg_tax_all_in_0pct_bracket(self):
        """Stack entirely under $96,700 threshold → LTCG tax = $0."""
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=50_000.0, ltcg_ytd=40_000.0)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        # ltcg_start=$50K, ltcg_end=$90K — entirely below $96,700
        assert result.ltcg_tax == pytest.approx(0.0)

    def test_ltcg_tax_crosses_20pct_threshold(self):
        """Stack crosses into 20% bracket: $200K ordinary + $500K LTCG.

        Rev. Proc. 2025-32 §3.03: 0%/15% = $98,900; 15%/20% = $613,700 (MFJ).
        std_ded = $32,200 (MFJ, no seniors). taxable_ordinary = $167,800.
        ltcg_start = $167,800, ltcg_end = $667,800.
        ltcg_at_15 = min($667,800, $613,700) - max($167,800, $98,900) = $613,700 - $167,800 = $445,900.
        ltcg_at_20 = $667,800 - $613,700 = $54,100.
        ltcg_tax = $445,900 x 0.15 + $54,100 x 0.20 = $66,885.00 + $10,820.00 = $77,705.00.
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=200_000.0, ltcg_ytd=500_000.0)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        assert result.ltcg_tax == pytest.approx(77_705.00, abs=0.01)

    def test_ltcg_new_threshold_boundary_0pct_to_15pct(self):
        """Rev. Proc. 2025-32 §3.03 boundary: stack crosses $98,900 yielding partial 15%.

        std_ded = $32,200 (MFJ, no seniors).
        wages = $32,200 + $90,700 = $122,900 → taxable_ordinary = $90,700.
        ltcg_end = $90,700 + $10,000 = $100,700.
        Stack crosses Rev. Proc. 2025-32 threshold ($98,900): $1,800 in 15% band.
        Old Rev. Proc. 2024-40 threshold ($96,700) would have put $4,000 at 15% — confirms new value is used.
        """
        from engine.tax import STD_DEDUCTION_MFJ, estimate_ytd_federal_tax
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=STD_DEDUCTION_MFJ + 90_700, ltcg_ytd=10_000.0)
        result = estimate_ytd_federal_tax(ytd, self._hh())
        # ltcg_at_15 = min($100,700, $613,700) - max($90,700, $98,900) = $100,700 - $98,900 = $1,800
        # ltcg_tax = $1,800 x 0.15 = $270.00
        assert result.ltcg_tax == pytest.approx(1_800.0 * 0.15, abs=0.01)

    def test_ltcg_std_ded_both_seniors_mfj_all_zero_pct(self):
        """Regression A-2/E-7: MFJ both 65+, modest ordinary → all LTCG at 0%.

        std_ded = $32,200 + 2 x $1,650 = $35,500.
        ordinary = $80,000 → taxable_ordinary = $44,500.
        LTCG 0%-threshold ($96,700) headroom = $52,200 > $40,000 LTCG → 0% rate.

        Pre-fix (gross as stack base): ltcg_start=$80K, ltcg_end=$120K,
        ltcg_at_15 = $120K - $96,700 = $23,300 → tax $3,495 (wrong).
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        hh = Household(your_age=65, spouse_age=65, your_ira=500_000, spouse_ira=500_000)
        ytd = YTDSnapshot(wages_ytd=80_000.0, ltcg_ytd=40_000.0)
        result = estimate_ytd_federal_tax(ytd, hh)
        assert result.ltcg_tax == pytest.approx(0.0)

    def test_ltcg_std_ded_neither_senior_mfj_all_zero_pct(self):
        """Regression A-2/E-7: MFJ neither 65+, ordinary=$120K, LTCG=$10K → 0% LTCG.

        std_ded = $32,200.  taxable_ordinary = $87,800.
        LTCG 0%-threshold headroom = $96,700 - $87,800 = $8,900 < $10,000 LTCG.
        Wait — $10K > $8,900 headroom, so $1,100 spills into 15%.
        Use $8,000 LTCG to stay fully in 0% band.

        ordinary=$120K, LTCG=$8K → taxable_ordinary=$87,800.
        ltcg_end=$95,800 < $96,700 → all at 0%.
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        hh = Household(your_age=55, spouse_age=52, your_ira=500_000, spouse_ira=500_000)
        ytd = YTDSnapshot(wages_ytd=120_000.0, ltcg_ytd=8_000.0)
        result = estimate_ytd_federal_tax(ytd, hh)
        assert result.ltcg_tax == pytest.approx(0.0)

    def test_ltcg_std_ded_single_senior_all_zero_pct(self):
        """Regression A-2/E-7: Single 65+, ordinary=$40K, LTCG=$30K → all LTCG at 0%.

        std_ded = $16,100 + $1,850 = $17,950.
        taxable_ordinary = $40,000 - $17,950 = $22,050.
        Single 0%-threshold = $48,350; headroom = $26,300 > $30,000? No — $26,300 < $30,000.
        Use $25,000 LTCG to keep entirely in 0% band.

        taxable_ordinary=$22,050; ltcg_end=$47,050 < $48,350 → 0%.
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        hh = Household(
            your_age=65, spouse_age=55, your_ira=500_000, spouse_ira=0, filing_status="Single"
        )
        ytd = YTDSnapshot(wages_ytd=40_000.0, ltcg_ytd=25_000.0)
        result = estimate_ytd_federal_tax(ytd, hh)
        assert result.ltcg_tax == pytest.approx(0.0)


class TestIncomeEvent:
    def test_income_event_fields(self):
        event = IncomeEvent(date="2026-03-15", amount=25_000.0, kind="conversion", owner="you")
        assert event.date == "2026-03-15"
        assert event.amount == 25_000.0
        assert event.kind == "conversion"
        assert event.owner == "you"

    def test_income_event_owner_defaults_to_you(self):
        event = IncomeEvent(date="2026-03-15", amount=10_000.0, kind="distribution")
        assert event.owner == "you"


class TestSumIncomeEvents:
    def test_sums_matching_kind_and_owner(self):
        events = [
            IncomeEvent(date="2026-01-10", amount=10_000.0, kind="conversion", owner="you"),
            IncomeEvent(date="2026-02-10", amount=15_000.0, kind="conversion", owner="you"),
            IncomeEvent(date="2026-03-10", amount=5_000.0, kind="conversion", owner="spouse"),
            IncomeEvent(date="2026-04-10", amount=8_000.0, kind="distribution", owner="you"),
        ]
        assert sum_income_events(events, kind="conversion", owner="you") == 25_000.0
        assert sum_income_events(events, kind="conversion", owner="spouse") == 5_000.0
        assert sum_income_events(events, kind="distribution", owner="you") == 8_000.0

    def test_sum_with_no_owner_filter_combines_both(self):
        events = [
            IncomeEvent(date="2026-01-10", amount=8_000.0, kind="distribution", owner="you"),
            IncomeEvent(date="2026-02-10", amount=3_000.0, kind="distribution", owner="spouse"),
        ]
        assert sum_income_events(events, kind="distribution") == 11_000.0

    def test_empty_list_sums_to_zero(self):
        assert sum_income_events([], kind="conversion", owner="you") == 0.0


class TestYTDToFromDictRoundtrip:
    """ytd_to_dict/ytd_from_dict (audit-0823: "YTD in the data-bridge bundle") --
    extracted, pure counterparts of save_ytd_snapshot/load_ytd_snapshot's body.
    Both the on-disk cache AND the bridge_bundle "ytd" section go through these,
    so a round-trip failure here would silently corrupt either path."""

    def test_full_roundtrip_including_events(self):
        from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict
        from models.ytd_income import RealizedGainEvent, YTDSnapshot

        ytd = YTDSnapshot(
            tax_year=2026,
            snapshot_date="2026-06-12",
            wages_ytd=150_000.0,
            nec_income_ytd=5_000.0,
            ira_conversions_ytd=25_000.0,
            spouse_ira_conversions_ytd=7_500.0,
            ira_distributions_ytd=10_000.0,
            ltcg_ytd=50_000.0,
            stcg_ytd=4_000.0,
            qualified_dividends_ytd=2_000.0,
            ordinary_dividends_ytd=3_000.0,
            interest_ytd=600.0,
            tax_exempt_interest_ytd=1_500.0,
            nqo_exercise_ytd=96_000.0,
            federal_withholding_ytd=42_000.0,
            hsa_contribution_ytd=1_000.0,
            deductible_ira_contribution_ytd=500.0,
            crypto_stcg_ytd=10_000.0,
            crypto_ltcg_ytd=20_000.0,
            crypto_income_ytd=5_000.0,
            gain_events=[
                RealizedGainEvent(
                    date="2026-03-15",
                    description="TXN stop-loss",
                    proceeds=250_000,
                    cost_basis=150_000,
                    holding_period="long",
                    account_name="Schwab Brokerage",
                )
            ],
            income_events=[
                IncomeEvent(date="2026-02-01", amount=25_000.0, kind="conversion", owner="you"),
            ],
            manually_entered=False,
        )

        roundtripped = ytd_from_dict(ytd_to_dict(ytd))

        assert roundtripped == ytd

    def test_from_dict_migrates_legacy_dividends_key(self):
        """Pre-split caches/bundles stored a single dividends_ytd key."""
        from engine.portfolio_sync.ytd import ytd_from_dict

        data = {"tax_year": 2026, "dividends_ytd": 4_500.0}
        snap = ytd_from_dict(data)
        assert snap.ordinary_dividends_ytd == 4_500.0
        assert snap.qualified_dividends_ytd == 0.0

    def test_from_dict_migrates_missing_nqo_exercise_ytd(self):
        """Pre-PR1 caches/bundles lack nqo_exercise_ytd entirely."""
        from engine.portfolio_sync.ytd import ytd_from_dict

        data = {"tax_year": 2026, "wages_ytd": 10_000.0}
        snap = ytd_from_dict(data)
        assert snap.nqo_exercise_ytd == 0.0

    def test_from_dict_does_not_mutate_caller_dict(self):
        """ytd_from_dict must operate on a copy -- a bundle dict is reused
        elsewhere (setup_scalars/portfolio/ledger sections) and popping keys
        off the caller's own dict in place would corrupt those other reads."""
        from engine.portfolio_sync.ytd import ytd_from_dict

        data = {"tax_year": 2026, "gain_events": [], "income_events": []}
        original = dict(data)
        ytd_from_dict(data)
        assert data == original
