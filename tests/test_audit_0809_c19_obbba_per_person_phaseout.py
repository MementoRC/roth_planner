"""RED-gate regression for audit-0809 finding C19/#04 (senior_bonus_deduction).

engine/tax.py senior_bonus_deduction() applied the OBBBA senior-bonus phase-out
ONCE against the AGGREGATE bonus (total_bonus - rate*excess, floored once).  That
is not how IRS Schedule 1-A (Form 1040), Part V, lines 31-37 computes the amount:
the form derives ONE reduced per-person figure and enters that SAME figure on
BOTH line 36a (you) and line 36b (spouse), then line 37 sums them.  So each
person's $6,000 must be reduced by 6% of the MAGI excess AND INDEPENDENTLY
FLOORED AT ZERO, then summed -- not reduced once against the combined total.

Correct rule (Pub. L. 119-21 §70103 / IRC §151(d)(5)(C)):
    per_person = max(0.0, bonus_per_person - phaseout_rate * max(0.0, magi - phaseout_start))
    deduction  = per_person * eligible

The two formulas are ALGEBRAICALLY IDENTICAL when eligible == 1 (single-eligible
MFJ, Single/HoH). They diverge only for dual-eligible MFJ (eligible == 2), where
the old aggregate formula phased out at MAGI $350,000 instead of the correct
$250,000 ($150,000 + $6,000/0.06).

NAMING CONSTRAINT -- test function names in this file must NOT have exactly 35
characters after the `test_` prefix. TruffleHog's Lob API-key detector matches
`(live|test)_` followed by 35 characters and its verifier stamps any
test_-prefixed candidate as Verified=true, so such a name is reported as a
verified leaked secret. CI runs with fail-on-secrets: true, which makes that a
HARD BLOCK on a pure false positive.
"""

from __future__ import annotations

import pytest

from engine.tax import senior_bonus_deduction


class TestDualEligibleMfjPerPersonFloor:
    """Both spouses 65+, MFJ, MAGI > $150,000 -- the only regime where the
    aggregate and per-person formulas diverge."""

    def test_dual_65_magi_200k_partial(self) -> None:
        # per_person = max(0, 6_000 - 0.06*50_000) = 3_000; total = 2*3_000 = 6_000
        # Pre-fix (aggregate): 12_000 - 0.06*50_000 = 9_000.
        assert senior_bonus_deduction(66, 65, magi=200_000.0, year=2026) == pytest.approx(
            6_000.0
        )

    def test_dual_65_magi_250k_zeros(self) -> None:
        # per_person = max(0, 6_000 - 0.06*100_000) = max(0, 0) = 0; total = 0.0
        # Pre-fix (aggregate): 12_000 - 0.06*100_000 = 6_000 (does not zero here).
        assert senior_bonus_deduction(65, 67, magi=250_000.0, year=2026) == pytest.approx(0.0)

    def test_dual_65_magi_300k_zeros_too(self) -> None:
        # per_person = max(0, 6_000 - 0.06*150_000) = max(0, -3_000) = 0; total = 0.0
        # Pre-fix (aggregate): 12_000 - 0.06*150_000 = 3_000.
        assert senior_bonus_deduction(70, 65, magi=300_000.0, year=2027) == pytest.approx(0.0)


class TestSingleEligibleUnchangedByFix:
    """eligible == 1 makes the two formulas algebraically identical -- these
    pin that the per-person fix does NOT move the single-eligible answer."""

    def test_single_eligible_unchanged(self) -> None:
        # MFJ, only "your" spouse is 65+ (spouse_age=60 < 65): eligible=1.
        # per_person = max(0, 6_000 - 0.06*50_000) = 3_000; total = 3_000*1 = 3_000
        assert senior_bonus_deduction(
            65, 60, magi=200_000.0, year=2026, filing_status="MFJ"
        ) == pytest.approx(3_000.0)

    def test_single_filer_unchanged(self) -> None:
        # Single filer, age 68, MAGI=120_000: threshold=$75,000, eligible=1.
        # per_person = max(0, 6_000 - 0.06*45_000) = max(0, 3_300) = 3_300
        assert senior_bonus_deduction(
            68, 0, magi=120_000.0, year=2026, filing_status="Single"
        ) == pytest.approx(3_300.0)
