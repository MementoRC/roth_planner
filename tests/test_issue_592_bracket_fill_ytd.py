"""Issue #592: add_bracket_fill_withdrawals must size its fill with the caller's YTD.

The function runs an internal base scenario to measure each post-RMD year's
bracket room. Without the year-to-date income, the base year's room ignored
income already realised, so the "Fill 12% + Bracket Fill" preset withdrew too
much and landed the base year above the target bracket once the real
projection (which does carry the YTD) ran.
"""

from engine.scenario import run_scenario
from engine.scenario_autofill import add_bracket_fill_withdrawals, auto_fill_12
from engine.scenario_compare import build_scenario
from engine.tax import indexed_bracket_ceiling
from models.household import Household
from models.ytd_income import YTDSnapshot

BASE_YEAR = 2026


def _post_rmd_hh() -> Household:
    # Both already past RMD start in the base year, so the bracket fill acts on
    # the base year itself -- the only year the YTD snapshot applies to.
    # No brokerage: a run without YTD adds FORECAST dividends in the base year
    # (a run with YTD takes them from the snapshot), which would blur the
    # with/without comparison below.
    return Household(
        brokerage_start=0.0,
        your_age=76,
        spouse_age=74,
        base_year=BASE_YEAR,
        cpi_assumption=0.0,
        your_ira=800_000.0,
        spouse_ira=300_000.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        grants=[],
    )


def _ytd() -> YTDSnapshot:
    return YTDSnapshot(tax_year=BASE_YEAR, wages_ytd=40_000)


def _ceiling_12(filing_status: str) -> float:
    return indexed_bracket_ceiling(1, year=BASE_YEAR, cpi=0.0, filing_status=filing_status)


def test_bracket_fill_with_ytd_stays_within_target_bracket() -> None:
    hh = _post_rmd_hh()
    ytd = _ytd()
    plan = add_bracket_fill_withdrawals(hh, auto_fill_12(hh, ytd), target_bracket=0.12, ytd=ytd)

    # The fill must still do something in the base year, or the check below is vacuous.
    assert plan.extra_withdrawals.get(BASE_YEAR, 0.0) > 0.0

    result = run_scenario(hh, plan, "fill", end_age=95, ytd=ytd)
    base = next(y for y in result.years if y.year == BASE_YEAR)
    assert base.taxable_income <= _ceiling_12(base.filing_status) + 1.0


def test_fill_with_ytd_lands_on_the_ceiling_and_draws_less() -> None:
    """With the YTD, the fill tops the base year up to exactly the 12% ceiling,
    and needs a smaller withdrawal than a fill that ignores the realised income."""
    hh = _post_rmd_hh()
    ytd = _ytd()
    without = add_bracket_fill_withdrawals(hh, auto_fill_12(hh), target_bracket=0.12)
    with_ytd = add_bracket_fill_withdrawals(hh, auto_fill_12(hh, ytd), target_bracket=0.12, ytd=ytd)

    result = run_scenario(hh, with_ytd, "fill", end_age=95, ytd=ytd)
    base = next(y for y in result.years if y.year == BASE_YEAR)
    assert abs(base.taxable_income - _ceiling_12(base.filing_status)) <= 1.0

    assert with_ytd.extra_withdrawals.get(BASE_YEAR, 0.0) < without.extra_withdrawals.get(
        BASE_YEAR, 0.0
    )


def test_i8_comparator_preset_passes_nii_to_the_fill(monkeypatch) -> None:
    import engine.scenario as scenario_mod
    import engine.scenario_compare as compare_mod

    seen: dict[str, float] = {}
    real_fill = compare_mod.add_bracket_fill_withdrawals
    real_run = scenario_mod.run_scenario

    def fill_spy(*args, **kwargs):
        seen["fill"] = kwargs.get("net_inv_income", 0.0)
        return real_fill(*args, **kwargs)

    def run_spy(*args, **kwargs):
        if kwargs.get("end_age") == 95 and args[2:3] == ("temp",):
            seen["inner"] = kwargs.get("net_inv_income", 0.0)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(compare_mod, "add_bracket_fill_withdrawals", fill_spy)
    monkeypatch.setattr(scenario_mod, "run_scenario", run_spy)
    build_scenario(_post_rmd_hh(), "fill_12_bf", ytd=_ytd(), net_inv_income=20_000.0)
    assert seen == {"fill": 20_000.0, "inner": 20_000.0}


def test_i8_fill_default_nii_equals_explicit_zero() -> None:
    hh = _post_rmd_hh()
    ytd = _ytd()
    base = auto_fill_12(hh, ytd)
    plain = add_bracket_fill_withdrawals(hh, base, target_bracket=0.12, ytd=ytd)
    zero = add_bracket_fill_withdrawals(hh, base, target_bracket=0.12, ytd=ytd, net_inv_income=0.0)
    assert plain.extra_withdrawals == zero.extra_withdrawals


def test_comparator_preset_passes_ytd_to_the_fill() -> None:
    hh = _post_rmd_hh()
    result = build_scenario(hh, "fill_12_bf", ytd=_ytd())
    base = next(y for y in result.years if y.year == BASE_YEAR)
    # The preset targets the 22% bracket; with the YTD ignored it overshot by the
    # realised income.
    ceiling_22 = indexed_bracket_ceiling(
        2, year=BASE_YEAR, cpi=0.0, filing_status=base.filing_status
    )
    assert base.extra_withdrawal > 0.0
    assert base.taxable_income <= ceiling_22 + 1.0
