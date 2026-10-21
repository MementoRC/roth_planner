"""Issue #42: ACA/IRMAA Explorer leftovers after audit-1018 W3/W12.

Synthetic figures only.
"""

import pytest

import engine.aca_irmaa_compute as aic
from engine.aca import aca_ceiling_magi, aca_subsidy, fpl_for
from engine.aca_irmaa_compute import _nontaxable_ss, compute_cost_curves
from engine.tax import taxable_ss
from models.household import Household


def test_hidden_cost_excludes_aca_loss_when_nobody_on_aca(monkeypatch) -> None:
    """Nobody on ACA: the hidden-cost line must carry no ACA subsidy loss.

    The effective benchmark is already 0 when nobody is enrolled, so a real
    run can't show the leak. Force a nonzero benchmark to prove the hidden
    cost is gated the same way as the loss series.
    """
    monkeypatch.setattr(aic, "effective_benchmark_premium", lambda *_a, **_k: 20_000.0)
    hh = Household(your_age=60, spouse_age=60, your_aca_enrolled=False, spouse_aca_enrolled=False)
    hh.aca_enhanced_subsidies_active = False
    points = [40_000.0, 70_000.0, 120_000.0]
    cc = compute_cost_curves(points, 35_000.0, 0.0, hh, year=2026, cpi=0.025)

    assert cc.aca_subsidy_loss_vals == [0, 0, 0]
    for i in range(len(points)):
        assert cc.total_hidden_cost_vals[i] == pytest.approx(
            cc.irmaa_increase_vals[i] + cc.niit_increase_vals[i]
        )


def test_hidden_cost_includes_aca_loss_when_on_aca() -> None:
    """Control: an enrolled household's hidden cost still includes the loss."""
    hh = Household(your_age=60, spouse_age=60, your_aca_enrolled=True, spouse_aca_enrolled=True)
    hh.aca_enhanced_subsidies_active = False
    points = [40_000.0, 70_000.0, 120_000.0]
    cc = compute_cost_curves(points, 35_000.0, 0.0, hh, year=2026, cpi=0.025)

    assert cc.aca_subsidy_loss_vals[-1] > 0
    for i in range(len(points)):
        assert cc.total_hidden_cost_vals[i] == pytest.approx(
            cc.aca_subsidy_loss_vals[i] + cc.irmaa_increase_vals[i] + cc.niit_increase_vals[i]
        )


def _ss_30k_hh() -> Household:
    return Household(
        your_age=62,
        spouse_age=60,
        filing_status="MFJ",
        your_ss_start_age=62,
        your_ss_fra=30_000.0 / 12,
        your_fra_age=62,
        spouse_ss_fra=0.0,
        ss_cola=0.0,
        your_aca_enrolled=True,
    )


def test_nontaxable_ss_counts_muni_once_hand_computed() -> None:
    """MFJ, SS $30,000, muni $5,000, other (non-SS AGI) income $40,000.

    §86 provisional = 40,000 + 5,000 + 15,000 = 60,000.
    Taxable SS = min(0.85 * (60,000 - 44,000) + min(15,000, 6,000), 25,500) = 19,600.
    Non-taxable SS = 10,400.
    IRMAA MAGI = 40,000 + 19,600 + 5,000 = 64,600; ACA MAGI = 75,000.
    """
    assert taxable_ss(30_000.0, 45_000.0, filing_status="MFJ") == pytest.approx(19_600.0)
    irmaa_magi = 64_600.0
    nts = _nontaxable_ss(_ss_30k_hh(), 62, 60, other_income=irmaa_magi, filing_status="MFJ")
    assert nts == pytest.approx(10_400.0, abs=0.01)
    assert irmaa_magi + nts == pytest.approx(75_000.0, abs=0.01)
    # Counting muni twice would give provisional 65,000 and non-taxable 6,150.
    assert nts != pytest.approx(6_150.0, abs=1.0)


def test_cost_curves_ss_addback_counts_muni_once() -> None:
    """Same hand case through compute_cost_curves' base-point add-back."""
    cc = compute_cost_curves([64_600.0], 64_600.0, 0.0, _ss_30k_hh(), year=2026, cpi=0.025)
    assert cc.nontaxable_ss == pytest.approx(10_400.0, abs=0.01)


def test_aca_ceiling_magi_pinned_at_exact_400_pct_fpl() -> None:
    """Owner decision: keep the exact 4.0 x FPL planning bound."""
    assert aca_ceiling_magi("MFJ", 2026, 0.025) == pytest.approx(84_600.0)
    assert aca_ceiling_magi("Single", 2026, 0.025) == pytest.approx(62_600.0)
    assert aca_ceiling_magi("MFJ", 2030, 0.03) == pytest.approx(4.0 * fpl_for("MFJ", 2030, 0.03))


def test_aca_ceiling_magi_is_about_211_below_truncation_edge() -> None:
    """Form 8962 truncation still subsidizes MAGI up to just under 401% FPL;
    the planning bound sits $211.50 below that edge (MFJ, 2026 coverage)."""
    ceiling = aca_ceiling_magi("MFJ", 2026, 0.025)

    def sub(magi: float) -> float:
        return aca_subsidy(
            magi, 20_000.0, enhanced_subsidies_active=False, filing_status="MFJ", year=2026
        )

    assert sub(ceiling + 211.0) > 0
    assert sub(ceiling + 212.0) == 0
