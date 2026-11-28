Feature: Income Harvest bugs, red until fixed

  # Confirmed defects in the 0% LTCG harvest (#123 slice 3), written before the fixes.
  # Evidence: IRC §1(h)(1)(B), §86, §151(d)(5)(C), §1211(b), §1222, §1012.
  Background:
    Given ACA coverage is off and there is no inflation

  # C2: a sale that funds living expenses realizes gain = amount sold x (1 - basis / balance).
  Scenario: A brokerage sale that funds living expenses realizes its gain
    Given a married-filing-jointly household, both 60, with no income
    And living expenses of $200,000 a year
    And a brokerage account of $1,000,000 with a cost basis of $200,000, and no growth, dividends or turnover
    When the 2026 shortfall is funded through the projection's expense debit
    And the same shortfall is funded by an explicit brokerage sale
    Then the realized gain, federal tax and MAGI are the same either way

  # C3: the harvest line rounds to a whole dollar and must not round up past what the engine allows.
  Scenario: The harvest line never shows more than the engine's harvest
    Given a married-filing-jointly household, both under 65, with no Social Security
    And a 2026 conversion of $60,000.40 is the only income
    And the conversion tax is paid from cash, not the brokerage account
    When I view Income Control for 2026
    Then the harvest line never shows more than the engine's 0% LTCG harvest

  # C4: tightness in a year with a net capital loss (IRC §1211(b), §1222). The gain is realized the way
  # tests/test_room_lines_harvest.py does it, as the base-year long-term actual through the full scenario.
  Scenario: The harvest is tax-free and tight in a year with a net capital loss
    Given a married-filing-jointly household, both under 65, with no Social Security
    And a 2026 conversion of $60,000 is the only income
    And the conversion tax is paid from cash, not the brokerage account
    And a net capital loss of $10,000 so far in 2026
    When I view Income Control for 2026
    Then realizing the harvest adds no federal tax for the year
    And realizing $2 more than the harvest adds federal tax, unless a non-tax limit binds

  # C5 (owner decision 2026-11-20): the cap is the year-END unrealized gain, closing balance less closing basis.
  @decision
  Scenario: The unrealized-gain cap is the gain at the end of the year
    Given a married-filing-jointly household, both under 65, with no Social Security
    And a 2026 conversion of $60,000 is the only income
    And the conversion tax is paid from cash, not the brokerage account
    And the brokerage holds $10,000 of unrealized gain
    And the brokerage grows 5% in the year with 20% turnover
    When I view Income Control for 2026
    Then the harvest equals the year-end unrealized gain in the brokerage account
    And it stops at the unrealized gain in the brokerage account

  # C6: the conversion lifts MAGI over the senior-deduction phase-out, so it costs more 0% room than its face amount.
  Scenario: A conversion that costs more room than its face amount still uses the 0% room
    Given a married-filing-jointly household, both 70, with no Social Security
    And a $146,000 IRA distribution in 2026
    And a 2026 conversion of $10,000 is planned
    And the conversion tax is paid from cash, not the brokerage account
    When I view Income Control for 2026
    Then the 0% LTCG harvest is $0
    And with no planned conversion the 0% LTCG harvest would be $400
    And the tab says this year's planned conversion already uses the 0% LTCG room

  # C7: §151(d)(5)(C): $6,000 per person, reduced by 6% of MAGI over $150,000 (joint). No Social Security is involved.
  Scenario: A shrinking senior deduction is not taxable Social Security
    Given a married-filing-jointly household, both 66, with no Social Security
    And itemized deductions of $90,000
    And a $140,000 IRA distribution in 2026
    When I view Income Control for 2026
    Then the harvest stops where MAGI reaches $150,000, the senior deduction phase-out threshold
    And the harvest does not stop at the point where more Social Security becomes taxable

  # C9: the conversion's tax is funded by selling brokerage shares; without the conversion that sale never happens.
  Scenario: The room before a conversion leaves out the conversion's own funding sale
    Given a married-filing-jointly household, both under 65, with no Social Security
    And the household spends $200,000 a year from the brokerage account
    And the brokerage cost basis is well below its balance
    And a 2026 conversion of $500,000 is planned
    And the conversion tax is paid from the brokerage account, not cash
    When I view Income Control for 2026
    Then the room before the conversion is the 0% LTCG room of the same year re-run with no planned conversion
    And the 0% LTCG harvest is $0
    And the same year re-run with no planned conversion still has 0% LTCG room
    And the tab says this year's planned conversion already uses the 0% LTCG room

  # C8: IRC §1211(b)(1) (params_round3 R1) lets a net capital loss offset ordinary income up to $3,000 a year.
  # Adjusted taxable income (§1212(b)(2), R5) is far above $3,000 here, so the limit is $3,000.
  Scenario: A net capital loss offsets only a limited amount of ordinary income
    Given a married-filing-jointly household, both under 65, whose brokerage account turns over 20% a year
    And a 2026 conversion of $100,000 is the only income
    And the brokerage falls 5% in 2026
    When the forecast years 2026 and 2027 are projected
    Then the 2026 net capital loss lowers MAGI by $3,000, not by the whole loss
    And the 2026 taxable income falls by $3,000 with the loss
    And the 2026 taxable income is lower by $3,000 and federal tax is lower

  # C8b: IRC §1212(b)(1) (R3, R4): the excess of the loss over the limit carries to the succeeding year.
  Scenario: The excess of a net capital loss carries to the next year
    Given a married-filing-jointly household, both under 65, whose brokerage account turns over 20% a year
    And a 2026 conversion of $100,000 is the only income
    And the brokerage falls 5% in 2026
    And the brokerage rises 10% in 2027
    When the forecast years 2026 and 2027 are projected
    Then the 2027 MAGI from capital gains is the gain less the 2026 loss in excess of $3,000

  # C8e: IRC §1212(b)(2): qualified dividends are part of taxable income, so they count in adjusted taxable income (ATI).
  # Hand figures (synthetic): conversion 32,700 + qualified dividends 2,000 - standard deduction 32,200 = 2,500 before the
  # loss; with the $10,000 loss (limit $3,000 allowed) taxable income = 2,500 - 3,000 = -500;
  # ATI = -500 + 3,000 + (§151 term, $0) = 2,500; used = min(3,000, 2,500) = 2,500; carryover = 10,000 - 2,500 = 7,500;
  # the 2027 capital part is the 2027 gain less 7,500 (19,000 - 7,500 = 11,500 before reinvested dividends lift the 2027 balance).
  # The engine leaves qualified dividends out of ATI (ATI 500, carryover 9,500), so this row is red.
  Scenario: Qualified dividends count in the adjusted taxable income that limits the loss used
    Given a married-filing-jointly household, both under 65, whose brokerage account turns over 20% a year
    And a 2026 conversion of $32,700 is planned
    And the brokerage pays qualified dividends of $2,000 in 2026
    And the brokerage falls 5% in 2026
    And the brokerage rises 10% in 2027
    When the forecast years 2026 and 2027 are projected
    Then the 2027 MAGI from capital gains is the gain less the 2026 loss not used against adjusted taxable income of $2,500

  # C8f: IRC §1212(b)(1): the excess of a net capital loss over the limit carries to the succeeding year, also when the
  # loss is in the base year's actuals. Synthetic actuals: $100,000 of wages and a $10,000 net long-term loss, so ATI is
  # far above $3,000 and the limit is $3,000; carryover = 10,000 - 3,000 = 7,000, and the 2027 capital part is the
  # 2027 gain (read from the engine) less 7,000. The engine zeroes the base year's forecast gains when actuals are
  # present (scenario.py:456) and carries nothing forward, so 2027 shows the whole gain and this row is red.
  Scenario: A net capital loss in this year's actuals carries to next year
    Given a married-filing-jointly household, both under 65, whose brokerage account turns over 20% a year
    And actuals so far in 2026 of $100,000 of wages and a $10,000 net long-term capital loss
    And the brokerage rises 10% in 2027
    When the forecast years 2026 and 2027 are projected
    Then the 2027 MAGI from capital gains is the gain less the $7,000 carried over from the actuals loss

  # C8d: IRC §1212(b)(2) (round 4, quotes verified). For the carryover, a short-term gain equal to the LESSER of the
  # §1211(b) amount or adjusted taxable income (ATI) is treated as arising in the loss year. ATI = taxable income
  # + the §1211(b) amount allowed + the §151 deduction; an excess of deductions over gross income is negative taxable income.
  # Hand figures (synthetic): conversion 32,700 - standard deduction 32,200 = 500 before the loss; with the $10,000 loss
  # (limit $3,000 allowed) taxable income = 500 - 3,000 = -2,500; ATI = -2,500 + 3,000 + (§151 term, $0) = $500 < $3,000.
  Scenario: A low adjusted taxable income uses less than the full limit, so more of the loss carries over
    Given a married-filing-jointly household, both under 65, whose brokerage account turns over 20% a year
    And a 2026 conversion of $32,700 is the only income
    And the brokerage falls 5% in 2026
    And the brokerage rises 10% in 2027
    When the forecast years 2026 and 2027 are projected
    Then the 2027 MAGI from capital gains is the gain less the 2026 loss not used against adjusted taxable income of $500
    And the 2027 carryover is larger than the 2026 loss in excess of $3,000

  # C8g: IRC §1222, §1211(b), §1212(b): all of a year's capital gains and losses net into ONE figure before the $3,000
  # limit. Synthetic MFJ, both 60: actuals of $60,000 of wages and a $10,000 net long-term loss; living expenses of
  # $65,000 a year leave a shortfall that the brokerage account funds by selling shares at a 50% gain share (basis 500,000
  # of 1,000,000), so the sale realizes a gain G.
  # Hand figures: taxable income before the sale = 60,000 - 3,000 allowed loss - 32,200 standard deduction = 24,800, tax 2,480
  # (10% bracket), so the shortfall is 65,000 - (60,000 - 2,480) = 7,480 and G = 7,480 x 50% = about 3,740 (the test reads G
  # from the engine). The year nets to G - 10,000 = about -6,260, a loss; allowed = min(3,000, 10,000 - G) = 3,000;
  # ATI is far above 3,000, so used = 3,000 and carryover = (10,000 - G) - 3,000 = about 3,260.
  # Expected: the 2026 capital part of MAGI is -3,000 (the gain does not separately raise MAGI), and the 2027 capital part
  # is the 2027 gain less (10,000 - G) - 3,000. The engine nets the sale's gain only against the carry-in, so it puts G
  # into MAGI (capital part G - 3,000 = about +740) and carries 7,000 (the actuals loss less the allowed part), so this row is red.
  Scenario: A sale this year nets against the actuals loss before the limit
    Given a married-filing-jointly household, both under 65, whose brokerage account turns over 20% a year
    And actuals so far in 2026 of $60,000 of wages and a $10,000 net long-term capital loss
    And the household spends $65,000 a year from the brokerage account
    And the brokerage cost basis is well below its balance
    And the brokerage rises 10% in 2027
    When the forecast years 2026 and 2027 are projected
    Then the 2026 brokerage sale realizes a gain between $1,000 and $7,000
    And the 2026 MAGI from capital gains is minus the actuals loss netted with the sale's gain, up to $3,000
    And the 2027 MAGI from capital gains is the gain less the 2026 netted loss in excess of $3,000

  # I135a (issue #135): the harvest of a year after a loss year never subtracts the carried-in capital loss, which the
  # scenario nets (IRC §1212(b)(1), §1222). Synthetic MFJ, both 60: a $100,000 conversion is the only 2026 income and
  # the $1,000,000 brokerage (20% turnover) falls 5% in 2026, so the forecast 2026 net loss is 1,000,000 x -5% x 20% =
  # -10,000; ATI is far above 3,000, so 3,000 offsets income and 7,000 carries into 2027. 2027 has no wages and no
  # conversion, so its 0% room is well defined: threshold 98,900 + unused standard deduction 32,200 = 131,100 of room
  # for preferential income. The brokerage opens 2027 at 950,000 and rises 10%, so 2027 turnover realizes
  # 950,000 x 10% x 20% = 19,000. Correct harvest = 131,100 - (19,000 - 7,000) = 119,100. The engine sizes against the
  # 19,000 alone, so it shows 131,100 - 19,000 = 112,100, about 7,000 too small. The oracle is the end-to-end 2027 tax:
  # at 112,102 the 2027 net gain is 19,000 + 112,102 - 7,000 = 124,102, still under 131,100, so $2 more adds no tax and
  # the tightness step fails (red). At 119,100 the stack is exactly full and $2 more adds tax.
  Scenario: I135a: The harvest in the year after a loss year is tight against the carried-in loss
    Given a married-filing-jointly household, both under 65, with no Social Security
    And a 2026 conversion of $100,000 is the only income
    And the conversion tax is paid from cash, not the brokerage account
    And the brokerage turns over 20% a year
    And the brokerage falls 5% in 2026
    And the brokerage rises 10% in 2027
    When I view Income Control for 2027 in the forecast starting 2026
    Then realizing the harvest adds no federal tax for the year
    And realizing $2 more than the harvest adds federal tax, unless a non-tax limit binds

  # I135b (issue #135): carry-in exceeds the year's gains, so the year nets to a loss. IRC §1222, §1211(b), §1212(b)(1), §1(h).
  Scenario: I135b: The harvest in a net-loss carry-in year also absorbs the unused loss
    Given a married-filing-jointly household, both under 65, with no Social Security
    And a 2026 conversion of $100,000 is the only income
    And the conversion tax is paid from cash, not the brokerage account
    And the brokerage turns over 20% a year
    And the brokerage falls 5% in 2026
    And the brokerage rises 1% in 2027
    When I view Income Control for 2027 in the forecast starting 2026
    Then the 0% LTCG harvest is $136,200
    And realizing the harvest adds no federal tax for the year
    And realizing $2 more than the harvest adds federal tax, unless a non-tax limit binds

  # I135c (issue #135): auto-fill sizes a forecast year with a carry-in against the raw gain; the scenario nets it. IRC §1212(b)(1), §1222.
  Scenario: I135c: Auto-fill to the first IRMAA tier agrees with the scenario's netted capital figure
    Given a married-filing-jointly household, both under 65, whose brokerage account turns over 20% a year
    And actuals so far in 2026 of $100,000 of wages and a $10,000 net long-term capital loss
    And the brokerage rises 10% in 2027
    When the conversion plan is auto-filled to stay under the first IRMAA tier
    And the forecast years 2026 and 2027 are projected
    Then the 2027 MAGI is $218,000, the first IRMAA tier

  # I135d (issue #135): in the base year auto-fill adds a forced sale's gain raw on top of the YTD-alone capital figure; the scenario nets them. IRC §1222, §1211(b), §1(h).
  Scenario: I135d: Auto-fill nets a forced brokerage sale with the actuals loss in the base year
    Given a married-filing-jointly household, both under 65, whose brokerage account turns over 20% a year
    And actuals so far in 2026 of $0 of wages and a $10,000 net long-term capital loss
    And the household spends $12,000 a year from the brokerage account
    And the brokerage cost basis is 50% of its balance
    When the conversion plan is auto-filled to stay under the first IRMAA tier
    And the forecast years 2026 and 2027 are projected
    Then the 2026 brokerage sale realizes a gain between $5,990 and $6,010
    And the 2026 MAGI is $218,000, the first IRMAA tier

  # I137a (issue #137): a prior-year capital loss carryover has no input; the base year starts it at 0. IRC §1212(b)(1), §1211(b), §1222.
  Scenario: I137a: A prior-year long-term capital loss carryover is netted in the base year
    Given a married-filing-jointly household, both under 65, whose brokerage account turns over 20% a year
    And a 2026 conversion of $100,000 is planned
    And a $10,000 long-term capital loss carryover from the 2025 return
    And the brokerage rises 10% in 2027
    When the forecast years 2026 and 2027 are projected
    Then the 2026 MAGI comes to $97,000
    And the 2026 taxable income comes to $64,800
    And the 2027 MAGI comes to $13,000

  # I137b (issue #137): base-year auto-fill with actuals ignores a prior-year carryover when there is no forced sale. IRC §1212(b)(1), §1211(b).
  Scenario: I137b: Auto-fill nets a prior-year carryover with the actuals in the base year
    Given a married-filing-jointly household, both under 65, whose brokerage account turns over 20% a year
    And actuals so far in 2026 of $0 of wages and a $1,000 net long-term capital loss
    And a $10,000 long-term capital loss carryover from the 2025 return
    When the conversion plan is auto-filled to stay under the first IRMAA tier
    And the forecast years 2026 and 2027 are projected
    Then the 2026 MAGI is $218,000, the first IRMAA tier

  # C8h: guard: consequence of C8g; room_lines netting; not red-first. The 0% LTCG harvest sizes against ONE base-year
  # net: YTD short-term + (YTD long-term + this year's realized sale gain), the same single figure the scenario nets
  # (IRC §1222, §1211(b)). Synthetic MFJ, both under 65: a $60,000 conversion is the only ordinary income, a $10,000 net
  # long-term loss is in the year's actuals, and $12,000 of living expenses are funded by selling brokerage shares at a
  # 50% gain share (basis 500,000 of 1,000,000), so the sale realizes about 6,000, smaller than the loss. The year nets
  # to about -4,000; the harvest's first dollars only shrink that loss, so the harvest is larger than the 0% room alone.
  # Stacking the sale gain on top of the actuals' preferential income (the old room_lines) would size the harvest about
  # 6,000 too small: realizing $2 more would then add no tax and the tightness assertion would fail.
  Scenario: The harvest is tax-free and tight when an actuals loss and a sale gain net together
    Given a married-filing-jointly household, both under 65, with no Social Security
    And a 2026 conversion of $60,000 is the only income
    And the conversion tax is paid from cash, not the brokerage account
    And a net capital loss of $10,000 so far in 2026
    And the household spends $12,000 a year from the brokerage account
    And the brokerage cost basis is well below its balance
    When I view Income Control for 2026
    Then realizing the harvest adds no federal tax for the year
    And realizing $2 more than the harvest adds federal tax, unless a non-tax limit binds
