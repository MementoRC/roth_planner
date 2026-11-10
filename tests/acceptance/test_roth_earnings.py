"""Steps for roth_earnings.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("roth_earnings.feature")


@given(
    parsers.parse(
        "a single filer aged {age:d} in 2026 with a {ira} traditional IRA and no Roth IRA"
    )
)
def _converter(ctx: dict[str, Any], age: int, ira: str) -> None:
    ctx["case"] = dsl.a_roth_converter(age, dsl.money(ira))


@given(parsers.parse("living expenses of {amount} a year and no other income"))
def _expenses(ctx: dict[str, Any], amount: str) -> None:
    ctx["case"].living_expenses = dsl.money(amount)


@given(parsers.parse("they convert the whole IRA in {year:d}"))
def _convert_all(ctx: dict[str, Any], year: int) -> None:
    dsl.converts(ctx["case"], year)


@when(parsers.parse("the years through {year:d} are projected"))
def _project(ctx: dict[str, Any], year: int) -> None:
    dsl.project_through(ctx["case"], year)


@then(
    parsers.parse(
        "in {year:d} they draw from the Roth IRA and not from the traditional IRA,"
        " beyond everything they converted"
    )
)
def _roth_draw_beyond(ctx: dict[str, Any], year: int) -> None:
    row = dsl.roth_year(ctx["case"], year)
    assert row.roth_draw > 0, row
    assert row.ira_draw == 0, row
    assert dsl.roth_draws_beyond_converted(ctx["case"], year), ctx["case"].rows


@then(parsers.parse("part of the {year:d} Roth draw is earnings"))
def _earnings_drawn(ctx: dict[str, Any], year: int) -> None:
    row = dsl.roth_year(ctx["case"], year)
    earnings = dsl.roth_earnings_drawn(row)
    assert 0 < earnings <= row.roth_draw, row


@then(
    parsers.parse(
        "the {year:d} early-withdrawal additional tax is 10% of that year's Roth earnings drawn"
    )
)
def _earnings_penalty(ctx: dict[str, Any], year: int) -> None:
    row = dsl.roth_year(ctx["case"], year)
    expected = 0.10 * dsl.roth_earnings_drawn(row)
    assert dsl.close_to_cent(row.early_withdrawal_penalty, expected), row


@then(parsers.parse("there is no {year:d} early-withdrawal additional tax"))
def _no_penalty(ctx: dict[str, Any], year: int) -> None:
    row = dsl.roth_year(ctx["case"], year)
    assert row.early_withdrawal_penalty == 0, row


@then(parsers.parse("the {year:d} gross income is that year's Roth earnings drawn"))
def _earnings_in_income(ctx: dict[str, Any], year: int) -> None:
    row = dsl.roth_year(ctx["case"], year)
    assert dsl.close_to_cent(row.gross_income, dsl.roth_earnings_drawn(row)), row


@then(parsers.parse("the {year:d} IRMAA MAGI includes that year's Roth earnings drawn"))
def _irmaa_magi_has_earnings(ctx: dict[str, Any], year: int) -> None:
    row = dsl.roth_year(ctx["case"], year)
    assert dsl.close_to_cent(row.magi, dsl.roth_earnings_drawn(row)), row


@then(parsers.parse("the {year:d} ACA MAGI includes that year's Roth earnings drawn"))
def _aca_magi_has_earnings(ctx: dict[str, Any], year: int) -> None:
    row = dsl.roth_year(ctx["case"], year)
    assert dsl.close_to_cent(row.aca_magi, dsl.roth_earnings_drawn(row)), row


@then(
    parsers.parse(
        "the MAGI that sets the {premium_year:d} IRMAA is the {year:d} MAGI,"
        " which includes that year's Roth earnings drawn"
    )
)
def _lookback_magi(ctx: dict[str, Any], premium_year: int, year: int) -> None:
    income_row = dsl.roth_year(ctx["case"], year)
    premium_row = dsl.roth_year(ctx["case"], premium_year)
    assert dsl.roth_earnings_drawn(income_row) > 0, income_row
    assert dsl.close_to_cent(dsl.irmaa_lookback_magi(premium_row), income_row.magi), (
        premium_row,
        income_row,
    )
