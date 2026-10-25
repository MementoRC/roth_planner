"""audit-1018 W7: statement parsing and queue fixes (F-15, F-16, F-17, F-18).

Synthetic data only.
"""

from __future__ import annotations

import pytest

import engine.brokerage_statement_pdf as bsp
from engine.brokerage_statement_pdf import (
    BrokerageStatementRecord,
    apply_account_type_overrides,
    parse_statement_text,
    partition_by_account_type,
)
from engine.ssa_statement_pdf import (
    SsaStatement,
    load_ssa_pdf_estimates,
    parse_ssa_statement_text,
    pick_latest_ssa,
    save_ssa_pdf_estimates,
)

# ---------------------------------------------------------------- F-15


def _ssa(date: str, owner: str | None = None, fra: float = 1500.0) -> SsaStatement:
    return SsaStatement(owner_key=owner, statement_date=date, fra_age=67, estimates={67: fra})


class TestF15NamelessSsaQueue:
    def test_two_nameless_statements_both_stay_queued(self):
        a = _ssa("2026-03-01", fra=1500.0)
        b = _ssa("2026-04-02", fra=2400.0)
        assert pick_latest_ssa([a, b]) == [a, b]

    def test_identical_nameless_statement_is_deduplicated(self):
        a = _ssa("2026-03-01")
        assert pick_latest_ssa([a, _ssa("2026-03-01")]) == [a]

    def test_named_statements_still_keep_newest_per_holder(self):
        old = _ssa("2026-03-01", owner="PAT SAMPLE", fra=1000.0)
        new = _ssa("2026-04-01", owner="PAT SAMPLE", fra=1100.0)
        assert pick_latest_ssa([old, new]) == [new]


# ---------------------------------------------------------------- F-16

_IBKR_TEMPLATE = """Interactive Brokers LLC
Activity Statement
September 30, 2026
Account Information
Name JOHN Q SAMPLE
Account {acct}
Customer Type Individual
Cash Report
Dividends 0.00 0.00 0.00 10.00 1,234.56
Broker Interest Paid and Received 0.00 0.00 0.00 5.00 321.00
"""


class TestF16IbkrSevenDigitAccount:
    @pytest.mark.parametrize("acct", ["U12345678", "U1234567"])
    def test_account_id_widths_parse(self, acct):
        recs = parse_statement_text([_IBKR_TEMPLATE.format(acct=acct)])
        assert [r.account_number for r in recs] == [acct]
        assert recs[0].account_type == "taxable"
        assert recs[0].dividends_taxable_ytd == pytest.approx(1234.56)


# ---------------------------------------------------------------- F-17

_SSA_PAGE = """Your Social Security Statement
March 1, 2026
Your full retirement age is {fra}.
62 $1,400
63 $1,500
64 $1,600
65 $1,700
66 $1,889
67 $2,027
68 $2,200
69 $2,350
70 $2,507
Form SSA-7005
"""


class TestF17FraMonths:
    def test_fra_66_and_10_months_derives_fra_amount(self):
        stmt = parse_ssa_statement_text([_SSA_PAGE.format(fra="66 and 10 months")])
        assert stmt.fra_age == 66
        assert stmt.fra_months == 10
        # age-66 claim is 10 months early: reduced 5/9 of 1% per month.
        assert stmt.fra_monthly == pytest.approx(1889 / (1 - 10 * 5 / 900))

    def test_whole_year_fra_unchanged(self):
        stmt = parse_ssa_statement_text([_SSA_PAGE.format(fra="66")])
        assert stmt.fra_months == 0
        assert stmt.fra_monthly == 1889

    def test_fra_months_survive_persistence(self):
        stmt = parse_ssa_statement_text([_SSA_PAGE.format(fra="66 and 10 months")])
        save_ssa_pdf_estimates("you", stmt)
        loaded = load_ssa_pdf_estimates("you")
        assert loaded is not None
        assert loaded.fra_monthly == pytest.approx(stmt.fra_monthly)


# ---------------------------------------------------------------- F-18


def _rec(broker: str, number: str = "1234-5678", **kw) -> BrokerageStatementRecord:
    base = {
        "account_number": number,
        "broker": broker,
        "account_type": "unknown",
        "statement_period_end": "2026-06-30",
        "interest_taxable_ytd": 0.0,
        "interest_tax_exempt_ytd": 0.0,
        "dividends_taxable_ytd": 100.0,
        "dividends_tax_exempt_ytd": 0.0,
        "stcg_net_ytd": 0.0,
        "ltcg_net_ytd": 0.0,
        "captured_at": "2026-07-10T00:00:00+00:00",
    }
    base.update(kw)
    return BrokerageStatementRecord(**base)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(bsp, "_ACCOUNT_TYPE_OVERRIDES_PATH", tmp_path / "overrides.json")
    monkeypatch.setattr(bsp, "_STATEMENT_CACHE_PATH", tmp_path / "cache.json")
    return tmp_path


class TestF18BrokerKeyedOverrides:
    def test_override_for_one_broker_does_not_touch_the_other(self, paths):
        schwab, ubs = _rec("schwab"), _rec("ubs")
        bsp.save_account_type_override("1234-5678", "taxable", broker="schwab")
        result = apply_account_type_overrides({"1234-5678": ubs}, bsp.load_account_type_overrides())
        assert result["1234-5678"].account_type == "unknown"
        _, _, needs = partition_by_account_type(result)
        assert "1234-5678" in needs
        result = apply_account_type_overrides(
            {"1234-5678": schwab}, bsp.load_account_type_overrides()
        )
        assert result["1234-5678"].account_type == "taxable"

    def test_override_applies_whichever_dict_key_the_record_holds(self, paths):
        ubs = _rec("ubs")
        bsp.save_account_type_override("1234-5678", "taxable", broker="ubs")
        result = apply_account_type_overrides(
            {"ubs:1234-5678": ubs}, bsp.load_account_type_overrides()
        )
        assert result["ubs:1234-5678"].account_type == "taxable"

    def test_legacy_bare_override_migrates_to_the_cached_broker(self, paths):
        bsp.write_pii_json(bsp._ACCOUNT_TYPE_OVERRIDES_PATH, {"1234-5678": "taxable"})
        bsp.write_pii_json(bsp._STATEMENT_CACHE_PATH, {"1234-5678": _rec("schwab").to_dict()})
        overrides = bsp.load_account_type_overrides()
        assert overrides == {"schwab:1234-5678": "taxable"}
        # persisted, and the other broker's same-number account is unaffected
        result = apply_account_type_overrides({"1234-5678": _rec("ubs")}, overrides)
        assert result["1234-5678"].account_type == "unknown"

    def test_ambiguous_legacy_override_is_dropped(self, paths):
        bsp.write_pii_json(bsp._ACCOUNT_TYPE_OVERRIDES_PATH, {"1234-5678": "taxable"})
        bsp.write_pii_json(
            bsp._STATEMENT_CACHE_PATH,
            {
                "1234-5678": _rec("schwab").to_dict(),
                "ubs:1234-5678": _rec("ubs").to_dict(),
            },
        )
        assert bsp.load_account_type_overrides() == {}

    def test_unmatched_legacy_override_still_applies_by_number(self, paths):
        bsp.write_pii_json(bsp._ACCOUNT_TYPE_OVERRIDES_PATH, {"9999-0001": "taxable"})
        overrides = bsp.load_account_type_overrides()
        result = apply_account_type_overrides({"9999-0001": _rec("schwab", "9999-0001")}, overrides)
        assert result["9999-0001"].account_type == "taxable"
