"""Tests for engine.tax — brackets, deductions, LTCG, OBBBA senior bonus, safe harbor."""

import json

import pytest

from engine.tax import (
    deductions,
    federal_tax,
    marginal_rate,
    room_to_12,
    room_to_22,
)


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestTaxEngine:
    def test_tax_on_zero(self):
        assert federal_tax(0) == 0

    def test_tax_top_of_10pct(self):
        assert federal_tax(24_800) == approx(24_800 * 0.10)

    def test_tax_top_of_12pct(self):
        t = 24_800 * 0.10 + (100_800 - 24_800) * 0.12
        assert federal_tax(100_800) == approx(t)

    def test_tax_top_of_22pct(self):
        t = 24_800 * 0.10 + (100_800 - 24_800) * 0.12 + (211_400 - 100_800) * 0.22
        assert federal_tax(211_400) == approx(t)

    def test_marginal_rates(self):
        assert marginal_rate(50_000) == 0.12
        assert marginal_rate(150_000) == 0.22
        assert marginal_rate(300_000) == 0.24

    def test_room_to_12_no_income(self):
        assert room_to_12(0, 32_200) == approx(133_000)

    def test_room_to_12_with_options(self):
        assert room_to_12(69_934, 32_200) == approx(63_066)

    def test_room_to_22_no_income(self):
        assert room_to_22(0, 32_200) == approx(243_600)


class TestDeductions:
    def test_under_65(self):
        assert deductions(60, 54) == 32_200

    def test_one_senior(self):
        assert deductions(65, 59) == 32_200 + 1_650

    def test_both_senior(self):
        assert deductions(75, 69) == 32_200 + 2 * 1_650


class TestSafeHarborPayment:
    """Tests for engine.tax.safe_harbor_payment."""

    def test_no_prior_year_uses_current_estimate(self):
        """prior=0 → uses current estimate as target."""
        from engine.tax import safe_harbor_payment

        g = safe_harbor_payment(
            prior_year_tax=0.0,
            current_year_estimate=100_000.0,
            already_paid_ytd=0.0,
            payment_date="2026-06-12",
        )
        assert g.safe_harbor_target == pytest.approx(100_000.0)
        assert "current estimate" in g.rule_used
        assert g.remaining_to_pay == pytest.approx(100_000.0)

    def test_prior_110pct_is_lesser_uses_prior(self):
        """110% prior ($88K) < current ($120K) → uses prior."""
        from engine.tax import safe_harbor_payment

        g = safe_harbor_payment(
            prior_year_tax=80_000.0,
            current_year_estimate=120_000.0,
            already_paid_ytd=0.0,
            payment_date="2026-06-12",
        )
        assert g.safe_harbor_target == pytest.approx(88_000.0)
        assert "110% prior" in g.rule_used

    def test_current_is_lesser_uses_current(self):
        """current ($100K) < 110% prior ($132K) → uses current."""
        from engine.tax import safe_harbor_payment

        g = safe_harbor_payment(
            prior_year_tax=120_000.0,
            current_year_estimate=100_000.0,
            already_paid_ytd=0.0,
            payment_date="2026-06-12",
        )
        assert g.safe_harbor_target == pytest.approx(100_000.0)
        assert "current estimate" in g.rule_used

    def test_already_paid_reduces_remaining(self):
        """Already paid $60K of $88K target → remaining = $28K."""
        from engine.tax import safe_harbor_payment

        g = safe_harbor_payment(
            prior_year_tax=80_000.0,
            current_year_estimate=120_000.0,
            already_paid_ytd=60_000.0,
            payment_date="2026-06-12",
        )
        assert g.remaining_to_pay == pytest.approx(28_000.0)

    def test_quarterly_due_dates(self):
        """Correct next-quarterly-due date for each calendar quarter."""
        from engine.tax import safe_harbor_payment

        cases = [
            ("2026-01-15", "2026-04-15"),  # Q1 window
            ("2026-03-01", "2026-04-15"),  # Q1 window
            ("2026-04-15", "2026-04-15"),  # Q1 boundary
            ("2026-04-16", "2026-06-15"),  # Q2 window
            ("2026-06-15", "2026-06-15"),  # Q2 boundary
            ("2026-06-16", "2026-09-15"),  # Q3 window
            ("2026-09-15", "2026-09-15"),  # Q3 boundary
            ("2026-09-16", "2027-01-15"),  # Q4 window
            ("2026-12-31", "2027-01-15"),  # Q4 boundary
        ]
        for payment_date, expected_due in cases:
            g = safe_harbor_payment(0.0, 0.0, 0.0, payment_date)
            assert g.next_quarterly_due == expected_due, (
                f"payment_date={payment_date}: expected {expected_due}, got {g.next_quarterly_due}"
            )

    def test_safe_harbor_uses_100pct_when_agi_low(self):
        """Prior-year AGI ≤ $150K → 100% prior-year rule (not 110%)."""
        from engine.tax import safe_harbor_payment

        # prior=$80K, AGI=$100K (≤ $150K threshold) → safe harbor = 100% × $80K = $80K
        g = safe_harbor_payment(
            prior_year_tax=80_000.0,
            current_year_estimate=120_000.0,
            already_paid_ytd=0.0,
            payment_date="2026-06-12",
            prior_year_agi=100_000.0,
        )
        assert g.safe_harbor_target == pytest.approx(80_000.0)
        assert "100% prior year" in g.rule_used

    def test_safe_harbor_uses_110pct_when_agi_high(self):
        """Prior-year AGI > $150K → 110% prior-year rule."""
        from engine.tax import safe_harbor_payment

        # prior=$80K, AGI=$200K (> $150K threshold) → safe harbor = 110% × $80K = $88K
        g = safe_harbor_payment(
            prior_year_tax=80_000.0,
            current_year_estimate=120_000.0,
            already_paid_ytd=0.0,
            payment_date="2026-06-12",
            prior_year_agi=200_000.0,
        )
        assert g.safe_harbor_target == pytest.approx(88_000.0)
        assert "110% prior year" in g.rule_used

    def test_safe_harbor_single_uses_150k_threshold_not_75k(self):
        """Single filer, AGI between $75K and $150K → 100% rule (Single threshold is $150K, not $75K)."""
        from engine.tax import safe_harbor_payment

        g = safe_harbor_payment(
            prior_year_tax=80_000.0,
            current_year_estimate=120_000.0,
            already_paid_ytd=0.0,
            payment_date="2026-06-12",
            prior_year_agi=100_000.0,
            filing_status="Single",
        )
        assert g.safe_harbor_target == pytest.approx(80_000.0)
        assert "100% prior year" in g.rule_used

    def test_safe_harbor_mfs_uses_75k_threshold(self):
        """MFS filer, AGI above $75K but below $150K → 110% rule (MFS threshold is $75K)."""
        from engine.tax import safe_harbor_payment

        g = safe_harbor_payment(
            prior_year_tax=80_000.0,
            current_year_estimate=120_000.0,
            already_paid_ytd=0.0,
            payment_date="2026-06-12",
            prior_year_agi=100_000.0,
            filing_status="MFS",
        )
        assert g.safe_harbor_target == pytest.approx(88_000.0)
        assert "110% prior year" in g.rule_used

    def test_safe_harbor_unknown_agi_assumes_110pct_and_labels_it(self):
        """prior_year_agi=None → conservatively assume 110%, and the rule label says it's assumed."""
        from engine.tax import safe_harbor_payment

        g = safe_harbor_payment(
            prior_year_tax=80_000.0,
            current_year_estimate=120_000.0,
            already_paid_ytd=0.0,
            payment_date="2026-06-12",
            prior_year_agi=None,
            filing_status="Single",
        )
        assert g.safe_harbor_target == pytest.approx(88_000.0)
        assert "110%" in g.rule_used
        assert "assumed" in g.rule_used.lower()

    def test_next_quarterly_due_rolls_saturday_to_monday(self):
        """Quarterly due dates that fall on Saturday must advance to Monday."""
        from engine.tax import _next_quarterly_due

        # Jun 15, 2024 is a Saturday → should roll to Monday Jun 17, 2024.
        # (Apr 15, 2023 — also a Saturday — rolls to Apr 18, not Apr 17: Monday
        # Apr 17, 2023 was DC Emancipation Day observed, a §7503 legal holiday.)
        assert _next_quarterly_due("2024-04-20") == "2024-06-17"
        assert _next_quarterly_due("2023-01-01") == "2023-04-18"

    def test_next_quarterly_due_rolls_sunday_to_monday(self):
        """Quarterly due dates that fall on Sunday must advance to Monday."""
        from engine.tax import _next_quarterly_due

        # Sep 15, 2024 is a Sunday → should roll to Monday Sep 16, 2024
        # Jun 15 2024 is a Sat (Q2 rolls to Jun 17); use Jun 18 to land in the Q3 window
        result = _next_quarterly_due("2024-06-18")
        assert result == "2024-09-16"

    def test_next_quarterly_due_weekend_gap_maps_to_rolled_deadline(self):
        # Dates in the gap between a weekend nominal due date and its rolled (Monday)
        # deadline must map to that still-open deadline, not the next quarter.
        # Jun 15, 2024 is a Saturday -> Q2 deadline rolls to Mon Jun 17, 2024.
        # (Apr 15, 2028 is also a Saturday, but Mon Apr 17, 2028 is DC Emancipation
        # Day observed, so §7503 rolls that Q1 deadline on to Tue Apr 18, 2028.)
        from engine.tax import _next_quarterly_due

        assert _next_quarterly_due("2024-06-15") == "2024-06-17"
        assert _next_quarterly_due("2024-06-16") == "2024-06-17"
        assert _next_quarterly_due("2024-06-17") == "2024-06-17"
        assert _next_quarterly_due("2028-04-15") == "2028-04-18"
        assert _next_quarterly_due("2028-04-17") == "2028-04-18"

    def test_room_to_12_uses_brackets_constant(self):
        """room_to_12 must derive its ceiling from BRACKETS_MFJ, not a hardcoded literal."""
        from engine.tax import BRACKETS_MFJ, room_to_12

        # The 12% bracket ceiling is BRACKETS_MFJ[1][0]
        bracket_12_ceiling = BRACKETS_MFJ[1][0]
        # room_to_12(0, 0) == bracket_12_ceiling (no deductions, no income)
        assert room_to_12(0, 0) == pytest.approx(bracket_12_ceiling)
        # room_to_12(0, 32_200) == bracket_12_ceiling + 32_200
        assert room_to_12(0, 32_200) == pytest.approx(bracket_12_ceiling + 32_200)

    def test_room_to_22_uses_brackets_constant(self):
        """room_to_22 must derive its ceiling from BRACKETS_MFJ, not a hardcoded literal."""
        from engine.tax import BRACKETS_MFJ, room_to_22

        bracket_22_ceiling = BRACKETS_MFJ[2][0]
        assert room_to_22(0, 0) == pytest.approx(bracket_22_ceiling)
        assert room_to_22(0, 32_200) == pytest.approx(bracket_22_ceiling + 32_200)

    # --- M1: filing_status param on room_to_12 / room_to_22 ---

    def test_room_to_12_single_uses_single_ceiling(self):
        """Single filer sees the 12% Single ceiling ($50,400), not MFJ ceiling ($100,800)."""
        from engine.tax import BRACKETS_SINGLE, room_to_12

        single_12_ceiling = BRACKETS_SINGLE[1][0]  # 50_400
        # With zero income and zero deductions, room == ceiling
        assert room_to_12(0, 0, filing_status="Single") == pytest.approx(single_12_ceiling)
        # With a deduction the room grows by the deduction amount
        assert room_to_12(0, 16_100, filing_status="Single") == pytest.approx(
            single_12_ceiling + 16_100
        )

    def test_room_to_22_single_uses_single_ceiling(self):
        """Single filer sees the 22% Single ceiling ($105,700), not MFJ ceiling ($211,400)."""
        from engine.tax import BRACKETS_SINGLE, room_to_22

        single_22_ceiling = BRACKETS_SINGLE[2][0]  # 105_700
        assert room_to_22(0, 0, filing_status="Single") == pytest.approx(single_22_ceiling)
        assert room_to_22(0, 16_100, filing_status="Single") == pytest.approx(
            single_22_ceiling + 16_100
        )

    def test_room_to_12_default_unchanged_mfj(self):
        """Default (no filing_status) still returns the MFJ ceiling — regression guard."""
        from engine.tax import BRACKETS_MFJ, room_to_12

        mfj_12_ceiling = BRACKETS_MFJ[1][0]  # 100_800
        assert room_to_12(0, 0) == pytest.approx(mfj_12_ceiling)

    def test_room_to_22_default_unchanged_mfj(self):
        """Default (no filing_status) still returns the MFJ ceiling — regression guard."""
        from engine.tax import BRACKETS_MFJ, room_to_22

        mfj_22_ceiling = BRACKETS_MFJ[2][0]  # 211_400
        assert room_to_22(0, 0) == pytest.approx(mfj_22_ceiling)

    def test_room_to_24_uses_brackets_constant(self):
        """room_to_24 must derive its ceiling from BRACKETS_MFJ, not a hardcoded literal."""
        from engine.tax import BRACKETS_MFJ, room_to_24

        bracket_24_ceiling = BRACKETS_MFJ[3][0]
        assert room_to_24(0, 0) == pytest.approx(bracket_24_ceiling)
        assert room_to_24(0, 32_200) == pytest.approx(bracket_24_ceiling + 32_200)

    # --- M3: senior_bonus_deduction survivor (who_dies=="you") ---

    def test_senior_bonus_survivor_who_dies_you(self):
        """Survivor where you died: spouse_age holds survivor's age, your_age==0.
        Single path must count max(0, survivor_age) → full $6,000 bonus."""
        from engine.tax import senior_bonus_deduction

        # who_dies=="you": scenario sets your_age=0, spouse_age=survivor's real age
        result = senior_bonus_deduction(
            your_age=0, spouse_age=67, magi=50_000, year=2026, filing_status="Single"
        )
        assert result == pytest.approx(6_000.0)

    def test_senior_bonus_survivor_who_dies_spouse(self):
        """Survivor where spouse died: your_age holds survivor's age, spouse_age==0."""
        from engine.tax import senior_bonus_deduction

        result = senior_bonus_deduction(
            your_age=67, spouse_age=0, magi=50_000, year=2026, filing_status="Single"
        )
        assert result == pytest.approx(6_000.0)

    def test_senior_bonus_genuine_single_unchanged(self):
        """Genuine single filer (spouse_age always 0): your_age drives eligibility."""
        from engine.tax import senior_bonus_deduction

        result = senior_bonus_deduction(
            your_age=67, spouse_age=0, magi=50_000, year=2026, filing_status="Single"
        )
        assert result == pytest.approx(6_000.0)

    def test_senior_bonus_single_too_young(self):
        """Single filer under 65: no bonus (regression guard)."""
        from engine.tax import senior_bonus_deduction

        result = senior_bonus_deduction(
            your_age=62, spouse_age=0, magi=50_000, year=2026, filing_status="Single"
        )
        assert result == pytest.approx(0.0)


class TestLoadPriorYearFederalTax:
    """Tests for engine.tax.load_prior_year_federal_tax.

    The function resolves its cache path from
    ``engine.tax_return_pdf._PDF_TAX_CACHE_PATH`` — the same module attribute
    the writer (``save_pdf_tax_records``) uses — so tests redirect that
    attribute directly via ``monkeypatch.setattr``, the same mechanism
    tests/conftest.py's ``_redirect_cache_paths_to_tmp`` fixture relies on.
    """

    def test_redirected_cache_with_total_federal_tax_is_read(self, tmp_path, monkeypatch):
        """Proves the fix: monkeypatching _PDF_TAX_CACHE_PATH (the writer's
        constant) is now honoured by load_prior_year_federal_tax, closing the
        test-isolation hole where the function computed its own inline path."""
        import engine.tax_return_pdf as tax_return_pdf_module
        from engine.tax import load_prior_year_federal_tax

        cache = tmp_path / ".tax_pdf_cache.json"
        cache.write_text(json.dumps({"2024": {"total_federal_tax": 55_000.0}}))
        monkeypatch.setattr(tax_return_pdf_module, "_PDF_TAX_CACHE_PATH", cache)

        result = load_prior_year_federal_tax()
        assert result == pytest.approx(55_000.0)

    def test_real_cache_returns_zero_no_total_tax_field(self, tmp_path, monkeypatch):
        """A cache with MAGI components but no total_federal_tax → returns 0.0."""
        import engine.tax_return_pdf as tax_return_pdf_module
        from engine.tax import load_prior_year_federal_tax

        cache = tmp_path / ".tax_pdf_cache.json"
        cache.write_text(json.dumps({"2024": {"agi": 200_000.0, "magi": 205_000.0}}))
        monkeypatch.setattr(tax_return_pdf_module, "_PDF_TAX_CACHE_PATH", cache)

        result = load_prior_year_federal_tax()
        assert result == pytest.approx(0.0)

    def test_no_matching_key_in_cache_returns_zero(self, tmp_path, monkeypatch):
        """A cache with agi/magi keys but no total_federal_tax/total_tax/line_24 → returns 0.0."""
        import engine.tax_return_pdf as tax_return_pdf_module
        from engine.tax import load_prior_year_federal_tax

        cache = tmp_path / ".tax_pdf_cache.json"
        cache.write_text(json.dumps({"2023": {"agi": 150_000.0, "tax_exempt_interest": 500.0}}))
        monkeypatch.setattr(tax_return_pdf_module, "_PDF_TAX_CACHE_PATH", cache)

        result = load_prior_year_federal_tax()
        assert result == pytest.approx(0.0)

    @staticmethod
    def _load(tmp_path, monkeypatch, content):
        """Write ``content`` to a tmp cache, point the writer constant at it, and
        call the REAL load_prior_year_federal_tax (legacy no-arg path)."""
        import engine.tax_return_pdf as tax_return_pdf_module
        from engine.tax import load_prior_year_federal_tax

        cache = tmp_path / ".tax_pdf_cache.json"
        cache.write_text(content if isinstance(content, str) else json.dumps(content))
        monkeypatch.setattr(tax_return_pdf_module, "_PDF_TAX_CACHE_PATH", cache)
        return load_prior_year_federal_tax()

    def test_nested_total_federal_tax_key(self, tmp_path, monkeypatch):
        """Cache with nested year → total_federal_tax → returns float."""
        result = self._load(tmp_path, monkeypatch, {"2024": {"total_federal_tax": 42_500.0}})
        assert result == pytest.approx(42_500.0)

    def test_nested_line_24_key(self, tmp_path, monkeypatch):
        """Cache with nested year → line_24 → returns float."""
        result = self._load(tmp_path, monkeypatch, {"2023": {"line_24": 38_000.0}})
        assert result == pytest.approx(38_000.0)

    def test_most_recent_year_wins(self, tmp_path, monkeypatch):
        result = self._load(
            tmp_path,
            monkeypatch,
            {"2022": {"total_federal_tax": 1_000.0}, "2024": {"total_federal_tax": 2_000.0}},
        )
        assert result == pytest.approx(2_000.0)

    def test_nested_bad_value_falls_through_to_next_key(self, tmp_path, monkeypatch):
        """An unparseable total_federal_tax must not hide a usable line_24."""
        result = self._load(
            tmp_path,
            monkeypatch,
            {"2024": {"total_federal_tax": "n/a", "line_24": 1_234.0}},
        )
        assert result == pytest.approx(1_234.0)

    def test_flat_dict_fallback(self, tmp_path, monkeypatch):
        """Single-year (non-nested) cache is read through the flat fallback."""
        result = self._load(tmp_path, monkeypatch, {"line_24": 777.0})
        assert result == pytest.approx(777.0)

    def test_flat_dict_bad_value_returns_zero(self, tmp_path, monkeypatch):
        result = self._load(tmp_path, monkeypatch, {"total_federal_tax": "n/a"})
        assert result == pytest.approx(0.0)

    def test_malformed_json_returns_zero(self, tmp_path, monkeypatch):
        """Malformed JSON → returns 0.0 without raising."""
        result = self._load(tmp_path, monkeypatch, "{{not valid json")
        assert result == pytest.approx(0.0)

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason=(
            "audit-1018 F-51: engine/tax.py flat-dict fallback calls data.get() "
            "without an isinstance(data, dict) guard, so a top-level JSON list "
            "raises AttributeError. Latent only: the sole production caller passes "
            "tax_year and takes the guarded branch."
        ),
    )
    def test_top_level_list_returns_zero(self, tmp_path, monkeypatch):
        result = self._load(tmp_path, monkeypatch, [1, 2, 3])
        assert result == pytest.approx(0.0)


class TestBrokerageGainTaxStackWalk:
    """Verify brokerage_gain_tax uses LTCG stack-walk, not flat 0.15.

    Uses Household(grants=[]) to zero out ACME NQO option_income so
    that combined_gross is fully controlled by the test parameters.
    """

    def _single_year_brokerage_gain_tax(
        self,
        ordinary_taxable_income: float,
        realized_gains: float,
    ) -> float:
        """Return the preferential stack-walk tax for a given ordinary taxable
        income and realized-gains amount, by calling the REAL engine function
        (engine.scenario._pref_stack_tax) that run_scenario uses for
        brokerage_gain_tax, with MFJ thresholds/rates.
        """
        from engine.scenario import _pref_stack_tax
        from engine.tax import LTCG_RATES_MFJ, LTCG_THRESHOLDS_MFJ

        return _pref_stack_tax(
            ordinary_taxable_income,
            0.0,
            0.0,
            realized_gains,
            LTCG_THRESHOLDS_MFJ,
            LTCG_RATES_MFJ,
        )

    @staticmethod
    def _projected_year(*, survivor, yield_rate: float, year: int):
        """Run the REAL projection for a synthetic brokerage-only household and
        return the YearResult for ``year`` (no IRA/SS/grants, zero CPI, so the
        constant thresholds apply un-indexed)."""
        from engine.scenario import ConversionPlan, run_scenario
        from models.household import GrowthProfile, Household

        hh = Household(
            base_year=2026,
            your_age=55,
            spouse_age=53,
            your_ira=0.0,
            spouse_ira=0.0,
            your_roth=0.0,
            spouse_roth=0.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
            exercise_schedule=None,
            living_expenses=0.0,
            cpi_assumption=0.0,
            brokerage_start=3_000_000.0,
            brokerage_start_basis=3_000_000.0,
            brok_turnover=0.5,
            brokerage_growth=GrowthProfile(
                default_rate=0.07, yield_rate=yield_rate, qualified_fraction=1.0
            ),
            survivor=survivor,
        )
        result = run_scenario(hh, ConversionPlan(), end_age=60)
        return hh, next(y for y in result.years if y.year == year)

    @staticmethod
    def _worksheet_tax(yr, hh, thresholds) -> float:
        """Independent 0/15/20 worksheet over the year's own reported figures."""
        pref = yr.brokerage_growth * hh.brok_turnover + yr.brokerage_qual_div
        unused_ded = max(0.0, yr.total_deductions - yr.combined_gross)
        start = yr.taxable_income
        end = start + max(0.0, pref - unused_ded)
        at_15 = max(0.0, min(end, thresholds[1]) - max(start, thresholds[0]))
        at_20 = max(0.0, end - max(start, thresholds[1]))
        return at_15 * 0.15 + at_20 * 0.20

    def test_brokerage_gain_tax_all_in_15pct(self):
        """Small ordinary income + small gain → all gains taxed at 15%."""
        from engine.tax import LTCG_THRESHOLDS_MFJ

        # Ordinary income well below 0% ceiling; gains stay entirely in 15% band
        ordinary = LTCG_THRESHOLDS_MFJ[0] + 10_000  # just above 0% threshold
        gain = 50_000.0  # stays below 20% threshold
        result = self._single_year_brokerage_gain_tax(ordinary, gain)
        assert result == pytest.approx(gain * 0.15, rel=1e-9)

    def test_brokerage_gain_tax_straddles_15_to_20(self):
        """Ordinary income near 20% threshold + gain that pushes over → split tax."""
        from engine.tax import LTCG_THRESHOLDS_MFJ

        threshold_20 = LTCG_THRESHOLDS_MFJ[1]  # 613_700
        # Set ordinary income 10_000 below the 20% threshold
        ordinary = threshold_20 - 10_000
        gain = 30_000.0  # 10_000 in 15% band, 20_000 in 20% band
        result = self._single_year_brokerage_gain_tax(ordinary, gain)
        expected = 10_000 * 0.15 + 20_000 * 0.20
        assert result == pytest.approx(expected, rel=1e-9)

    def test_brokerage_gain_tax_entirely_above_20pct(self):
        """Ordinary income already above 20% threshold → all gains at 20%."""
        from engine.tax import LTCG_THRESHOLDS_MFJ

        ordinary = LTCG_THRESHOLDS_MFJ[1] + 50_000  # above 613_700
        gain = 100_000.0
        result = self._single_year_brokerage_gain_tax(ordinary, gain)
        assert result == pytest.approx(gain * 0.20, rel=1e-9)
        # Confirm this would have been wrong under the old flat-rate approach
        old_flat_rate_tax = gain * 0.15
        assert result > old_flat_rate_tax

    def test_ltcg_stack_start_is_ordinary_income_not_reduced(self):
        """Regression: stack-walk start must be yr.taxable_income, not
        yr.taxable_income - realized_gains.

        Scenario: ordinary_taxable = 80_000, realized_gains = 30_000.
        2026 MFJ 0%-LTCG threshold is LTCG_THRESHOLDS_MFJ[0] (≈98_900).

        Buggy formula:  ltcg_start = 80_000 - 30_000 = 50_000
                        ltcg_end   = 50_000 + 30_000 = 80_000
                        → entire $30K below threshold → tax = $0.

        Fixed formula:  ltcg_start = 80_000
                        ltcg_end   = 80_000 + 30_000 = 110_000
                        → $18_900 in 0% band, $11_100 at 15% → tax = $1_665.
        """
        from engine.tax import LTCG_THRESHOLDS_MFJ

        ordinary = 80_000.0
        gain = 30_000.0
        threshold_0 = LTCG_THRESHOLDS_MFJ[0]  # ≈98_900 for 2026 MFJ

        # Real engine stack-walk
        result = self._single_year_brokerage_gain_tax(ordinary, gain)

        # The $30K gain straddles the 0%→15% boundary:
        # gain_in_15pct = (ordinary + gain) - threshold_0 = 110_000 - 98_900 = 11_100
        gain_taxed_at_15 = (ordinary + gain) - threshold_0
        expected = gain_taxed_at_15 * 0.15
        assert result == pytest.approx(expected, rel=1e-9)
        assert result > 0.0, "Fixed formula must produce non-zero LTCG tax here"

        # The buggy start (ordinary - gain) would have kept the whole gain in the 0% band.
        buggy_result = self._single_year_brokerage_gain_tax(ordinary - gain, gain)
        assert buggy_result == pytest.approx(0.0), "Buggy start must yield $0 (regression anchor)"
        assert result > buggy_result, "Fix must increase LTCG tax vs buggy start"

    def test_qual_div_included_in_ltcg_stack_mfj(self):
        """C-5 regression: qualified dividends taxed at LTCG preferential rates (IRC §1(h)(11)).

        MFJ ordinary taxable $100K, realized_gains $0, qual_div $10K.
        Pre-fix: $0 LTCG tax (qual_div not in stack).
        Post-fix: stack walks $100K → $110K; LTCG_THRESHOLDS_MFJ[0]=98_900 so
        $10K is entirely above 0%-band → 15% = $1_500.
        """
        # Real engine stack-walk: $10K of eligible income above the 98_900 threshold → 15%
        result = self._single_year_brokerage_gain_tax(100_000.0, 10_000.0)
        assert result == pytest.approx(1_500.0, rel=1e-9)

        # Pre-fix (no qual_div in stack) would have returned $0
        assert self._single_year_brokerage_gain_tax(100_000.0, 0.0) == pytest.approx(0.0)

    def test_run_scenario_stacks_qualified_dividends_in_gain_tax(self):
        """C-5 wired into run_scenario: qualified dividends are part of the
        preferential stack that yields yr.brokerage_gain_tax (MFJ year)."""
        from engine.tax import LTCG_THRESHOLDS_MFJ

        hh, with_div = self._projected_year(survivor=None, yield_rate=0.02, year=2027)
        assert with_div.filing_status == "MFJ"
        assert with_div.brokerage_qual_div > 0.0
        expected = self._worksheet_tax(with_div, hh, LTCG_THRESHOLDS_MFJ)
        assert expected > 0.0
        assert with_div.brokerage_gain_tax == pytest.approx(expected, rel=1e-6)

    def test_single_survivor_uses_single_thresholds(self):
        """C-6 regression: survivor (Single) uses LTCG_THRESHOLDS_SINGLE not MFJ.

        Single ordinary taxable $60K, realized_gains $30K, qual_div $0.
        MFJ 0%-ceiling ($98_900): stack $60K→$90K entirely below → $0 tax (pre-fix).
        Single 0%-ceiling ($49_450, 2026 Rev. Proc. 2025-32): stack starts $60K (above ceiling) → all $30K at 15%
        = $4_500 (post-fix).
        """
        from engine.scenario import _pref_stack_tax
        from engine.tax import (
            LTCG_RATES_MFJ,
            LTCG_RATES_SINGLE,
            LTCG_THRESHOLDS_MFJ,
            LTCG_THRESHOLDS_SINGLE,
        )

        # Real stack-walk function with each filing status's constants
        single = _pref_stack_tax(
            60_000.0, 0.0, 0.0, 30_000.0, LTCG_THRESHOLDS_SINGLE, LTCG_RATES_SINGLE
        )
        mfj = _pref_stack_tax(60_000.0, 0.0, 0.0, 30_000.0, LTCG_THRESHOLDS_MFJ, LTCG_RATES_MFJ)
        assert single == pytest.approx(4_500.0, rel=1e-9)
        assert mfj == pytest.approx(0.0), "MFJ thresholds yield $0 (pre-fix anchor)"

    def test_run_scenario_survivor_year_uses_single_thresholds(self):
        """C-6 wired into run_scenario: a survivor year (Single) must price
        yr.brokerage_gain_tax on Single thresholds, a joint year on MFJ."""
        from engine.tax import LTCG_THRESHOLDS_MFJ, LTCG_THRESHOLDS_SINGLE
        from models.household import SurvivorScenario

        surv = SurvivorScenario(who_dies="you", death_year=2026)
        hh, yr = self._projected_year(survivor=surv, yield_rate=0.02, year=2027)
        assert yr.filing_status == "Single"
        on_single = self._worksheet_tax(yr, hh, LTCG_THRESHOLDS_SINGLE)
        on_mfj = self._worksheet_tax(yr, hh, LTCG_THRESHOLDS_MFJ)
        assert on_single > on_mfj + 1_000.0, "precondition: the two statuses must differ"
        assert yr.brokerage_gain_tax == pytest.approx(on_single, rel=1e-6)

    def test_single_survivor_ytd_uses_single_thresholds(self):
        """YTD LTCG (b) regression: survivor (Single) uses LTCG_THRESHOLDS_SINGLE
        in the YTD walk, mirroring the projected-LTCG fix from PR #119.

        Same numerical setup as test_single_survivor_uses_single_thresholds:
        ordinary $60K + ltcg_ytd $30K. MFJ would yield $0; Single yields $4,500.
        """
        from engine.tax import estimate_ytd_federal_tax

        hh, ytd = self._single_filer_ytd(ordinary_taxable=60_000.0, ltcg=30_000.0)
        assert estimate_ytd_federal_tax(ytd, hh).ltcg_tax == pytest.approx(4_500.0, rel=1e-9)

        # The same figures filed jointly sit wholly in the 0% band.
        mfj_hh, mfj_ytd = self._single_filer_ytd(ordinary_taxable=60_000.0, ltcg=30_000.0, mfj=True)
        assert estimate_ytd_federal_tax(mfj_ytd, mfj_hh).ltcg_tax == pytest.approx(0.0)

    @staticmethod
    def _single_filer_ytd(
        *, ordinary_taxable: float, ltcg: float, qual_div: float = 0.0, mfj: bool = False
    ):
        """Synthetic age-50 household + YTD snapshot whose ordinary TAXABLE income
        is exactly ``ordinary_taxable`` (wages = taxable + standard deduction)."""
        from engine.tax import STD_DEDUCTION_MFJ, STD_DEDUCTION_SINGLE
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            your_age=50,
            spouse_age=50,
            cpi_assumption=0.0,
            filing_status="MFJ" if mfj else "Single",
            grants=[],
        )
        std_ded = STD_DEDUCTION_MFJ if mfj else STD_DEDUCTION_SINGLE
        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=ordinary_taxable + std_ded,
            ltcg_ytd=ltcg,
            qualified_dividends_ytd=qual_div,
        )
        return hh, ytd

    def test_qual_div_and_single_thresholds_compose(self):
        """C-5 + C-6 combined: Single survivor with both qual_div and realized_gains.

        Single ordinary taxable $40K, realized_gains $5K, qual_div $8K.
        Single 0%-ceiling = LTCG_THRESHOLDS_SINGLE[0] = 49_450 (2026 Rev. Proc. 2025-32).
        ltcg_eligible = $13K; stack: $40K → $53K.
        $53K - $49_450 = $3_550 above 0%-band → all at 15% = $532.50.
        """
        from engine.tax import estimate_ytd_federal_tax

        hh, ytd = self._single_filer_ytd(ordinary_taxable=40_000.0, ltcg=5_000.0, qual_div=8_000.0)
        result = estimate_ytd_federal_tax(ytd, hh).ltcg_tax
        assert result == pytest.approx(532.50, rel=1e-9)


class TestEstimateYTDEffectiveRateDenominator:
    """MU1-F02: estimate_ytd_federal_tax effective_rate must use niit_magi_with_ss
    (muni-EXCLUDED) as denominator per IRC §1411(d)(3).

    Pre-fix (bug): denominator was magi_ytd (muni-included), understating the displayed rate.
    Post-fix: denominator is niit_magi_with_ss = niit_magi_ytd + tss (muni-excluded).

    Including muni interest in the denominator inflates it, making the effective rate
    appear lower than it actually is. The correct base excludes tax-exempt interest.
    """

    def test_effective_rate_uses_niit_magi_not_full_magi(self) -> None:
        """effective_rate denominator is niit_magi_ytd (muni-excluded), not magi_ytd.

        Construct a YTDSnapshot with muni interest so the two MAGI variants differ.
        Assert effective_rate == total / niit_magi_ytd (muni-excluded, per §1411(d)(3)),
        which is strictly greater than total / magi_ytd (muni-included, larger denom).
        """
        from engine.tax import estimate_ytd_federal_tax
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        hh = Household(
            your_age=66,
            spouse_age=66,
            base_year=2026,
            cpi_assumption=0.0,
            your_ira=500_000.0,
            spouse_ira=500_000.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
        )
        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=150_000.0,
            tax_exempt_interest_ytd=30_000.0,  # muni: causes magi_ytd != niit_magi_ytd
        )

        estimate = estimate_ytd_federal_tax(ytd, hh, combined_ss=0.0)

        # Derive the two candidate denominators directly from the snapshot
        full_magi = ytd.magi_ytd  # 150_000 + 30_000 = 180_000
        niit_magi = ytd.niit_magi_ytd  # 150_000 (muni stripped)

        assert full_magi > niit_magi, "Precondition: muni must cause MAGI variants to differ"
        assert niit_magi > 0

        # MU1-F02 fix: denominator is niit_magi_ytd (tss=0 here since combined_ss=0)
        expected_rate = estimate.total / niit_magi
        wrong_rate = estimate.total / full_magi  # pre-fix (inflated denom → lower rate)

        assert estimate.effective_rate == pytest.approx(expected_rate, rel=1e-9), (
            f"effective_rate should use niit_magi denominator (MU1-F02): "
            f"got {estimate.effective_rate:.6f}, expected {expected_rate:.6f}"
        )
        assert estimate.effective_rate > wrong_rate, (
            "effective_rate must be strictly greater than total/full_magi "
            "(muni-exclusive denom is smaller → higher rate)"
        )


class TestSSCapAndBracketEdges:
    """C6 regression: §86(a)(1) tier-1 SS cap (tax-core-1) and strict-'<' bracket
    edge behavior for marginal_rate (tax-core-3)."""

    def test_tier1_band_applies_half_benefits_cap(self) -> None:
        # MFJ, combined_ss=4000, other=36000 -> provisional=38000 (tier-1 band).
        # §86(a)(1) = min(0.5*(38000-32000), 0.5*4000) = min(3000, 2000) = 2000.
        from engine.tax import taxable_ss

        assert taxable_ss(4000, 36000, "MFJ") == 2000.0

    def test_tier1_band_uncapped_case_unchanged(self) -> None:
        # When half-the-excess is below half-benefits, the cap is inert.
        # combined_ss=40000, other=13000 -> provisional=33000 (tier-1), half-excess
        # =0.5*(33000-32000)=500, cap=0.5*40000=20000 -> min=500 (unchanged by edit).
        from engine.tax import taxable_ss

        assert taxable_ss(40000, 13000, "MFJ") == 500.0

    def test_marginal_rate_strict_at_ceiling_mfj(self) -> None:
        from engine.tax import BRACKETS_MFJ, index_bracket_list, marginal_rate
        from engine.tax_indexing import BASE_YEAR, DEFAULT_CPI

        # At BASE_YEAR the index factor is 1.0, so bracket ceilings are the raw values.
        brackets = index_bracket_list(BRACKETS_MFJ, BASE_YEAR, DEFAULT_CPI)
        c0, r0 = brackets[0]
        _, r1 = brackets[1]
        # Exactly on the first ceiling -> next bracket's rate.
        assert marginal_rate(c0, year=BASE_YEAR, cpi=DEFAULT_CPI) == r1
        # Just below -> current bracket's rate.
        assert marginal_rate(c0 - 1, year=BASE_YEAR, cpi=DEFAULT_CPI) == r0


class TestEstimateYtdSsOmissionCluster:
    """Audit 0705 #4 — taxable SS must enter NIIT MAGI, the senior-bonus phase-out base,
    and the effective-rate denominator in estimate_ytd_federal_tax (mirrors headroom engine)."""

    def test_effective_rate_denominator_includes_taxable_ss(self):
        """H3: effective_rate denominator must include taxable SS (numerator already taxes it)."""
        from engine.tax import estimate_ytd_federal_tax, taxable_ss
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(tax_year=2026, wages_ytd=100_000.0)
        hh = Household(
            your_age=60,
            spouse_age=60,
            base_year=2026,
            cpi_assumption=0.0,
            your_ira=500_000.0,
            spouse_ira=500_000.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
        )
        combined_ss = 40_000.0
        est = estimate_ytd_federal_tax(ytd, hh, combined_ss=combined_ss)
        tss = taxable_ss(combined_ss, ytd.magi_ytd, filing_status="MFJ")

        assert tss > 0
        assert est.effective_rate == pytest.approx(est.total / (ytd.magi_ytd + tss))
        # SS sits in the denominator → reported rate is strictly below the SS-free denominator
        assert est.effective_rate < est.total / ytd.magi_ytd

    def test_niit_magi_includes_taxable_ss(self):
        """H2: NIIT MAGI must include taxable SS (AGI includes it per §86); can only under-apply otherwise."""
        from engine.niit import niit
        from engine.tax import estimate_ytd_federal_tax, taxable_ss
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        # niit_magi_ytd = 235k wages + 15k LTCG = 250k, exactly at the MFJ threshold.
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=235_000.0, ltcg_ytd=15_000.0)
        hh = Household(
            your_age=60,
            spouse_age=60,
            base_year=2026,
            cpi_assumption=0.0,
            your_ira=500_000.0,
            spouse_ira=500_000.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
        )
        combined_ss = 40_000.0
        est = estimate_ytd_federal_tax(ytd, hh, combined_ss=combined_ss)
        tss = taxable_ss(combined_ss, ytd.magi_ytd, filing_status="MFJ")
        nii = ytd.ltcg_ytd  # stcg/dividends/interest all 0 → NII == LTCG

        expected = niit(ytd.niit_magi_ytd + tss, nii, filing_status="MFJ")
        buggy = niit(ytd.niit_magi_ytd, nii, filing_status="MFJ")
        assert tss > 0
        assert expected > buggy  # the SS pushes MAGI over the threshold
        assert est.niit == pytest.approx(expected)

    def test_senior_bonus_phaseout_base_includes_taxable_ss(self):
        """Panel #4: OBBBA senior-bonus phase-out MAGI must include taxable SS (§151(d)(5) sits on AGI)."""
        from unittest.mock import patch

        import engine.tax as tax_mod
        from engine.tax import taxable_ss
        from models.household import Household
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(tax_year=2026, wages_ytd=120_000.0)
        hh = Household(
            your_age=66,
            spouse_age=0,
            base_year=2026,
            cpi_assumption=0.0,
            your_ira=500_000.0,
            spouse_ira=0.0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            grants=[],
            filing_status="Single",
        )
        combined_ss = 40_000.0
        tss = taxable_ss(combined_ss, ytd.magi_ytd, filing_status="Single")

        captured: dict = {}
        real = tax_mod.senior_bonus_deduction

        def _spy(*args, **kwargs):
            captured["magi"] = args[2]  # 3rd positional arg is the phase-out MAGI
            return real(*args, **kwargs)

        with patch.object(tax_mod, "senior_bonus_deduction", side_effect=_spy):
            tax_mod.estimate_ytd_federal_tax(ytd, hh, combined_ss=combined_ss)

        assert tss > 0
        assert captured["magi"] == pytest.approx(ytd.niit_magi_ytd + tss)


class TestDeductionsFilingGate:
    """Audit 0705 follow-up (SS-IRMAA-DBL twin): a Single filer's phantom spouse must not add a
    second senior standard-deduction extra. deductions() must gate the spouse extra on MFJ.

    Audit 0706 update: the survivor scenario places the live filer's age in the spouse_age slot
    (ya_eff=0 for the deceased). deductions() uses max(your_age, spouse_age) for non-MFJ so the
    survivor gets exactly one senior extra. The Setup gate guarantees that a non-survivor Single
    filer always has spouse_age=0, so max() correctly resolves to the filer's own age in all
    non-survivor paths (no phantom double-extra risk).
    """

    def test_single_filer_ignores_phantom_spouse_senior_extra(self):
        import pytest

        from engine.tax import SENIOR_EXTRA_SINGLE, STD_DEDUCTION_SINGLE, deductions

        # Both ages >= 65 but Single → max() picks the higher age; only one extra granted (not two).
        d = deductions(75, 69, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, filing_status="Single")
        assert d == pytest.approx(STD_DEDUCTION_SINGLE + SENIOR_EXTRA_SINGLE)

    def test_single_primary_under_65_gets_no_extra(self):
        import pytest

        from engine.tax import SENIOR_EXTRA_SINGLE, STD_DEDUCTION_SINGLE, deductions

        # Non-survivor Single filer, age 60: Setup gate zeroes spouse_age (not a survivor).
        # max(60, 0) = 60 < 65 → no senior extra. This is the realistic application shape.
        d = deductions(60, 0, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, filing_status="Single")
        assert d == pytest.approx(STD_DEDUCTION_SINGLE)

    def test_mfj_default_still_counts_both_senior_extras(self):
        import pytest

        from engine.tax import SENIOR_EXTRA_MFJ, STD_DEDUCTION_MFJ, deductions

        # MFJ (the default) is unchanged: both spouses' senior extras count.
        d = deductions(75, 69)
        assert d == pytest.approx(STD_DEDUCTION_MFJ + 2 * SENIOR_EXTRA_MFJ)
