"""audit-1008 W22 -- Holdings page portfolio totals (synthetic data only).

F-79: the Account Overview table and class metrics dropped target-date fund
value, so an IRA holding only a target-date fund showed its full Total Value
next to $0 in every class column -- the classes no longer added up to the
total. Every class the engine defines (``EXPECTED_RETURNS``) must have a
column and a metric.

F-80: the Growth Rate Mapping "Brokerage" row read
``snap.account_by_type("brokerage")``, i.e. the FIRST brokerage account only,
so a second owner's brokerage account vanished from the balance and the
weighted return disagreed with the rate the planner actually uses
(``snap.brokerage_total`` / ``snap.brokerage_weighted_return``).
"""

from streamlit.testing.v1 import AppTest


def _overview_app():
    from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
    from views.portfolio import render_account_overview

    snap = PortfolioSnapshot(
        accounts=[
            AccountSummary(
                account_type="trad_ira",
                owner="you",
                account_name="Traditional IRA Synthetic",
                total_value=100_000.0,
                target_date_value=100_000.0,
            ),
        ],
    )
    render_account_overview(snap)


def _growth_app():
    from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
    from models.household import Household
    from views.portfolio import render_growth_mapping

    snap = PortfolioSnapshot(
        accounts=[
            AccountSummary(
                account_type="brokerage",
                owner="you",
                account_name="Brokerage A",
                total_value=100_000.0,
                bond_value=100_000.0,
            ),
            AccountSummary(
                account_type="brokerage",
                owner="spouse",
                account_name="Brokerage B",
                total_value=300_000.0,
                equity_value=300_000.0,
            ),
        ],
    )
    render_growth_mapping(snap, Household())


def _run(fn) -> AppTest:
    at = AppTest.from_function(fn, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


class TestF79TargetDateInOverview:
    def test_overview_table_has_target_date_column(self):
        at = _run(_overview_app)
        df = at.dataframe[0].value
        assert "Target Date" in df.columns
        assert df.iloc[0]["Target Date"] == "$100,000"

    def test_every_engine_asset_class_has_a_column(self):
        from engine.portfolio_sync import EXPECTED_RETURNS
        from views.portfolio import CLASS_COLUMNS

        assert [attr for attr, _ in CLASS_COLUMNS] == [f"{c}_value" for c in EXPECTED_RETURNS]

    def test_class_metrics_add_up_to_total(self):
        at = _run(_overview_app)
        metrics = {m.label: m.value for m in at.metric}
        assert metrics["Total Portfolio"] == "$100,000"
        assert metrics["Target Date"] == "$100,000"


class TestF80BrokerageRowAggregatesAllAccounts:
    def test_brokerage_row_uses_combined_total_and_return(self):
        at = _run(_growth_app)
        df = at.dataframe[0].value
        row = df[df["Account"] == "Brokerage"].iloc[0]
        # 100k bond @ 4% + 300k equity @ 9% => 400k @ 7.75%
        assert row["Balance"] == "$400,000"
        assert row["Weighted Return"] == "7.8%"
