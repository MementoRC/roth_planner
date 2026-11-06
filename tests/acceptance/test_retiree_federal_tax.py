"""Steps for retiree_federal_tax.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("retiree_federal_tax.feature")


@given(
    parsers.parse(
        "a married-filing-jointly household, both {age:d} at the end of 2026, "
        "taking the standard deduction"
    )
)
def _couple(ctx: dict[str, Any], age: int) -> None:
    ctx["couple"] = dsl.retired_couple(age)


@given(parsers.parse("their combined 2026 Social Security benefits are {benefits}"))
def _benefits(ctx: dict[str, Any], benefits: str) -> None:
    ctx["couple"].benefits = dsl.money(benefits)


@given(parsers.parse("they take a {amount} distribution from a traditional IRA in 2026"))
def _distribution(ctx: dict[str, Any], amount: str) -> None:
    ctx["couple"].ira_distribution = dsl.money(amount)


@when("their 2026 federal income tax is figured")
def _figure(ctx: dict[str, Any]) -> None:
    ctx["return"] = dsl.retiree_return(ctx["couple"])


@then(parsers.parse("{amount} of their benefits is taxable"))
def _taxable_benefits(ctx: dict[str, Any], amount: str) -> None:
    actual = ctx["return"].taxable_benefits
    assert dsl.close_to_cent(actual, dsl.money(amount)), actual


@then(parsers.parse("their adjusted gross income is {amount}"))
def _agi(ctx: dict[str, Any], amount: str) -> None:
    assert dsl.close_to_cent(ctx["return"].agi, dsl.money(amount)), ctx["return"].agi


@then(parsers.parse("their standard deduction is {amount}"))
def _standard_deduction(ctx: dict[str, Any], amount: str) -> None:
    actual = ctx["return"].standard_deduction
    assert dsl.close_to_cent(actual, dsl.money(amount)), actual


@then(parsers.parse("their enhanced deduction for seniors is {amount}"))
def _senior_deduction(ctx: dict[str, Any], amount: str) -> None:
    actual = ctx["return"].senior_deduction
    assert dsl.close_to_cent(actual, dsl.money(amount)), actual


@then(parsers.parse("their taxable income is {amount}"))
def _taxable_income(ctx: dict[str, Any], amount: str) -> None:
    actual = ctx["return"].taxable_income
    assert dsl.close_to_cent(actual, dsl.money(amount)), actual


@then(parsers.parse("their federal income tax is {amount}"))
def _tax(ctx: dict[str, Any], amount: str) -> None:
    actual = ctx["return"].total_tax
    assert dsl.close_to_cent(actual, dsl.money(amount)), actual
