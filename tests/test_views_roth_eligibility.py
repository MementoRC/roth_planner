"""Tests for views.roth_eligibility — filing-status correctness."""

from __future__ import annotations

import inspect

from models.household import Household


def test_roth_eligibility_imports():
    """views.roth_eligibility must import without error."""
    from views import roth_eligibility

    assert hasattr(roth_eligibility, "render")


class TestFilingStatusDefaultFromHousehold:
    """Roth Eligibility page must seed Filing Status widget from hh.filing_status.

    Before the fix the selectbox had no index= argument so it always defaulted to
    index 0 ("MFJ"), ignoring hh.filing_status="Single" and presenting MFJ phaseout
    thresholds to single filers (~$3,500 over-statement).
    """

    def _source(self) -> str:
        from views import roth_eligibility

        return inspect.getsource(roth_eligibility.render)

    def test_filing_selectbox_uses_hh_filing_status_index(self):
        """render() must derive the Filing Status selectbox default from hh.filing_status.

        The selectbox call must contain an index= argument that references
        hh.filing_status so that a Single household opens the page showing Single
        thresholds rather than MFJ thresholds.
        """
        source = self._source()
        selectbox_pos = source.find('"Filing Status"')
        assert selectbox_pos != -1, '"Filing Status" selectbox not found in render()'
        slice_for_check = source[selectbox_pos : selectbox_pos + 300]
        assert "hh.filing_status" in slice_for_check, (
            "Filing Status selectbox does not reference hh.filing_status — "
            "single filers will see MFJ phaseout thresholds (the defect)"
        )

    def test_single_phaseout_thresholds_differ_from_mfj(self):
        """ROTH_PHASEOUT_BY_YEAR must have distinct Single and MFJ bands for each year."""
        from views.roth_eligibility import ROTH_PHASEOUT_BY_YEAR

        for year, bands in ROTH_PHASEOUT_BY_YEAR.items():
            assert "Single" in bands, f"Missing 'Single' key for year {year}"
            assert "MFJ" in bands, f"Missing 'MFJ' key for year {year}"
            assert bands["Single"] != bands["MFJ"], (
                f"Single and MFJ phaseout bands are identical for {year}"
            )
            assert bands["Single"][0] < bands["MFJ"][0], (
                f"Single lower phaseout ({bands['Single'][0]}) >= MFJ lower "
                f"({bands['MFJ'][0]}) for {year} — incorrect IRS thresholds"
            )

    def test_filing_status_options_include_single(self):
        """The Filing Status selectbox options must include 'Single'."""
        source = self._source()
        selectbox_pos = source.find('"Filing Status"')
        slice_for_check = source[selectbox_pos : selectbox_pos + 200]
        assert '"Single"' in slice_for_check, (
            '"Single" not in Filing Status selectbox options'
        )
