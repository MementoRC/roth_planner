Feature: Whole-year federal income tax for a retired couple

  # Source: IRS Rev. Proc. 2025-32 (tax year 2026): section 4.01 Table 1 (MFJ: $11,600 plus 22% of the excess over $100,800), section 4.14(1) standard deduction MFJ $32,200, section 4.14(3) additional amount for the aged $1,650 (married). https://www.irs.gov/pub/irs-drop/rp-25-32.pdf
  #         IRS Publication 915 (2025), Worksheet 1 (taxable benefits). https://www.irs.gov/publications/p915
  #         IRS Schedule 1-A (Form 1040) 2025, Part V (enhanced deduction for seniors, entered on Form 1040 line 13b below AGI). https://www.irs.gov/pub/irs-pdf/f1040s1a.pdf
  # Evidence: (b) tables + arithmetic. Both spouses are 66 at the end of 2026. The only income is Social Security and a traditional IRA distribution. Taxable income >= $100,000, so the rate schedule applies exactly (no Tax Table rounding). No credits and no other deductions.
  # Arithmetic: taxable benefits (Worksheet 1): line 2 = 25,000; line 8 = 150,000 + 25,000 = 175,000; line 10 = 143,000; line 12 = 131,000; line 15 = min(25,000, 6,000) = 6,000;
  #             line 16 = 0.85 x 131,000 = 111,350; line 17 = 117,350; line 18 = 0.85 x 50,000 = 42,500 -> 42,500.
  #             AGI = 150,000 + 42,500 = 192,500 (MAGI for Schedule 1-A is the same).
  #             Standard deduction = 32,200 + 2 x 1,650 = 35,500.
  #             Senior deduction: (192,500 - 150,000) x 6% = 2,550; 6,000 - 2,550 = 3,450 each; 6,900 total.
  #             Taxable income = 192,500 - 35,500 - 6,900 = 150,100.
  #             Tax = 11,600 + 22% x (150,100 - 100,800) = 11,600 + 22% x 49,300 = 11,600 + 10,846 = 22,446.
  Scenario: Retired couple, both 66, with Social Security and an IRA distribution
    Given a married-filing-jointly household, both 66 at the end of 2026, taking the standard deduction
    And their combined 2026 Social Security benefits are $50,000
    And they take a $150,000 distribution from a traditional IRA in 2026
    When their 2026 federal income tax is figured
    Then $42,500 of their benefits is taxable
    And their adjusted gross income is $192,500
    And their standard deduction is $35,500
    And their enhanced deduction for seniors is $6,900
    And their taxable income is $150,100
    And their federal income tax is $22,446
