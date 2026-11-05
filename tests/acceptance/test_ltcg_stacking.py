"""Steps for ltcg_stacking.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("ltcg_stacking.feature")


@given(
    parsers.parse(
        "a married-filing-jointly household, both under 65, with 2026 wages of {wages} "
        "and the standard deduction"
    )
)
def _household(ctx: dict[str, Any], wages: str) -> None:
    ctx["hh"] = dsl.a_household("MFJ", dsl.money(wages), 2026)


@when(parsers.parse("they realize a {amount} long-term capital gain in 2026"))
def _realize(ctx: dict[str, Any], amount: str) -> None:
    ctx["gain"] = dsl.money(amount)
    ctx["before"] = dsl.baseline(ctx["hh"])
    ctx["after"] = dsl.realize_ltcg(ctx["hh"], ctx["gain"])


@then(parsers.parse("their AGI and MAGI rise by {amount}"))
def _magi(ctx: dict[str, Any], amount: str) -> None:
    rise = ctx["after"].magi - ctx["before"].magi
    assert dsl.close_to_cent(rise, dsl.money(amount)), rise


@then("the federal tax on their ordinary income is unchanged")
def _ordinary_unchanged(ctx: dict[str, Any]) -> None:
    assert dsl.close_to_cent(ctx["after"].ordinary_tax, ctx["before"].ordinary_tax), (
        ctx["before"].ordinary_tax,
        ctx["after"].ordinary_tax,
    )


@then(parsers.parse("{amount} of the gain is taxed at 0%"))
def _at_0(ctx: dict[str, Any], amount: str) -> None:
    actual = dsl.gain_taxed_at_0(ctx["after"], ctx["gain"])
    assert dsl.close_to_cent(actual, dsl.money(amount)), actual


@then(parsers.parse("{amount} of the gain is taxed at 15%"))
def _at_15(ctx: dict[str, Any], amount: str) -> None:
    actual = dsl.gain_taxed_at_15(ctx["after"])
    assert dsl.close_to_cent(actual, dsl.money(amount)), actual


@then(parsers.parse("the added federal income tax is {amount}"))
def _added(ctx: dict[str, Any], amount: str) -> None:
    added = dsl.added_tax(ctx["before"], ctx["after"])
    assert dsl.close_to_cent(added, dsl.money(amount)), added
