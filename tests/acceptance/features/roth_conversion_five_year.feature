Feature: 10% additional tax on converted Roth amounts withdrawn within five years

  # Every household is a single filer with a traditional IRA, no Roth IRA before
  # the conversions, no wages, no Social Security and no brokerage account. It
  # lives on $9,000 a year, which the projection funds by its own withdrawal order.
  # Converted amounts were fully taxable at conversion (no basis).

  # Source: IRS Publication 590-B (2025), Ch. 2 "Roth IRAs", "Are Distributions Taxable?" > "Additional Tax on Early Distributions" > "Distributions of conversion and certain rollover contributions within 5-year period". https://www.irs.gov/publications/p590b
  # Evidence: (b) rule text. "You must generally pay the 10% additional tax on any amount attributable to the part of the amount converted ... that you had to include in income" if the distribution is "within the 5-year period starting with the first day of your tax year in which you convert an amount". A 2026 conversion's period covers 2026-2030; age 52 is under 59 1/2.
  Scenario: Converted money drawn two years after the conversion, before 59 1/2
    Given a single filer aged 50 in 2026 with a $200,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert the whole IRA in 2026
    When the years through 2028 are projected
    Then in 2028 they draw from the Roth IRA and not from the traditional IRA
    And the 2028 early-withdrawal additional tax is 10% of that year's Roth draw

  # Source: as above. Same heading: "A separate 5-year period applies to each conversion"; example: a conversion made February 25, 2025 has a 5-year period that "begins January 1, 2025".
  # Evidence: (b) rule text + worked example. The period for a 2026 conversion is tax years 2026, 2027, 2028, 2029 and 2030. A 2031 distribution is outside it, so although the owner (55) is under 59 1/2 the conversion recapture does not apply.
  Scenario: Converted money drawn after the five-year period has ended
    Given a single filer aged 50 in 2026 with a $200,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert the whole IRA in 2026
    When the years through 2031 are projected
    Then in 2031 they draw from the Roth IRA and not from the traditional IRA
    And there is no 2031 early-withdrawal additional tax

  # Source: as above, "Additional Tax on Early Distributions" > "Exceptions": "You have reached age 59 1/2."
  # Evidence: (b) rule text. The 2030 distribution is inside the 2026 conversion's period, but the owner is 60, past 59 1/2, so the exception applies.
  Scenario: Converted money drawn inside the five years after reaching 59 1/2
    Given a single filer aged 56 in 2026 with a $200,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert the whole IRA in 2026
    When the years through 2030 are projected
    Then in 2030 they draw from the Roth IRA and not from the traditional IRA
    And there is no 2030 early-withdrawal additional tax

  # Source: as above, "Ordering Rules for Distributions": regular contributions, then "Conversion and rollover contributions, on a first-in, first-out basis"; within each, "Taxable portion ... first", then "Nontaxable portion", then "Earnings on contributions". "A separate 5-year period applies to each conversion."
  # Evidence: (b) rule text. The $5,000 2026 conversion is used up by the first Roth draws (2028), so the 2031 draw comes from the 2027 conversion, whose period (2027-2031) is still running. The owner (55) is under 59 1/2.
  Scenario: The older conversion is used up first, so a later draw is from the newer conversion
    Given a single filer aged 50 in 2026 with a $130,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert $5,000 of the IRA in 2026
    And they convert the rest of the IRA in 2027
    When the years through 2031 are projected
    Then in 2031 they draw from the Roth IRA and not from the traditional IRA
    And the 2031 early-withdrawal additional tax is 10% of that year's Roth draw

  # Source: as above, "Ordering Rules for Distributions" (first-in, first-out across conversions).
  # Evidence: (b) rule text. Roth draws from 2028 to 2031 stay well inside the $200,000 2026 conversion, so the 2031 draw is still that conversion's money. Its period ended with 2030, so no recapture applies, even though the 2027 conversion's period is still running.
  Scenario: A draw still covered by the older conversion is not taxed because of a newer one
    Given a single filer aged 50 in 2026 with a $320,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert $200,000 of the IRA in 2026
    And they convert the rest of the IRA in 2027
    When the years through 2031 are projected
    Then in 2031 they draw from the Roth IRA and not from the traditional IRA
    And there is no 2031 early-withdrawal additional tax

  # Source: as above, "Ordering Rules for Distributions": a distribution that isn't qualified comes from "Regular contributions" first, then "Conversion and rollover contributions, on a first-in, first-out basis", then "Earnings on contributions". The recapture applies only to "the portion of the distribution attributable to the part of the conversion" included in income.
  # Evidence: (b) rule text. The $20,000 of regular contributions comes out before the 2026 conversion, so a 2027 draw of living expenses (well under $20,000) is a return of contributions. No part is attributable to the conversion, so although that conversion's period (2026-2030) is running and the owner (51) is under 59 1/2, no additional tax applies.
  Scenario: Regular contributions come out first, so a draw they cover is not taxed because of a conversion
    Given a single filer aged 50 in 2026 with a $200,000 traditional IRA and a $20,000 Roth IRA of regular contributions
    And living expenses of $9,000 a year and no other income
    And they convert the whole IRA in 2026
    When the years through 2027 are projected
    Then in 2027 they draw from the Roth IRA and not from the traditional IRA
    And there is no 2027 early-withdrawal additional tax

  # Source: as above, "Ordering Rules for Distributions" (regular contributions, then conversions first-in, first-out) and "A separate 5-year period applies to each conversion".
  # Evidence: (b) rule text. Roth draws from 2028 to 2030 (living expenses, about $29,500 with the projection's expense growth) come from the $36,000 of contributions, leaving about $6,500. The 2031 draw (about $10,400) uses up those contributions and then takes part of the $5,000 2026 conversion, whose period ended with 2030. None of it reaches the 2027 conversion, whose period (2027-2031) is still running, so no additional tax applies although the owner (55) is under 59 1/2.
  Scenario: A draw that spans contributions and an expired conversion is not taxed because of a newer one
    Given a single filer aged 50 in 2026 with a $200,000 traditional IRA and a $36,000 Roth IRA of regular contributions
    And living expenses of $9,000 a year and no other income
    And they convert $5,000 of the IRA in 2026
    And they convert the rest of the IRA in 2027
    When the years through 2031 are projected
    Then in 2031 they draw from the Roth IRA and not from the traditional IRA
    And there is no 2031 early-withdrawal additional tax

  # Source: as above, "A separate 5-year period applies to each conversion"; the recapture applies to "any amount attributable to the part of the amount converted ... that you had to include in income".
  # Evidence: (b) rule text. In 2028 both conversions are inside their periods (2026-2030 and 2027-2031), so every converted dollar drawn is exposed, whichever conversion it comes from. First-in, first-out, the 2028 draw comes from the 2026 conversion; the 2027 conversion (what is left of the IRA after 2026, a few thousand dollars, less than a year's draw) does not change that. The recapture applies to the whole draw. The owner (52) is under 59 1/2.
  Scenario: A draw is taxed in full while every conversion it could come from is inside its five years
    Given a single filer aged 50 in 2026 with a $200,000 traditional IRA and no Roth IRA
    And living expenses of $9,000 a year and no other income
    And they convert $138,000 of the IRA in 2026
    And they convert the rest of the IRA in 2027
    When the years through 2028 are projected
    Then in 2028 they draw from the Roth IRA and not from the traditional IRA
    And the 2028 early-withdrawal additional tax is 10% of that year's Roth draw
