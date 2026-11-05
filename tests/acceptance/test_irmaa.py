"""Steps for irmaa.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("irmaa.feature")


@given(parsers.parse("a Medicare beneficiary filing {status} with 2024 MAGI of {magi}"))
def _beneficiary(ctx: dict[str, Any], status: str, magi: str) -> None:
    ctx["person"] = dsl.Beneficiary(dsl.status_of(status), {2024: dsl.money(magi)})


@given(parsers.parse("a single Medicare beneficiary with 2024 MAGI of {magi}"))
def _single(ctx: dict[str, Any], magi: str) -> None:
    ctx["person"] = dsl.Beneficiary("Single", {2024: dsl.money(magi)})


@given(parsers.parse("2026 MAGI of {magi}"))
def _magi_2026(ctx: dict[str, Any], magi: str) -> None:
    ctx["person"].magi_by_year[2026] = dsl.money(magi)


@when("the 2026 Part B premium is determined")
def _determine(ctx: dict[str, Any]) -> None:
    ctx["irmaa"], ctx["total"] = dsl.premium_for(ctx["person"], 2026)


@then(parsers.parse("the income-related monthly adjustment is {amount}"))
def _irmaa(ctx: dict[str, Any], amount: str) -> None:
    assert dsl.close_to_cent(ctx["irmaa"], dsl.money(amount)), (ctx["irmaa"], amount)


@then(parsers.parse("the total monthly Part B premium is {amount}"))
def _total(ctx: dict[str, Any], amount: str) -> None:
    assert dsl.close_to_cent(ctx["total"], dsl.money(amount)), (ctx["total"], amount)
