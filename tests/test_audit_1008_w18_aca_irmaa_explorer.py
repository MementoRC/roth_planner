"""Audit 2026-10-08 W18: ACA + IRMAA Explorer (F-28, F-42, F-71) plus the
browser-pass items found after the W0/W5 merges.

- F-28: the "base MAGI" input defaults to the household's no-conversion MAGI
  for the base year (the Sweet Spot base), not to option income alone.
- F-42: the Explorer's NIIT uses the same NIIT MAGI as scenario / Sweet Spot:
  tax-exempt muni interest is out, the manual additional NII is in.
- F-71: in survivor years where "you" died, the timeline row describes the
  surviving spouse's coverage and leaves the deceased's age blank.
- The reference-panel caption escapes its dollar signs (no LaTeX).
- The ACA band table shows each band's ramp instead of one rate per band.
- The RMD Squeeze Tier-1 note names the IRMAA payment year it projects to.
"""

from __future__ import annotations

import re

import pytest
from streamlit.testing.v1 import AppTest

from engine.aca import aca_cap_bands
from engine.aca_irmaa_compute import (
    compute_cost_curves,
    compute_year_by_year_timeline,
    default_base_magi,
)
from engine.niit import niit
from engine.sweet_spot_compute import base_income_for_year, zero_conversion_ira_draws
from models.household import Household, SurvivorScenario
from models.ytd_income import YTDSnapshot

# --------------------------------------------------------------------------- F-28


def test_default_base_magi_is_not_option_income_only() -> None:
    hh = Household(grants=[], your_ss_start_age=62, spouse_ss_start_age=62)
    draws = zero_conversion_ira_draws(hh, ytd=None, net_inv_income=0.0)
    expected = base_income_for_year(
        hh, hh.base_year, ytd=None, ira_draw=draws.get(hh.base_year, 0.0)
    ).base_magi
    assert hh.option_income(hh.base_year) == 0.0
    got = default_base_magi(hh, ytd=None, net_inv_income=0.0)
    assert got == pytest.approx(expected)
    assert got > 0.0


def _render_aca(kw: dict, session: dict | None = None) -> AppTest:
    def _script(kw: dict, session: dict) -> None:
        import streamlit as st

        import views.aca_irmaa as page
        from models.household import Household

        for k, v in session.items():
            st.session_state.setdefault(k, v)
        page.render(Household(**kw))

    at = AppTest.from_function(_script, args=(kw, session or {}), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_view_base_magi_input_defaults_to_household_base() -> None:
    kw = {"grants": [], "your_ss_start_age": 62, "spouse_ss_start_age": 62}
    hh = Household(**kw)
    expected = int(default_base_magi(hh, ytd=None, net_inv_income=0.0))
    assert expected > 0
    at = _render_aca(kw)
    box = next(n for n in at.number_input if "base MAGI" in n.label)
    assert box.value == expected


# --------------------------------------------------------------------------- F-42


def test_niit_excludes_muni_and_includes_manual_nii() -> None:
    hh = Household(grants=[])
    ytd = YTDSnapshot(tax_year=hh.base_year, tax_exempt_interest_ytd=20_000.0)
    manual_nii = 10_000.0
    magi = 265_000.0  # IRMAA-basis MAGI, which includes the $20K muni
    curves = compute_cost_curves(
        [magi],
        magi,
        manual_nii,
        hh,
        year=hh.base_year,
        cpi=hh.cpi_assumption,
        ytd=ytd,
    )
    total_nii = manual_nii + curves.auto_nii
    expected = niit(magi + manual_nii - 20_000.0, total_nii, filing_status="MFJ")
    assert curves.niit_vals[0] == pytest.approx(expected)
    # The old IRMAA-MAGI basis charged NIIT on the muni dollars.
    assert curves.niit_vals[0] != pytest.approx(niit(magi, total_nii, filing_status="MFJ"))
    assert curves.base_niit == pytest.approx(expected)


def test_niit_without_ytd_still_counts_manual_nii_in_magi() -> None:
    hh = Household(grants=[])
    curves = compute_cost_curves(
        [245_000.0], 245_000.0, 10_000.0, hh, year=hh.base_year, cpi=hh.cpi_assumption
    )
    total_nii = 10_000.0 + curves.auto_nii
    assert curves.niit_vals[0] == pytest.approx(niit(255_000.0, total_nii, filing_status="MFJ"))


# --------------------------------------------------------------------------- F-71


def _survivor_row(who_dies: str):
    hh = Household(
        survivor=SurvivorScenario(who_dies=who_dies, death_year=2027),
        your_age=60,
        spouse_age=54,
        spouse_aca_enrolled=True,
        your_aca_enrolled=True,
    )
    rows = compute_year_by_year_timeline(hh, 60_000.0, years=6, cpi=hh.cpi_assumption)
    return hh, next(r for r in rows if r.year == 2030)


def test_survivor_row_after_you_die_describes_the_spouse() -> None:
    hh, row = _survivor_row("you")
    assert row.you_age is None
    assert row.spouse_age == hh.spouse_age_in(2030)
    assert "(you)" not in row.system
    assert row.system == "ACA (sp)"
    assert row.aca_subsidy is not None


def test_survivor_row_after_spouse_dies_unchanged_shape() -> None:
    hh, row = _survivor_row("spouse")
    assert row.you_age == hh.your_age_in(2030)
    assert row.spouse_age is None
    assert "(sp)" not in row.system


# --------------------------------------------------------------------------- caption


def test_reference_caption_escapes_dollars() -> None:
    at = _render_aca({}, {})
    caps = [c.value for c in at.caption if "Benchmark silver plan" in c.value]
    assert caps, [c.value for c in at.caption]
    assert "\\$" in caps[0]
    assert not re.search(r"(?<!\\)\$", caps[0]), caps[0]


def test_hidden_cost_caption_escapes_dollars() -> None:
    at = _render_aca({}, {})
    caps = [c.value for c in at.caption if c.value.startswith("Base MAGI")]
    assert caps
    assert not re.search(r"(?<!\\)\$", caps[0]), caps[0]


# --------------------------------------------------------------------------- band table


def test_enhanced_bands_show_linear_ramps() -> None:
    bands = aca_cap_bands(True, year=2026)
    assert bands[0] == (1.0, 1.5, 0.0, 0.0)
    assert bands[1] == (1.5, 2.0, 0.0, 0.02)
    assert bands[2] == (2.0, 2.5, 0.02, 0.04)
    assert bands[-1] == (4.0, float("inf"), 0.085, 0.085)


def test_pre_arp_bands_flat_ends_and_ramped_middle() -> None:
    bands = aca_cap_bands(False, year=2026)
    assert bands[0] == (1.0, 1.33, 0.021, 0.021)
    assert bands[1] == (1.33, 1.5, 0.0314, 0.0419)
    assert bands[-1] == (3.0, 4.0, 0.0996, 0.0996)


def test_view_band_table_labels_ramps() -> None:
    at = _render_aca({"aca_enhanced_subsidies_active": True}, {})
    frames = [df.value for df in at.dataframe if "FPL Range" in df.value.columns]
    assert frames
    df = frames[0]
    ranges = list(df["FPL Range"])
    assert "150–200%" in ranges
    caps = dict(zip(df["FPL Range"], df["Premium Cap"], strict=True))
    assert caps["150–200%"].startswith("0.0% → 2.0%")


# --------------------------------------------------------------------------- RMD Squeeze


def test_rmd_squeeze_tier1_note_names_payment_year() -> None:
    def _script() -> None:
        import views.rmd_squeeze as rmd
        from models.household import Household

        rmd.render(Household())

    at = AppTest.from_function(_script, default_timeout=60).run()
    assert not at.exception
    text = next(m.value for m in at.markdown if "IRMAA trigger" in m.value)
    line = next(ln for ln in text.splitlines() if "IRMAA trigger" in ln)
    hh = Household()
    assert str(hh.base_year + 2) in line
