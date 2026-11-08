"""Steps for roth_conversion_five_year.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("roth_conversion_five_year.feature")


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
@given(parsers.parse("they convert the rest of the IRA in {year:d}"))
def _convert_all(ctx: dict[str, Any], year: int) -> None:
    dsl.converts(ctx["case"], year)


@given(parsers.re(r"they convert (?P<amount>\$[\d,]+) of the IRA in (?P<year>\d{4})"))
def _convert(ctx: dict[str, Any], amount: str, year: str) -> None:
    dsl.converts(ctx["case"], int(year), dsl.money(amount))


@when(parsers.parse("the years through {year:d} are projected"))
def _project(ctx: dict[str, Any], year: int) -> None:
    dsl.project_through(ctx["case"], year)


@then(parsers.parse("in {year:d} they draw from the Roth IRA and not from the traditional IRA"))
def _roth_draw(ctx: dict[str, Any], year: int) -> None:
    row = dsl.roth_year(ctx["case"], year)
    assert row.roth_draw > 0, row
    assert row.ira_draw == 0, row
    assert dsl.roth_draws_within_converted(ctx["case"], year), ctx["case"].rows


@then(parsers.parse("the {year:d} early-withdrawal additional tax is 10% of that year's Roth draw"))
def _penalty(ctx: dict[str, Any], year: int) -> None:
    row = dsl.roth_year(ctx["case"], year)
    assert dsl.close_to_cent(row.early_withdrawal_penalty, 0.10 * row.roth_draw), row


@then(parsers.parse("there is no {year:d} early-withdrawal additional tax"))
def _no_penalty(ctx: dict[str, Any], year: int) -> None:
    row = dsl.roth_year(ctx["case"], year)
    assert row.early_withdrawal_penalty == 0, row
