"""Steps for rmd.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("rmd.feature")


@given(parsers.parse("a traditional IRA owner who turns {age:d} in 2026"))
@given(parsers.parse("a traditional IRA owner who is {age:d} in 2026"))
def _owner(ctx: dict[str, Any], age: int) -> None:
    ctx["ira"] = dsl.an_ira_owner(age)


@given(parsers.parse("their spouse is the sole beneficiary and is {years:d} years younger"))
def _spouse(ctx: dict[str, Any], years: int) -> None:
    dsl.with_spouse_beneficiary_younger_by(ctx["ira"], years)


@given(parsers.parse("the IRA balance on December 31, 2025 was {balance}"))
def _balance(ctx: dict[str, Any], balance: str) -> None:
    ctx["ira"].balance = dsl.money(balance)


@when("the 2026 required minimum distribution is figured")
def _figure(ctx: dict[str, Any]) -> None:
    ira = ctx["ira"]
    ctx["period"] = dsl.rmd_period(ira)
    ctx["rmd"] = dsl.rmd_amount(ira)


@then(parsers.parse("the distribution period is {period}"))
def _period(ctx: dict[str, Any], period: str) -> None:
    assert ctx["period"] == dsl.money(period)


@then(parsers.parse("the required minimum distribution is {amount}"))
def _rmd(ctx: dict[str, Any], amount: str) -> None:
    assert ctx["rmd"] == dsl.money(amount)
