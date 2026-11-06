"""Steps for taxable_social_security.feature."""

from typing import Any

from pytest_bdd import given, parsers, scenarios, then, when

from tests.acceptance import dsl

scenarios("taxable_social_security.feature")


@given(
    parsers.parse(
        "a married-filing-jointly household with 2026 Social Security benefits of {benefits}"
    )
)
def _mfj(ctx: dict[str, Any], benefits: str) -> None:
    ctx["case"] = dsl.a_beneficiary_household("MFJ", dsl.money(benefits))


@given(parsers.parse("a single filer with 2026 Social Security benefits of {benefits}"))
def _single(ctx: dict[str, Any], benefits: str) -> None:
    ctx["case"] = dsl.a_beneficiary_household("Single", dsl.money(benefits))


@given(parsers.parse("other taxable income of {other} and tax-exempt interest of {exempt}"))
def _other_income(ctx: dict[str, Any], other: str, exempt: str) -> None:
    ctx["case"].other_income = dsl.money(other)
    ctx["case"].tax_exempt_interest = dsl.money(exempt)


@when("the taxable part of their 2026 benefits is figured")
def _figure(ctx: dict[str, Any]) -> None:
    ctx["taxable"] = dsl.taxable_benefits(ctx["case"])


@then(parsers.parse("{taxable} of the benefits is taxable"))
def _taxable(ctx: dict[str, Any], taxable: str) -> None:
    assert dsl.close_to_cent(ctx["taxable"], dsl.money(taxable)), ctx["taxable"]
