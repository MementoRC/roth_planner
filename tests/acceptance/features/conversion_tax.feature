Feature: Federal ordinary income tax added by a Roth conversion

  # Source: IRS Rev. Proc. 2025-32 (tax year 2026, reflects OBBBA), section 4.01 Table 1 (MFJ) and section 4.14(1) standard deduction MFJ $32,200. https://www.irs.gov/pub/irs-drop/rp-25-32.pdf
  # Evidence: (b) table + arithmetic. Both under 65: no aged additional deduction, no OBBBA senior deduction. Taxable income >= $100,000, so the rate schedule applies exactly (no Tax Table rounding).
  # Arithmetic: without = 150,000 - 32,200 = 117,800 -> 11,600 + 22% x (117,800 - 100,800) = 11,600 + 3,740 = 15,340.
  #             with = 167,800 -> 11,600 + 22% x 67,000 = 11,600 + 14,740 = 26,340. Added = 11,000.
  Scenario: Conversion stays inside the 22% bracket
    Given a married-filing-jointly household, both under 65, with 2026 wages of $150,000 and the standard deduction
    When they convert $50,000 to Roth in 2026
    Then the federal income tax without the conversion is $15,340
    And the federal income tax with the conversion is $26,340
    And the added federal income tax is $11,000

  # Source: as above, section 4.01 Table 1 (24% row: $35,932 plus 24% over $211,400).
  # Evidence: (b) table + arithmetic.
  # Arithmetic: with = 117,800 + 100,000 = 217,800 -> 35,932 + 24% x 6,400 = 35,932 + 1,536 = 37,468. Added = 37,468 - 15,340 = 22,128
  #             (check: 93,600 x 22% = 20,592 plus 6,400 x 24% = 1,536, total 22,128).
  Scenario: Conversion crosses into the 24% bracket
    Given a married-filing-jointly household, both under 65, with 2026 wages of $150,000 and the standard deduction
    When they convert $100,000 to Roth in 2026
    Then the added federal income tax is $22,128
