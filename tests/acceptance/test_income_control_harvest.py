"""Steps for income_control_harvest.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("income_control_harvest.feature")


@given("a married-filing-jointly household, both under 65, with no Social Security")
def _couple(ctx: dict[str, Any]) -> None:
    ctx["case"] = dsl.a_couple_under_65()


@given("no inflation, and a brokerage account with no growth, no dividends and no turnover")
def _plain_brokerage() -> None:
    """Encoded by the driver: zero CPI, and a brokerage with no growth, dividends or turnover."""


@given("ACA coverage is off")
def _aca_off() -> None:
    """Encoded by the driver: neither spouse is enrolled in ACA coverage."""


@given(parsers.parse("a {year:d} conversion of {amount} is the only income"))
def _conversion(ctx: dict[str, Any], year: int, amount: str) -> None:
    ctx["case"].year = year
    ctx["case"].conversion = dsl.money(amount)


@given("the brokerage cost basis is well below its balance")
def _basis_well_below(ctx: dict[str, Any]) -> None:
    dsl.with_basis_well_below_balance(ctx["case"])


@given(parsers.parse("the brokerage holds {gain} of unrealized gain"))
def _unrealized_gain(ctx: dict[str, Any], gain: str) -> None:
    dsl.with_unrealized_gain(ctx["case"], dsl.money(gain))


@given("no brokerage cost basis is entered")
def _no_basis(ctx: dict[str, Any]) -> None:
    dsl.with_no_cost_basis(ctx["case"])


@given("the conversion tax is paid from cash, not the brokerage account")
def _tax_from_cash(ctx: dict[str, Any]) -> None:
    dsl.with_tax_paid_from_cash(ctx["case"])


@given(
    parsers.parse(
        "a married-filing-jointly household, both {age:d}, receiving {benefits} of Social Security"
    )
)
def _social_security_couple(ctx: dict[str, Any], age: int, benefits: str) -> None:
    ctx["case"] = dsl.a_couple_receiving_social_security(age, dsl.money(benefits))


@given(parsers.parse("a {distribution} IRA distribution in {year:d}"))
def _distribution(ctx: dict[str, Any], distribution: str, year: int) -> None:
    ctx["case"].year = year
    ctx["case"].ira_distribution = dsl.money(distribution)


@when(parsers.parse("I view Income Control for {year:d}"))
def _view(ctx: dict[str, Any], year: int) -> None:
    ctx["case"].year = year
    ctx["view"] = dsl.view_harvest(ctx["case"])


@then(parsers.parse("the 0% LTCG harvest is {amount}"))
def _harvest_is(ctx: dict[str, Any], amount: str) -> None:
    view = ctx["view"]
    assert dsl.within_a_dollar(view.amount, dsl.money(amount)), view


@then(parsers.parse("it stops at {phrase}"))
def _stops_at(ctx: dict[str, Any], phrase: str) -> None:
    view = ctx["view"]
    assert dsl.stops_at(view, phrase), view


@then("the tab says this year's planned conversion already uses the 0% LTCG room")
def _conversion_uses_room(ctx: dict[str, Any]) -> None:
    assert ctx["view"].conversion_uses_room_note, ctx["view"]


@then("the tab asks me to enter my brokerage cost basis in Profile")
def _asks_for_basis(ctx: dict[str, Any]) -> None:
    assert ctx["view"].enter_basis_note, ctx["view"]


@then(
    parsers.parse(
        "taxable Social Security costs {costs} of that room, against {untaxed} if it were untaxed"
    )
)
def _social_security_cost(ctx: dict[str, Any], costs: str, untaxed: str) -> None:
    view = ctx["view"]
    without = dsl.harvest_without_social_security(ctx["case"])
    assert dsl.within_a_dollar(view.taxable_ss_at_harvest, dsl.money(costs)), view
    assert dsl.within_a_dollar(without.amount, dsl.money(untaxed)), without
