"""Audit 2026-10-08 wave W13 -- Roth Eligibility page fixes.

F-30  The MAGI default was the IRMAA anchor (AGI + tax-exempt interest) with
      Roth conversion income left in. Roth MAGI (IRC §408A(c)(3)(B)(i), via
      §219(g)(3)) excludes conversion income and does not add tax-exempt
      interest back.
F-31  The backdoor pro-rata used the owner's whole pre-tax total, which
      includes 403(b) balances. §408(d)(2) / Form 8606 line 6 count IRAs only.
F-63  The contribution limit used the base-year age, whatever tax year was
      selected.
F-77  The jump buttons opened Review Changes, which cannot edit these fields;
      they live on Household & Accounts.
F-83  The "Tax on Conversion" metric showed the taxable amount, not a tax.

AppTest closures must be self-contained (AppTest re-executes their source in
an isolated namespace), so every import lives inside each closure. All
figures are synthetic.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _all_text(at) -> str:
    out: list[str] = []
    for group in ("markdown", "caption", "success", "warning", "error", "info"):
        out.extend(el.value for el in getattr(at, group))
    return "\n".join(out)


# --- F-77: jump buttons go to the page that edits the field -------------------


def _render_basic() -> None:
    from models.household import Household
    from views.roth_eligibility import render

    render(Household(your_age=58, spouse_age=56, base_year=2026, filing_status="MFJ"))


def test_f77_jump_buttons_open_household_page():
    at = AppTest.from_function(_render_basic)
    at.run()
    assert not at.exception
    for key in (
        "nav_filing",
        "nav_your_age",
        "nav_spouse_age",
        "nav_your_workplace_plan",
        "nav_spouse_workplace_plan",
        "nav_your_trad_balance",
        "nav_spouse_trad_balance",
    ):
        at.button(key=key).click().run()
        assert at.session_state["nav_page"] == "household", key


def test_f77_caption_names_household_page_not_retired_setup():
    at = AppTest.from_function(_render_basic)
    at.run()
    assert not at.exception
    text = _all_text(at)
    assert "entered in Setup" not in text
    assert "Household & Accounts" in text
    labels = [b.label for b in at.button]
    assert "Edit in Command Center →" not in labels


# --- F-63: age at the end of the selected tax year ----------------------------


def _render_age_50_base_2026() -> None:
    from models.household import Household
    from views.roth_eligibility import render

    render(Household(your_age=50, base_year=2026, filing_status="Single", your_ira=0.0))


def _render_age_49_base_2025() -> None:
    from models.household import Household
    from views.roth_eligibility import render

    render(Household(your_age=49, base_year=2025, filing_status="Single", your_ira=0.0))


def test_f63_earlier_tax_year_uses_younger_age_no_catchup():
    at = AppTest.from_function(_render_age_50_base_2026)
    at.run()
    at.selectbox(key="elig_tax_year").set_value(2025).run()
    assert not at.exception
    text = _all_text(at)
    assert "### 49" in text
    assert "**IRA contribution limit**: $7,000 (under 50)" in text


def test_f63_later_tax_year_uses_older_age_with_catchup():
    at = AppTest.from_function(_render_age_49_base_2025)
    at.run()
    at.selectbox(key="elig_tax_year").set_value(2026).run()
    assert not at.exception
    text = _all_text(at)
    assert "### 50" in text
    assert "$8,600 (includes $1,100 catch-up)" in text


# --- F-31: pro-rata counts IRAs only, not 403(b) ------------------------------


def _render_403b_only() -> None:
    import streamlit as st

    from engine.portfolio_sync.shapes import AccountSummary, PortfolioSnapshot
    from models.household import Household
    from views.roth_eligibility import render

    st.session_state["portfolio_snapshot"] = PortfolioSnapshot(
        accounts=[AccountSummary(account_type="403b", owner="you", total_value=400_000.0)]
    )
    st.session_state["prior_year_magi"] = {2025: 400_000.0}
    # your_ira is the owner's pre-tax total, which includes the 403(b).
    render(Household(your_age=55, base_year=2026, filing_status="Single", your_ira=400_000.0))


def _render_403b_plus_ira() -> None:
    import streamlit as st

    from engine.portfolio_sync.shapes import AccountSummary, PortfolioSnapshot
    from models.household import Household
    from views.roth_eligibility import render

    st.session_state["portfolio_snapshot"] = PortfolioSnapshot(
        accounts=[
            AccountSummary(account_type="403b", owner="you", total_value=400_000.0),
            AccountSummary(account_type="trad_ira", owner="you", total_value=100_000.0),
            # Another owner's 403(b) must not be subtracted from yours.
            AccountSummary(account_type="403b", owner="spouse", total_value=50_000.0),
        ]
    )
    st.session_state["prior_year_magi"] = {2025: 400_000.0}
    render(Household(your_age=55, base_year=2026, filing_status="Single", your_ira=500_000.0))


def test_f31_403b_only_backdoor_is_clean():
    at = AppTest.from_function(_render_403b_only)
    at.run()
    assert not at.exception
    text = _all_text(at)
    assert "Backdoor Roth is clean!" in text
    assert "Pro-rata rule makes backdoor Roth expensive" not in text
    assert "in 403(b) — IRA balance for pro-rata" in text  # the exclusion is explained


def test_f31_pro_rata_uses_ira_balance_without_403b():
    at = AppTest.from_function(_render_403b_plus_ira)
    at.run()
    assert not at.exception
    text = _all_text(at)
    assert "Your Traditional IRA balance: **$100,000**" in text
    assert "Your Traditional IRA balance: **$500,000**" not in text


# --- F-83: the metric is the taxable amount, labelled as such ------------------


def _render_pro_rata() -> None:
    from models.household import Household
    from views.roth_eligibility import render

    render(Household(your_age=61, base_year=2026, filing_status="Single", your_ira=1_000_000.0))


def test_f83_metric_labelled_taxable_amount():
    at = AppTest.from_function(_render_pro_rata)
    at.run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert "Taxable Amount" in labels
    assert "Tax on Conversion" not in labels


# --- F-30: Roth MAGI excludes conversions and tax-exempt interest -------------


def _render_with_1040() -> None:
    import streamlit as st

    from engine.tax_return_pdf import Form1040Record
    from models.household import Household
    from views.roth_eligibility import render

    st.session_state["_pdf_1040_scanned"] = {
        2025: Form1040Record(
            tax_year=2025,
            agi=260_000.0,
            tax_exempt_interest=10_000.0,
            taxable_ss=0.0,
            qualified_dividends=0.0,
            ordinary_dividends=0.0,
            feie=0.0,
            magi=270_000.0,
            filing_status=None,
            captured_at="2026-02-01T00:00:00",
        )
    }
    # The IRMAA anchor: AGI + tax-exempt interest.
    st.session_state["prior_year_magi"] = {2025: 270_000.0}
    render(Household(your_age=55, spouse_age=52, base_year=2026, filing_status="MFJ", your_ira=0.0))


def test_f30_default_magi_is_agi_not_irmaa_anchor():
    at = AppTest.from_function(_render_with_1040)
    at.run()
    assert not at.exception
    assert at.number_input(key="elig_magi").value == 260_000


def test_f30_conversion_income_is_excluded_from_roth_magi():
    at = AppTest.from_function(_render_with_1040)
    at.run()
    assert not at.exception
    # $260k is above the 2026 MFJ phase-out ($242k-$252k) before the exclusion.
    assert "**No direct Roth contribution**" in _all_text(at)
    at.number_input(key="elig_conversions").set_value(100_000).run()
    assert not at.exception
    text = _all_text(at)
    assert "**No direct Roth contribution**" not in text
    assert text.count("Eligible for full direct Roth contribution") == 2
    assert "MAGI $160,000" in text
