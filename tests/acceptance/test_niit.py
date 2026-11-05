"""Steps for niit.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("niit.feature")


@given(
    parsers.parse(
        "a household filing {status} with MAGI of {magi} and net investment income of {nii}"
    )
)
def _household(ctx: dict[str, Any], status: str, magi: str, nii: str) -> None:
    ctx["args"] = (dsl.status_of(status), dsl.money(magi), dsl.money(nii))


@when("the net investment income tax is figured")
def _figure(ctx: dict[str, Any]) -> None:
    ctx["niit"] = dsl.niit_owed(*ctx["args"])


@then(parsers.parse("the net investment income tax is {amount}"))
def _niit(ctx: dict[str, Any], amount: str) -> None:
    assert dsl.close_to_cent(ctx["niit"], dsl.money(amount)), (ctx["niit"], amount)
