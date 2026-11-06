"""Steps for senior_deduction.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("senior_deduction.feature")


@given(
    parsers.parse(
        "a married-filing-jointly household where {seniors} of the two spouses are 65 or older "
        "at the end of 2026"
    )
)
def _mfj(ctx: dict[str, Any], seniors: str) -> None:
    ctx["case"] = dsl.a_senior_household("MFJ", dsl.senior_count(seniors))


@given("a single filer who is 65 or older at the end of 2026")
def _single(ctx: dict[str, Any]) -> None:
    ctx["case"] = dsl.a_senior_household("Single", 1)


@given(parsers.parse("their 2026 modified adjusted gross income is {magi}"))
def _magi(ctx: dict[str, Any], magi: str) -> None:
    ctx["case"].magi = dsl.money(magi)


@when("their 2026 enhanced deduction for seniors is figured")
def _figure(ctx: dict[str, Any]) -> None:
    case = ctx["case"]
    ctx["each"] = dsl.senior_deduction_each(case)
    ctx["total"] = dsl.total_senior_deduction(case)


@then(parsers.parse("the deduction for each qualifying spouse is {each}"))
def _each(ctx: dict[str, Any], each: str) -> None:
    assert dsl.close_to_cent(ctx["each"], dsl.money(each)), ctx["each"]


@then(parsers.parse("the total enhanced deduction for seniors is {total}"))
def _total(ctx: dict[str, Any], total: str) -> None:
    assert dsl.close_to_cent(ctx["total"], dsl.money(total)), ctx["total"]
