"""Regression tests for deep-review 2026-06-18 PR-B (NIIT/MAGI definition) fixes."""

import pytest

from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestNiitMagiDefinition:
    def test_niit_room_excludes_muni_interest(self):
        """niit-1/niit-6: NIIT headroom must use muni-excluded section 1411 MAGI."""
        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd_none = YTDSnapshot(tax_year=2026)
        ytd_muni = YTDSnapshot(tax_year=2026, tax_exempt_interest_ytd=50_000)
        hr_none = compute_headroom(hh, ytd_none)
        hr_muni = compute_headroom(hh, ytd_muni)

        assert hr_none.room_to_niit > 0  # not trivially clamped to zero
        # Muni interest flows into the IRMAA-style locked MAGI ...
        assert hr_muni.locked_magi == approx(hr_none.locked_magi + 50_000)
        # ... but NOT into NIIT MAGI (IRC 1411(d)(3)) -> room unchanged.
        assert hr_muni.room_to_niit == approx(hr_none.room_to_niit)
        assert hr_muni.room_to_niit_with_planned == approx(hr_none.room_to_niit_with_planned)

    def test_sweet_spot_base_magi_uses_taxable_ss(self):
        """compare-sweetspot-1/niit-2: Sweet Spot MAGI must use taxable SS, not gross."""
        from engine.sweet_spot_compute import base_income_for_year

        hh = Household()
        year = 2026 + (70 - hh.your_age)  # year your_age reaches default claim age 70
        base = base_income_for_year(hh, year)

        assert base.combined_ss > 0
        # Gross SS would overstate MAGI; taxable SS is at most 85%.
        assert base.base_magi < base.opt + base.combined_ss
        # In the sweet-spot model MAGI == gross ordinary (opt + taxable SS).
        assert base.base_magi == approx(base.base_gross)
