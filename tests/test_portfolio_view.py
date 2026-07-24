"""Regression tests for views/portfolio.py grant-comparison table building.

Covers a production crash: the FinExtract-vs-planner comparison table mixed
bare ``int`` values with the ``"—"`` placeholder string in its "Outstanding"
column, which pandas/pyarrow could infer as int64 and then blow up on the
placeholder when Streamlit serialized the dataframe to Arrow for
``st.dataframe`` (ArrowInvalid: "Could not convert '—' with type str: tried
to convert to int64").
"""

import pandas as pd
import pyarrow as pa

from engine.portfolio_sync import EquityGrant
from models.grants import StockGrant
from views.portfolio import _build_grant_comparison_rows, _pair_grants


def _grant(grant_id="g1", grant_date="2020-01-01", outstanding=500) -> EquityGrant:
    return EquityGrant(
        grant_id=grant_id,
        grant_type="NQO",
        grant_date=grant_date,
        shares_granted=1000,
        outstanding=outstanding,
        current_value=12345.0,
    )


def _plan(year=2019, strike=104.0, shares=650, expiry_year=2029) -> StockGrant:
    return StockGrant(year=year, strike=strike, shares=shares, expiry_year=expiry_year)


class TestGrantComparisonRowsArrowSafe:
    def test_unpaired_rows_produce_uniformly_typed_outstanding_column(self):
        """A grant with no planner counterpart (or vice versa) must not mix
        int and '-' placeholder in the same column -- this is the exact
        shape that crashed pyarrow serialization in production."""
        pairs = _pair_grants(
            snap_grants=[_grant(grant_id="g1", grant_date="2020-01-01", outstanding=500)],
            planner_grants=[_plan(year=2019, shares=650)],
        )
        # 2020 FinExtract grant has no 2019 planner match and vice versa ->
        # two unpaired rows, each with one side rendered as the placeholder.
        rows = _build_grant_comparison_rows(pairs, txn_price_now=180.0)

        df = pd.DataFrame(rows)
        outstanding_types = {type(v) for v in df["Outstanding"]}
        assert outstanding_types == {str}, (
            f"Outstanding column must be uniformly str, got mixed types: {outstanding_types}"
        )

        # The actual production failure mode: this must not raise ArrowInvalid.
        table = pa.Table.from_pandas(df)
        assert table.num_rows == len(rows)

    def test_outstanding_values_are_formatted_with_thousands_separator(self):
        pairs = _pair_grants(
            snap_grants=[_grant(grant_id="g1", grant_date="2020-01-01", outstanding=1234)],
            planner_grants=[_plan(year=2020, shares=5678)],
        )
        rows = _build_grant_comparison_rows(pairs, txn_price_now=180.0)

        finextract_row = next(r for r in rows if r["Source"] == "FinExtract")
        planner_row = next(r for r in rows if r["Source"] == "Planner Default")
        assert finextract_row["Outstanding"] == "1,234"
        assert planner_row["Outstanding"] == "5,678"

    def test_no_pairs_returns_empty_rows(self):
        assert _build_grant_comparison_rows([], txn_price_now=180.0) == []
