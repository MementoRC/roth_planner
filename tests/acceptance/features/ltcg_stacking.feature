Feature: Long-term capital gains stack on top of ordinary income

  # Source: IRS Rev. Proc. 2025-32 (tax year 2026), section 4.03 Maximum Zero Rate Amount MFJ $98,900 and Maximum 15% Rate Amount $613,700; section 4.14(1) standard deduction $32,200. Method: Qualified Dividends and Capital Gain Tax Worksheet (Form 1040 instructions); gains fill the 0% band after ordinary taxable income. https://www.irs.gov/pub/irs-drop/rp-25-32.pdf
  # Evidence: (b) table + arithmetic. Ordinary taxable income is below $100,000 (Tax Table territory, 2026 table unpublished), so only the added tax is asserted, not an absolute ordinary-tax figure.
  # Arithmetic: ordinary taxable = 130,000 - 32,200 = 97,800 (unchanged by the gain).
  #             taxable with gain = 117,800. 0% portion = 98,900 - 97,800 = 1,100. 15% portion = 20,000 - 1,100 = 18,900 -> 2,835.
  #             Tax on the ordinary portion is the same with and without the gain, so the added tax = 2,835. AGI/MAGI rises by 20,000.
  Scenario: A gain fills the rest of the 0% band, then is taxed at 15%, without moving ordinary income
    Given a married-filing-jointly household, both under 65, with 2026 wages of $130,000 and the standard deduction
    When they realize a $20,000 long-term capital gain in 2026
    Then their AGI and MAGI rise by $20,000
    And the federal tax on their ordinary income is unchanged
    And $1,100 of the gain is taxed at 0%
    And $18,900 of the gain is taxed at 15%
    And the added federal income tax is $2,835
