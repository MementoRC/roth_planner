"""Steps for income_control_ytd_stack.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("income_control_ytd_stack.feature")


@given(parsers.parse("a single filer aged {age:d} in 2026 with a {ira} traditional IRA"))
def _filer(ctx: dict[str, Any], age: int, ira: str) -> None:
    ctx["case"] = dsl.an_income_control_filer(age, dsl.money(ira))


@given(parsers.parse("this year's actuals show {wages} of wages and {converted} already converted"))
def _actuals(ctx: dict[str, Any], wages: str, converted: str) -> None:
    ctx["case"].wages_ytd = dsl.money(wages)
    ctx["case"].converted_ytd = dsl.money(converted)


@given(parsers.parse("the plan converts {amount} in {year:d}"))
def _plan(ctx: dict[str, Any], amount: str, year: int) -> None:
    ctx["case"].planned_conversion = dsl.money(amount)


@when(parsers.parse("the Income Control stack for {year:d} is built with this year's actuals"))
def _build(ctx: dict[str, Any], year: int) -> None:
    dsl.build_income_control_stack(ctx["case"], year)


@then(
    parsers.parse(
        "the ordinary lines of the stack add up to the gross income of the {year:d} projection"
    )
)
def _guard(ctx: dict[str, Any], year: int) -> None:
    result = ctx["case"].result
    assert dsl.close_to_cent(result.ordinary_stack_total, result.combined_gross), result


@then(parsers.parse("the {year:d} conversion line is {done} done plus {remaining} still to come"))
def _conversion_split(ctx: dict[str, Any], year: int, done: str, remaining: str) -> None:
    result = ctx["case"].result
    assert dsl.close_to_cent(result.conversion_done, dsl.money(done)), result
    assert dsl.close_to_cent(result.conversion_remaining, dsl.money(remaining)), result
