"""Steps for income_control_harvest_bugs.feature (red until the confirmed bugs are fixed)."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("income_control_harvest_bugs.feature")


@given("ACA coverage is off and there is no inflation")
def _aca_off_no_inflation() -> None:
    """Encoded by the driver: neither spouse is enrolled in ACA coverage and CPI is zero."""


@given("a married-filing-jointly household, both under 65, with no Social Security")
def _couple_under_65(ctx: dict[str, Any]) -> None:
    ctx["case"] = dsl.a_couple_under_65()


@given(parsers.parse("a married-filing-jointly household, both {age:d}, with no Social Security"))
def _older_couple(ctx: dict[str, Any], age: int) -> None:
    ctx["case"] = dsl.a_couple_receiving_social_security(age, 0.0)


@given(parsers.parse("a {year:d} conversion of {amount} is the only income"))
@given(parsers.parse("a {year:d} conversion of {amount} is planned"))
def _conversion(ctx: dict[str, Any], year: int, amount: str) -> None:
    ctx["case"].year = year
    ctx["case"].conversion = dsl.money(amount)


@given(parsers.parse("a {distribution} IRA distribution in {year:d}"))
def _distribution(ctx: dict[str, Any], distribution: str, year: int) -> None:
    ctx["case"].year = year
    ctx["case"].ira_distribution = dsl.money(distribution)


@given("the conversion tax is paid from cash, not the brokerage account")
def _tax_from_cash(ctx: dict[str, Any]) -> None:
    dsl.with_tax_paid_from_cash(ctx["case"])


@given(parsers.parse("the brokerage holds {gain} of unrealized gain"))
def _unrealized_gain(ctx: dict[str, Any], gain: str) -> None:
    dsl.with_unrealized_gain(ctx["case"], dsl.money(gain))


@given(parsers.parse("a net capital loss of {loss} so far in {year:d}"))
def _net_loss(ctx: dict[str, Any], loss: str, year: int) -> None:
    ctx["case"].year = year
    dsl.with_a_year_to_date_net_capital_loss(ctx["case"], dsl.money(loss))


@given(parsers.parse("the brokerage grows {rate} in the year with {turnover} turnover"))
def _growth(ctx: dict[str, Any], rate: str, turnover: str) -> None:
    dsl.with_growth_and_turnover(ctx["case"], dsl.percent(rate), dsl.percent(turnover))


@given(parsers.parse("itemized deductions of {amount}"))
def _itemized(ctx: dict[str, Any], amount: str) -> None:
    dsl.with_itemized_deductions(ctx["case"], dsl.money(amount))


@given(parsers.parse("a married-filing-jointly household, both {age:d}, with no income"))
def _no_income_couple(ctx: dict[str, Any], age: int) -> None:
    ctx["shortfall"] = dsl.a_couple_with_no_income(age)


@given(parsers.parse("living expenses of {amount} a year"))
def _living_expenses(ctx: dict[str, Any], amount: str) -> None:
    ctx["shortfall"].living_expenses = dsl.money(amount)


@given(
    parsers.parse(
        "a brokerage account of {balance} with a cost basis of {basis}, "
        "and no growth, dividends or turnover"
    )
)
def _brokerage(ctx: dict[str, Any], balance: str, basis: str) -> None:
    ctx["shortfall"].brokerage_balance = dsl.money(balance)
    ctx["shortfall"].brokerage_basis = dsl.money(basis)


@when(parsers.parse("I view Income Control for {year:d}"))
def _view(ctx: dict[str, Any], year: int) -> None:
    ctx["case"].year = year
    ctx["view"] = dsl.view_harvest(ctx["case"])


@when(parsers.parse("the {year:d} shortfall is funded through the projection's expense debit"))
def _fund_by_debit(ctx: dict[str, Any], year: int) -> None:
    ctx["shortfall"].year = year
    ctx["debit"] = dsl.funded_by_expense_debit(ctx["shortfall"])


@when("the same shortfall is funded by an explicit brokerage sale")
def _fund_by_sale(ctx: dict[str, Any]) -> None:
    ctx["sale"] = dsl.funded_by_explicit_sale(ctx["shortfall"])


@then("the realized gain, federal tax and MAGI are the same either way")
def _same_either_way(ctx: dict[str, Any]) -> None:
    debit, sale = ctx["debit"], ctx["sale"]
    assert sale.realized_gains > 0, sale
    assert dsl.within_a_dollar(debit.realized_gains, sale.realized_gains), (debit, sale)
    assert dsl.within_a_dollar(debit.federal_tax, sale.federal_tax), (debit, sale)
    assert dsl.within_a_dollar(debit.magi, sale.magi), (debit, sale)


@then("the harvest line never shows more than the engine's 0% LTCG harvest")
def _line_within_engine(ctx: dict[str, Any]) -> None:
    view = ctx["view"]
    assert view.amount % 1 >= 0.5, f"the case needs a fractional part of 50 cents or more: {view}"
    assert dsl.shown_harvest(view) <= view.amount, view


@then("realizing the harvest adds no federal tax for the year")
def _harvest_is_tax_free(ctx: dict[str, Any]) -> None:
    view = ctx["view"]
    assert view.amount > 0, view
    extra = dsl.extra_tax_from_realizing(ctx["case"], view, view.amount)
    assert dsl.adds_no_tax(extra), (extra, view)


@then("realizing $2 more than the harvest adds federal tax, unless a non-tax limit binds")
def _harvest_is_tight(ctx: dict[str, Any]) -> None:
    view = ctx["view"]
    extra = dsl.extra_tax_from_realizing(ctx["case"], view, view.amount + 2.0)
    assert dsl.binds_on_a_non_tax_limit(view) or not dsl.adds_no_tax(extra), (extra, view)


@given(parsers.parse("the brokerage turns over {turnover} a year"))
def _turns_over(ctx: dict[str, Any], turnover: str) -> None:
    ctx["case"].turnover = dsl.percent(turnover)


@when(parsers.parse("I view Income Control for {year:d} in the forecast starting {base:d}"))
def _view_later_year(ctx: dict[str, Any], year: int, base: int) -> None:
    ctx["case"].year = base
    ctx["case"].view_year = year
    ctx["view"] = dsl.view_harvest(ctx["case"])


@then("the harvest equals the year-end unrealized gain in the brokerage account")
def _harvest_is_year_end_gain(ctx: dict[str, Any]) -> None:
    view = ctx["view"]
    assert view.year_end_unrealized_gain > 0, view
    assert dsl.within_a_dollar(view.amount, view.year_end_unrealized_gain), view


@then(parsers.parse("it stops at {phrase}"))
def _stops_at(ctx: dict[str, Any], phrase: str) -> None:
    view = ctx["view"]
    assert dsl.stops_at(view, phrase), view


@then(parsers.parse("the 0% LTCG harvest is {amount}"))
def _harvest_is(ctx: dict[str, Any], amount: str) -> None:
    view = ctx["view"]
    assert dsl.within_a_dollar(view.amount, dsl.money(amount)), view


@then(parsers.parse("with no planned conversion the 0% LTCG harvest would be {amount}"))
def _harvest_without_conversion(ctx: dict[str, Any], amount: str) -> None:
    without = dsl.harvest_without_conversion(ctx["case"])
    assert without.amount > ctx["view"].amount, (without, ctx["view"])
    assert dsl.within_a_dollar(without.amount, dsl.money(amount)), without


@then("the tab says this year's planned conversion already uses the 0% LTCG room")
def _conversion_uses_room(ctx: dict[str, Any]) -> None:
    assert ctx["view"].conversion_uses_room_note, ctx["view"]


@then(
    parsers.parse(
        "the harvest stops where MAGI reaches {threshold}, the senior deduction phase-out threshold"
    )
)
def _stops_at_phaseout(ctx: dict[str, Any], threshold: str) -> None:
    view = ctx["view"]
    assert dsl.stops_where_magi_reaches(view, dsl.money(threshold)), view


@then(parsers.parse("the harvest does not stop at {phrase}"))
def _does_not_stop_at(ctx: dict[str, Any], phrase: str) -> None:
    view = ctx["view"]
    assert not dsl.is_described_as_stopping_at(view, phrase), view


@given(parsers.parse("the household spends {amount} a year from the brokerage account"))
def _spends_from_brokerage(ctx: dict[str, Any], amount: str) -> None:
    ctx["case"].living_expenses = dsl.money(amount)


@given("the brokerage cost basis is well below its balance")
def _basis_well_below(ctx: dict[str, Any]) -> None:
    dsl.with_basis_well_below_balance(ctx["case"])


@given(parsers.parse("the brokerage cost basis is {pct} of its balance"))
def _basis_share(ctx: dict[str, Any], pct: str) -> None:
    dsl.with_basis_share(ctx["case"], dsl.percent(pct))


@given("the conversion tax is paid from the brokerage account, not cash")
def _tax_from_brokerage(ctx: dict[str, Any]) -> None:
    dsl.with_tax_paid_from_brokerage(ctx["case"])


@then(
    "the room before the conversion is the 0% LTCG room of the same year "
    "re-run with no planned conversion"
)
def _room_before_matches_rerun(ctx: dict[str, Any]) -> None:
    view = ctx["view"]
    rerun = dsl.harvest_without_conversion(ctx["case"])
    shown = view.ltcg_room_without_conversion
    assert shown is not None, view
    assert dsl.within_a_dollar(shown, rerun.ltcg_zero_room), (view, rerun)


@then("the same year re-run with no planned conversion still has 0% LTCG room")
def _rerun_still_has_room(ctx: dict[str, Any]) -> None:
    rerun = dsl.harvest_without_conversion(ctx["case"])
    assert dsl.has_room(rerun.ltcg_zero_room), rerun


@given(
    parsers.parse(
        "a married-filing-jointly household, both under 65, "
        "whose brokerage account turns over {turnover} a year"
    )
)
def _household_with_turnover(ctx: dict[str, Any], turnover: str) -> None:
    ctx["case"] = dsl.a_couple_under_65_with_turnover(dsl.percent(turnover))


@given(parsers.parse("the brokerage falls {rate} in {year:d}"))
def _falls(ctx: dict[str, Any], rate: str, year: int) -> None:
    dsl.with_return_in(ctx["case"], year, -dsl.percent(rate))


@given(parsers.parse("the brokerage rises {rate} in {year:d}"))
def _rises(ctx: dict[str, Any], rate: str, year: int) -> None:
    dsl.with_return_in(ctx["case"], year, dsl.percent(rate))


@given(parsers.parse("the brokerage pays qualified dividends of {amount} in {year:d}"))
def _pays_qualified_dividends(ctx: dict[str, Any], amount: str, year: int) -> None:
    dsl.with_qualified_dividends_in(ctx["case"], year, dsl.money(amount))


@given(
    parsers.parse(
        "actuals so far in {year:d} of {wages} of wages and a {loss} net long-term capital loss"
    )
)
def _actuals(ctx: dict[str, Any], year: int, wages: str, loss: str) -> None:
    ctx["case"].year = year
    dsl.with_year_to_date_actuals(ctx["case"], dsl.money(wages), dsl.money(loss))


@then(
    parsers.parse(
        "the {later:d} MAGI from capital gains is the gain less the {carried} "
        "carried over from the actuals loss"
    )
)
def _actuals_loss_carries(ctx: dict[str, Any], later: int, carried: str) -> None:
    second = ctx["years"][later]
    assert second.realized_gains > dsl.money(carried), second
    capital = second.magi - second.ordinary_gross
    assert dsl.within_a_dollar(capital, second.realized_gains - dsl.money(carried)), second


@when("the conversion plan is auto-filled to stay under the first IRMAA tier")
def _autofill_irmaa(ctx):
    ctx["case"] = dsl.with_autofill_irmaa_safe(ctx["case"])


@then(parsers.parse("the {year:d} MAGI is {amount}, the first IRMAA tier"))
def _magi_first_tier(ctx, year, amount):
    assert dsl.within_a_dollar(ctx["years"][year].magi, dsl.money(amount))


@when(parsers.parse("the forecast years {first:d} and {second:d} are projected"))
def _project(ctx: dict[str, Any], first: int, second: int) -> None:
    years = dsl.capital_years(ctx["case"])
    assert first in years, sorted(years)
    assert second in years, sorted(years)
    ctx["years"] = years


@then(parsers.parse("the {year:d} net capital loss lowers MAGI by {limit}, not by the whole loss"))
def _magi_lowered_by_the_limit(ctx: dict[str, Any], year: int, limit: str) -> None:
    loss_year = ctx["years"][year]
    no_loss = dsl.capital_years_without_the_loss(ctx["case"], year)[year]
    assert -loss_year.realized_gains > dsl.money(limit), loss_year
    assert dsl.within_a_dollar(no_loss.magi - loss_year.magi, dsl.money(limit)), (
        no_loss,
        loss_year,
    )


@then(parsers.parse("the {year:d} taxable income falls by {limit} with the loss"))
def _taxable_income_falls_by_the_limit(ctx: dict[str, Any], year: int, limit: str) -> None:
    loss_year = ctx["years"][year]
    no_loss = dsl.capital_years_without_the_loss(ctx["case"], year)[year]
    lowered = no_loss.taxable_income - loss_year.taxable_income
    assert dsl.within_a_dollar(lowered, dsl.money(limit)), (no_loss, loss_year)


@then(parsers.parse("the {year:d} taxable income is lower by {limit} and federal tax is lower"))
def _taxable_income_and_tax_lower(ctx: dict[str, Any], year: int, limit: str) -> None:
    loss_year = ctx["years"][year]
    no_loss = dsl.capital_years_without_the_loss(ctx["case"], year)[year]
    lowered = no_loss.taxable_income - loss_year.taxable_income
    assert dsl.within_a_dollar(lowered, dsl.money(limit)), (no_loss, loss_year)
    assert loss_year.federal_tax < no_loss.federal_tax, (no_loss, loss_year)


@then(
    parsers.parse(
        "the {later:d} MAGI from capital gains is the gain less the {earlier:d} loss in excess of {limit}"
    )
)
def _excess_loss_carries(ctx: dict[str, Any], later: int, earlier: int, limit: str) -> None:
    first, second = ctx["years"][earlier], ctx["years"][later]
    carried = -first.realized_gains - dsl.money(limit)
    assert carried > 0, first
    assert second.realized_gains > carried, second
    capital = second.magi - second.ordinary_gross
    assert dsl.within_a_dollar(capital, second.realized_gains - carried), (first, second)


@then(
    parsers.parse(
        "the {later:d} MAGI from capital gains is the gain less the {earlier:d} loss "
        "not used against adjusted taxable income of {ati}"
    )
)
def _unused_by_ati_carries(ctx: dict[str, Any], later: int, earlier: int, ati: str) -> None:
    first, second = ctx["years"][earlier], ctx["years"][later]
    carried = -first.realized_gains - dsl.money(ati)
    assert carried > 0, first
    assert second.realized_gains > carried, second
    capital = second.magi - second.ordinary_gross
    assert dsl.within_a_dollar(capital, second.realized_gains - carried), (first, second)


@then(
    parsers.parse(
        "the {later:d} carryover is larger than the {earlier:d} loss in excess of {limit}"
    )
)
def _carryover_exceeds_the_limit_case(
    ctx: dict[str, Any], later: int, earlier: int, limit: str
) -> None:
    first, second = ctx["years"][earlier], ctx["years"][later]
    limited = -first.realized_gains - dsl.money(limit)
    carried_in = second.realized_gains - (second.magi - second.ordinary_gross)
    assert carried_in > limited, (first, second)


@then(parsers.parse("the {year:d} brokerage sale realizes a gain between {low} and {high}"))
def _sale_gain_between(ctx: dict[str, Any], year: int, low: str, high: str) -> None:
    realized = ctx["years"][year]
    assert dsl.money(low) < realized.realized_gains < dsl.money(high), realized


@then(
    parsers.parse(
        "the {year:d} MAGI from capital gains is minus the actuals loss netted "
        "with the sale's gain, up to {limit}"
    )
)
def _magi_is_the_netted_loss(ctx: dict[str, Any], year: int, limit: str) -> None:
    case, first = ctx["case"], ctx["years"][year]
    netted = -case.ytd_net_long_term - first.realized_gains
    assert netted > 0, first
    capital = first.magi - case.ytd_wages
    assert dsl.within_a_dollar(capital, -min(dsl.money(limit), netted)), first


@then(
    parsers.parse(
        "the {later:d} MAGI from capital gains is the gain less the {earlier:d} "
        "netted loss in excess of {limit}"
    )
)
def _netted_loss_carries(ctx: dict[str, Any], later: int, earlier: int, limit: str) -> None:
    first, second = ctx["years"][earlier], ctx["years"][later]
    carried = -ctx["case"].ytd_net_long_term - first.realized_gains - dsl.money(limit)
    assert carried > 0, first
    assert second.realized_gains > carried, second
    capital = second.magi - second.ordinary_gross
    assert dsl.within_a_dollar(capital, second.realized_gains - carried), (first, second)


@given(parsers.parse("a {carry} long-term capital loss carryover from the {year:d} return"))
def _prior_lt_carryover(ctx: dict[str, Any], carry: str) -> None:
    dsl.with_prior_year_carryover(ctx["case"], dsl.money(carry))


@then(parsers.parse("the {year:d} MAGI comes to {amount}"))
def _magi_comes_to(ctx: dict[str, Any], year: int, amount: str) -> None:
    assert dsl.within_a_dollar(ctx["years"][year].magi, dsl.money(amount)), ctx["years"][year]


@then(parsers.parse("the {year:d} taxable income comes to {amount}"))
def _taxable_comes_to(ctx: dict[str, Any], year: int, amount: str) -> None:
    assert dsl.within_a_dollar(ctx["years"][year].taxable_income, dsl.money(amount)), ctx["years"][
        year
    ]
