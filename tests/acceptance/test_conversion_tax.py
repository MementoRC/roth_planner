"""Steps for conversion_tax.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("conversion_tax.feature")


@given(
    parsers.parse(
        "a married-filing-jointly household, both under 65, with 2026 wages of {wages} "
        "and the standard deduction"
    )
)
def _household(ctx: dict[str, Any], wages: str) -> None:
    ctx["hh"] = dsl.a_household("MFJ", dsl.money(wages), 2026)


@when(parsers.parse("they convert {amount} to Roth in 2026"))
def _convert(ctx: dict[str, Any], amount: str) -> None:
    ctx["before"] = dsl.baseline(ctx["hh"])
    ctx["after"] = dsl.convert_to_roth(ctx["hh"], dsl.money(amount))


@then(parsers.parse("the federal income tax without the conversion is {amount}"))
def _without(ctx: dict[str, Any], amount: str) -> None:
    assert dsl.close_to_cent(ctx["before"].total, dsl.money(amount)), ctx["before"].total


@then(parsers.parse("the federal income tax with the conversion is {amount}"))
def _with(ctx: dict[str, Any], amount: str) -> None:
    assert dsl.close_to_cent(ctx["after"].total, dsl.money(amount)), ctx["after"].total


@then(parsers.parse("the added federal income tax is {amount}"))
def _added(ctx: dict[str, Any], amount: str) -> None:
    added = dsl.added_tax(ctx["before"], ctx["after"])
    assert dsl.close_to_cent(added, dsl.money(amount)), added
