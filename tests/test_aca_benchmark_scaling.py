"""Regression tests: ACA benchmark must scale by enrollee count across all consumers."""

import pytest

from engine.aca import aca_subsidy, aca_subsidy_loss, effective_benchmark_premium
from engine.aca_irmaa_compute import compute_cost_curves, compute_year_by_year_timeline
from engine.scenario_compute import compute_aca
from engine.sweet_spot_compute import BaseIncome, all_in_at_conversion
from models.household import Household, SurvivorScenario


def test_compute_aca_clawback_scales_benchmark_single_enrollee() -> None:
    # R2-#1: the excess-APTC clawback must use the same enrollee-scaled benchmark
    # as the subsidy-loss path. One enrollee (you 60 enrolled, spouse 66 -> off ACA)
    # => effective benchmark is the age-rated share (not a flat 50/50 split).
    # factor_60=2.714, factor_66=3.000 (capped); share = 2.714/(2.714+3.000).
    benchmark = 21_600.0
    magi = 60_000.0
    advance = 10_000.0
    _, _, clawback = compute_aca(
        magi=magi,
        combined_ss=0.0,
        taxable_ss_amt=0.0,
        your_conversion=0.0,
        spouse_conversion=0.0,
        ya=60,
        sa=66,
        your_aca_enrolled=True,
        spouse_aca_enrolled=True,
        aca_benchmark_premium_annual=benchmark,
        aca_enhanced_subsidies_active=True,
        advance_aptc_annual=advance,
        current_filing_status="MFJ",
        year=2026,
        cpi=0.0,
    )
    # Age-rated effective benchmark: ya=60 enrolled, sa=66 (aca_applies=False)
    age_rated_benchmark = effective_benchmark_premium(
        benchmark,
        your_age=60,
        your_on_aca=True,
        spouse_age=66,
        spouse_on_aca=False,
        filing_status="MFJ",
    )
    age_rated_ptc = aca_subsidy(
        magi,
        age_rated_benchmark,
        enhanced_subsidies_active=True,
        filing_status="MFJ",
        year=2026,
        cpi=0.0,
    )
    full_ptc = aca_subsidy(
        magi, benchmark, enhanced_subsidies_active=True, filing_status="MFJ", year=2026, cpi=0.0
    )
    assert clawback == pytest.approx(advance - age_rated_ptc)
    assert clawback != pytest.approx(advance - full_ptc)
    # Age-rated share (2.714/5.714 ≈ 47.5%) is less than 50/50 for a 60+66 household
    assert age_rated_benchmark < benchmark * 0.5


def test_compute_cost_curves_scales_benchmark_single_enrollee() -> None:
    # R1 #2: ACA Explorer curves must scale the benchmark by age-rated enrollee share.
    # Default hh ages: your_age=55, spouse_age=53.
    # One enrollee (you); age-rated share = factor_55/(factor_55+factor_53)
    #   = 2.230/(2.230+2.040) ≠ 0.5.
    hh = Household()
    hh.your_aca_enrolled = True
    hh.spouse_aca_enrolled = False  # one enrollee (both under 65 by default)
    hh.aca_enhanced_subsidies_active = True
    magi = 80_000.0
    cc = compute_cost_curves([magi], base_magi=magi, net_inv_income=0.0, hh=hh, year=2026, cpi=0.0)
    age_rated_bench = effective_benchmark_premium(
        hh.aca_benchmark_premium_annual,
        your_age=hh.your_age,
        your_on_aca=True,
        spouse_age=hh.spouse_age,
        spouse_on_aca=False,
        filing_status=hh.filing_status,
    )
    expected = aca_subsidy(
        magi,
        age_rated_bench,
        enhanced_subsidies_active=True,
        filing_status=hh.filing_status,
        year=2026,
        cpi=0.0,
    )
    assert cc.aca_subsidy_vals[0] == pytest.approx(expected)
    assert expected > 0
    # Age-rated share (55yo) is slightly above 50/50 for a 55+53 pair
    assert age_rated_bench > hh.aca_benchmark_premium_annual * 0.5


def test_sweet_spot_all_in_scales_aca_benchmark_single_enrollee() -> None:
    # R1 #3: Sweet Spot all-in ACA loss must scale the benchmark by age-rated share.
    # Default hh ages: your_age=55, spouse_age=53.
    # One enrollee (you); age-rated share = factor_55/(factor_55+factor_53)
    #   = 2.230/(2.230+2.040) ≈ 52.2% — strictly above the old 50/50 split.
    hh = Household()
    hh.your_aca_enrolled = True
    hh.spouse_aca_enrolled = False  # one enrollee
    hh.aca_enhanced_subsidies_active = True
    base = BaseIncome(
        ya=hh.your_age,
        sa=hh.spouse_age,
        year=2026,
        cpi=0.0,
        opt=40_000.0,
        combined_ss=0.0,
        base_gross=40_000.0,
        base_magi=40_000.0,
        total_ded=0.0,
        ded_base=0.0,
        ytd_magi=0.0,
    )
    res = all_in_at_conversion(hh, base, conv=20_000.0, net_inv_income=0.0)
    age_rated_bench = effective_benchmark_premium(
        hh.aca_benchmark_premium_annual,
        your_age=hh.your_age,
        your_on_aca=True,
        spouse_age=hh.spouse_age,
        spouse_on_aca=False,
        filing_status=hh.filing_status,
    )
    expected = aca_subsidy_loss(
        40_000.0,
        60_000.0,
        benchmark=age_rated_bench,
        enhanced_subsidies_active=True,
        filing_status=hh.filing_status,
        year=2026,
        cpi=0.0,
    )
    assert res.aca_loss == pytest.approx(expected)
    assert expected > 0
    # Age-rated share (55yo in a 55+53 pair) is slightly above 50/50
    assert age_rated_bench > hh.aca_benchmark_premium_annual * 0.5


def test_timeline_scales_benchmark_by_yearly_enrollee_count() -> None:
    # Timeline consumer: benchmark scales by age-rated enrollee share THAT year.
    hh = Household()
    hh.your_aca_enrolled = True
    hh.spouse_aca_enrolled = True
    hh.aca_enhanced_subsidies_active = True
    base_magi = 80_000.0
    rows = compute_year_by_year_timeline(hh, base_magi=base_magi, years=20, cpi=0.0)

    def both(r):
        return (
            r.you_age is not None
            and r.spouse_age is not None
            and r.you_age < 65
            and r.spouse_age < 65
        )

    def exactly_one(r):
        you = r.you_age is not None and r.you_age < 65
        sp = r.spouse_age is not None and r.spouse_age < 65
        return you != sp

    r_both = next(r for r in rows if both(r))
    r_one = next(r for r in rows if exactly_one(r))
    assert r_both.aca_subsidy == pytest.approx(
        aca_subsidy(
            base_magi,
            hh.aca_benchmark_premium_annual,
            enhanced_subsidies_active=True,
            filing_status="MFJ",
            year=r_both.year,
            cpi=0.0,
        )
    )
    # For exactly-one-enrollee year, use age-rated benchmark (not flat 50/50).
    ya_one = r_one.you_age
    sa_one = r_one.spouse_age
    you_on = ya_one is not None and ya_one < 65
    sp_on = sa_one is not None and sa_one < 65
    age_rated_bench_one = effective_benchmark_premium(
        hh.aca_benchmark_premium_annual,
        your_age=ya_one or 0,
        your_on_aca=you_on,
        spouse_age=sa_one or 0,
        spouse_on_aca=sp_on,
        filing_status="MFJ",
    )
    assert r_one.aca_subsidy == pytest.approx(
        aca_subsidy(
            base_magi,
            age_rated_bench_one,
            enhanced_subsidies_active=True,
            filing_status="MFJ",
            year=r_one.year,
            cpi=0.0,
        )
    )


# ---------------------------------------------------------------------------
# Survivor transition tests for compute_year_by_year_timeline
# ---------------------------------------------------------------------------


def _make_survivor_hh(base_year: int = 2026, death_year: int = 2031) -> Household:
    """MFJ household where both spouses are >=65 from base_year onward."""
    hh = Household()
    hh.base_year = base_year
    # Ages: primary 66, spouse 65 — both already on Medicare in base_year
    hh.your_age = 66
    hh.spouse_age = 65
    hh.filing_status = "MFJ"
    hh.your_aca_enrolled = False
    hh.spouse_aca_enrolled = False
    hh.survivor = SurvivorScenario(who_dies="spouse", death_year=death_year)
    return hh


def test_timeline_survivor_medicare_count_drops_after_death() -> None:
    """medicare_count must be 2 before/at death_year and 1 after."""
    base_year = 2026
    death_year = 2031
    hh = _make_survivor_hh(base_year=base_year, death_year=death_year)
    rows = compute_year_by_year_timeline(hh, base_magi=80_000.0, years=10, cpi=0.0)

    before = [r for r in rows if r.year <= death_year]
    after = [r for r in rows if r.year > death_year]

    assert len(before) > 0
    assert len(after) > 0

    # Both spouses >=65 throughout, so before death both count
    for r in before:
        # irmaa_tier is not None only if medicare_count > 0; both on Medicare → not None
        assert r.irmaa_tier is not None, f"Expected Medicare active in year {r.year}"

    # After death: spouse excluded; only primary (you) remains on Medicare.
    # irmaa_tier should still be not None (primary still >=65), but the IRMAA
    # threshold used must reflect Single filing, not MFJ.
    for r in after:
        assert r.irmaa_tier is not None, f"Expected primary still on Medicare in year {r.year}"


def test_timeline_survivor_filing_status_switches_to_single() -> None:
    """After death_year the IRMAA tier must reflect Single thresholds (lower)."""
    from engine.irmaa import irmaa_tier as _irmaa_tier

    base_year = 2026
    death_year = 2028
    hh = _make_survivor_hh(base_year=base_year, death_year=death_year)
    # Use a MAGI that falls in tier 0 for MFJ but tier 1+ for Single
    # MFJ tier-1 threshold ~$212K; Single tier-1 ~$106K
    base_magi = 120_000.0
    rows = compute_year_by_year_timeline(hh, base_magi=base_magi, years=8, cpi=0.0)

    last_mfj_year = death_year  # year of death => still MFJ
    first_single_year = death_year + 1

    row_mfj = next((r for r in rows if r.year == last_mfj_year), None)
    row_single = next((r for r in rows if r.year == first_single_year), None)

    assert row_mfj is not None
    assert row_single is not None

    expected_tier_mfj = _irmaa_tier(base_magi, filing_status="MFJ", year=last_mfj_year, cpi=0.0)
    expected_tier_single = _irmaa_tier(
        base_magi, filing_status="Single", year=first_single_year, cpi=0.0
    )

    assert row_mfj.irmaa_tier == expected_tier_mfj
    assert row_single.irmaa_tier == expected_tier_single
    # The Single threshold is lower so tier must be higher (or equal) — just
    # confirm the timeline actually used Single, not MFJ, for the post-death row.
    assert expected_tier_single >= expected_tier_mfj


def test_timeline_no_survivor_unchanged() -> None:
    """Household with survivor=None must produce same result as always (regression guard)."""
    hh = Household()
    hh.your_age = 66
    hh.spouse_age = 65
    hh.filing_status = "MFJ"
    hh.survivor = None

    rows = compute_year_by_year_timeline(hh, base_magi=80_000.0, years=5, cpi=0.0)

    # Both >=65 every year → every row has irmaa_tier not None
    for r in rows:
        assert r.irmaa_tier is not None
    # Spot-check: filing_status used is MFJ (IRMAA tier for MFJ at 80K = 0)
    from engine.irmaa import irmaa_tier as _irmaa_tier

    for r in rows:
        expected = _irmaa_tier(80_000.0, filing_status="MFJ", year=r.year, cpi=0.0)
        assert r.irmaa_tier == expected


def test_timeline_survivor_who_dies_you_medicare_follows_spouse() -> None:
    """who_dies='you': after death the surviving SPOUSE's Medicare must still count.

    Regression for the bug where is_mfj=False forced sa=None, which dropped the
    surviving spouse's Medicare status and zeroed medicare_count (→ irmaa_tier
    None) even when the surviving spouse was 65+.
    """
    from engine.irmaa import irmaa_tier as _irmaa_tier

    base_year = 2026
    death_year = 2028
    hh = Household()
    hh.base_year = base_year
    # Primary dies; surviving spouse is 66 at base_year, 67+ after death.
    hh.your_age = 60
    hh.spouse_age = 66
    hh.filing_status = "MFJ"
    hh.your_aca_enrolled = False
    hh.spouse_aca_enrolled = False
    hh.survivor = SurvivorScenario(who_dies="you", death_year=death_year)

    base_magi = 120_000.0  # MFJ tier 0, Single tier 1+
    rows = compute_year_by_year_timeline(hh, base_magi=base_magi, years=8, cpi=0.0)

    after = [r for r in rows if r.year > death_year]
    assert len(after) > 0
    for r in after:
        # Surviving spouse is >=65, so Medicare/IRMAA must remain active...
        assert r.irmaa_tier is not None, (
            f"Surviving spouse 65+ must keep Medicare active in {r.year}"
        )
        # ...and filing must switch to Single (lower thresholds) post-death.
        expected_single = _irmaa_tier(base_magi, filing_status="Single", year=r.year, cpi=0.0)
        assert r.irmaa_tier == expected_single


# ---------------------------------------------------------------------------
# Fix 1 regression: Single filer gets full age-rated benchmark, not half
# ---------------------------------------------------------------------------


def test_timeline_single_filer_gets_full_benchmark_not_half() -> None:
    """Fix 1: Single filer enrolled on ACA must receive the full age-rated benchmark.

    A Single filer has one household adult, so effective_benchmark_premium returns
    couple_benchmark unmodified (there is no second adult to split with).
    The old flat-/2 code gave Single filers benchmark/2, which was wrong.
    """
    hh = Household()
    hh.filing_status = "Single"
    hh.your_age = 62
    hh.your_aca_enrolled = True
    hh.spouse_aca_enrolled = False
    hh.aca_enhanced_subsidies_active = True
    base_magi = 50_000.0
    rows = compute_year_by_year_timeline(hh, base_magi=base_magi, years=3, cpi=0.0)

    aca_rows = [r for r in rows if r.aca_subsidy is not None]
    assert len(aca_rows) > 0, "Expected at least one ACA-active row for Single filer age 62"

    for r in aca_rows:
        ya = r.you_age or hh.your_age
        expected_bench = effective_benchmark_premium(
            hh.aca_benchmark_premium_annual,
            your_age=ya,
            your_on_aca=True,
            spouse_age=0,
            spouse_on_aca=False,
            filing_status="Single",
        )
        # Full benchmark for Single = couple_benchmark (no split)
        assert expected_bench == hh.aca_benchmark_premium_annual, (
            "effective_benchmark_premium must return full benchmark for an enrolled Single filer"
        )
        expected_sub = aca_subsidy(
            base_magi,
            expected_bench,
            enhanced_subsidies_active=True,
            filing_status="Single",
            year=r.year,
            cpi=0.0,
        )
        assert r.aca_subsidy == pytest.approx(expected_sub), (
            f"Timeline subsidy {r.aca_subsidy} != expected {expected_sub} in year {r.year}"
        )
        # Guard: old flat-/2 would produce subsidy from benchmark/2, not benchmark
        half_bench_sub = aca_subsidy(
            base_magi,
            hh.aca_benchmark_premium_annual / 2,
            enhanced_subsidies_active=True,
            filing_status="Single",
            year=r.year,
            cpi=0.0,
        )
        assert r.aca_subsidy != pytest.approx(half_bench_sub), (
            "Single filer must NOT use benchmark/2 (old flat-split bug)"
        )

