Feature: Earnings in Roth IRA distributions

  # Every household is a single filer with a traditional IRA, no Roth IRA before
  # the conversion, no wages, no Social Security and no brokerage account. It
  # lives on $9,000 a year, which the projection funds by its own withdrawal order.
  # The whole IRA is converted in 2026 and was fully taxable at conversion (no
  # basis). The Roth grows at the projection's own rate, so once the Roth draws
  # exceed everything converted, the rest of a draw is earnings. The 2026
  # conversion is the first contribution to any Roth IRA for the owner.

  # Source: IRS Publication 590-B (2025), Ch. 2 "Roth IRAs", "Are Distributions Taxable?" > "Additional Tax on Early Distributions": "You must also pay the 10% additional tax on any portion of the distribution attributable to earnings on contributions." "Ordering Rules for Distributions": "3. Earnings on contributions." (after regular contributions and conversion contributions). https://www.irs.gov/publications/p590b
  # Evidence: (b) rule text. The 2031 draws pass the converted amount, so part of the 2031 draw is earnings. The 2026 conversion's own period (2026-2030) has ended, so no conversion recapture applies, but the owner (45) is under 59 1/2 and the distribution isn't qualified, so the earnings part carries the 10% additional tax.
  Scenario: Earnings drawn before 59 1/2 carry the 10% additional tax after the conversion's five years have ended
    Given a single filer aged 40 in 2026 with a $60,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert the whole IRA in 2026
    When the years through 2031 are projected
    Then in 2031 they draw from the Roth IRA and not from the traditional IRA, beyond everything they converted
    And part of the 2031 Roth draw is earnings
    And the 2031 early-withdrawal additional tax is 10% of that year's Roth earnings drawn

  # Source: as above, "Are Distributions Taxable?": "You don't include in your gross income qualified distributions or distributions that are a return of your regular contributions from your Roth IRA(s). ... You may have to include part of other distributions in your income." Figure 2-1, when the distribution isn't qualified: "The portion of the distribution allocable to earnings may be subject to tax".
  # Evidence: (b) rule text. The owner (45) is under 59 1/2, so the 2031 distribution isn't qualified. Converted amounts were taxed at conversion and come out first; the earnings part of the draw is the part that goes into gross income.
  Scenario: Earnings in a distribution that isn't qualified are gross income
    Given a single filer aged 40 in 2026 with a $60,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert the whole IRA in 2026
    When the years through 2031 are projected
    Then in 2031 they draw from the Roth IRA and not from the traditional IRA, beyond everything they converted
    And part of the 2031 Roth draw is earnings
    And the 2031 gross income is that year's Roth earnings drawn

  # Source: as above, "What Are Qualified Distributions?" (both requirements must be met: "It is made after the 5-year period beginning with the first tax year for which a contribution was made" and "Made on or after the date you reach age 59 1/2"); Figure 2-1 for a distribution that isn't qualified: "The portion of the distribution allocable to earnings may be subject to tax and it may be subject to the 10% additional tax." "Additional Tax on Early Distributions" > "Exceptions": "You have reached age 59 1/2."
  # Evidence: (b) rule text. In 2029 the owner is 61, past 59 1/2, but the 5-year period that began with the 2026 conversion (2026-2030) has not ended, so the distribution isn't qualified: its earnings part is gross income. The age exception means no 10% additional tax.
  Scenario: Earnings drawn after 59 1/2 but within five years of the first contribution are income with no additional tax
    Given a single filer aged 58 in 2026 with a $40,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert the whole IRA in 2026
    When the years through 2029 are projected
    Then in 2029 they draw from the Roth IRA and not from the traditional IRA, beyond everything they converted
    And part of the 2029 Roth draw is earnings
    And there is no 2029 early-withdrawal additional tax
    And the 2029 gross income is that year's Roth earnings drawn

  # Source: IRS Publication 590-B (2025), as above: the earnings part of a distribution that isn't qualified goes into gross income, so it is in AGI. 26 U.S.C. 36B(d)(2)(B): "The term "modified adjusted gross income" means adjusted gross income increased by— (i) any amount excluded from gross income under section 911, (ii) any amount of interest received or accrued by the taxpayer during the taxable year which is exempt from tax, and (iii) an amount equal to the portion of the taxpayer's social security benefits (as defined in section 86(d)) which is not included in gross income under section 86 for the taxable year." https://www.law.cornell.edu/uscode/text/26/36B . 20 CFR 418.1010(b)(6): "Modified adjusted gross income is your adjusted gross income as defined by the Internal Revenue Code, plus the following forms of tax-exempt income: (i) Tax-exempt interest income; ..." 20 CFR 418.1110(c) (example of the 2-year lookback): "We use your 2009 modified adjusted gross income ... to determine your 2011 income-related monthly adjustment amount." https://www.ecfr.gov/current/title-20/part-418
  # Evidence: (b) rule text. With no Social Security, tax-exempt interest or other income, MAGI in 2029 is the Roth earnings drawn. The amounts are far below any IRMAA tier, so the scenario asserts on the MAGI input, not on a surcharge.
  Scenario: Earnings that are income also enter the MAGI used for IRMAA and ACA
    Given a single filer aged 58 in 2026 with a $40,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert the whole IRA in 2026
    When the years through 2031 are projected
    Then in 2029 they draw from the Roth IRA and not from the traditional IRA, beyond everything they converted
    And part of the 2029 Roth draw is earnings
    And the 2029 IRMAA MAGI includes that year's Roth earnings drawn
    And the 2029 ACA MAGI includes that year's Roth earnings drawn
    And the MAGI that sets the 2031 IRMAA is the 2029 MAGI, which includes that year's Roth earnings drawn
