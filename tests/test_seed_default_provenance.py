"""First-load seed values are stamped Source.DEFAULT (badge "unconfirmed")."""

from __future__ import annotations

from datetime import datetime

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import COMMITTED_FIELD_ATTRS, migrate_committed
from engine.data_sources.orchestrator import resolve_for_app
from engine.data_sources.seed_defaults import seed_default_fields
from engine.data_status import compute_data_completeness
from models.household import Household
from models.sourced import Source, SourcedDict, SourcedList, SourcedValue

T0 = datetime(2026, 7, 24, 12, 0, 0)
T1 = datetime(2026, 7, 25, 12, 0, 0)
SCALARS = [a for a in COMMITTED_FIELD_ATTRS if a not in ("prior_year_magi", "grants")]
FIELDS = [*SCALARS, "grants"]


def _first_load(hh: Household):
    return resolve_for_app(hh, None, {}, CandidateStore(), ChoiceMap(), None, T0)


def _severities(hh: Household, fields: list[str], now: datetime = T0) -> dict[str, str]:
    result = compute_data_completeness(hh, fields, set(), now=now)
    return {i.field: i.severity for i in result.items}


def test_migrate_committed_stamps_default_fields_only() -> None:
    hh = Household()
    hh.prior_year_magi = {2025: 1.0}

    out = migrate_committed(hh, T0, default_fields={"your_ira", "grants", "prior_year_magi"})

    assert out["your_ira"]["source"] == "DEFAULT"
    assert out["your_ira"]["detail"] == "seed default"
    assert out["spouse_ira"]["source"] == "UNKNOWN"
    assert out["spouse_ira"]["detail"] == "pre-migration"
    assert isinstance(hh.grants, SourcedList)
    assert {p.source for p in hh.grants.prov} == {Source.DEFAULT}
    assert isinstance(hh.prior_year_magi, SourcedDict)
    assert hh.prior_year_magi.prov[2025].source is Source.DEFAULT


def test_migrate_committed_default_signature_unchanged() -> None:
    hh = Household()
    migrate_committed(hh, T0)
    assert isinstance(hh.your_ira, SourcedValue)
    assert hh.your_ira.prov.source is Source.UNKNOWN


def test_helper_flags_fresh_household_and_excludes_changed_field() -> None:
    assert seed_default_fields(Household()) == set(FIELDS)

    hh = Household()
    hh.your_ira = hh.your_ira + 1234.0
    flagged = seed_default_fields(hh)
    assert "your_ira" not in flagged
    assert flagged == set(FIELDS) - {"your_ira"}


def test_first_load_fresh_household_is_unconfirmed() -> None:
    out = _first_load(Household())
    hh = out.result.household

    sev = _severities(hh, FIELDS)
    assert set(sev.values()) == {"unconfirmed"}
    assert out.committed_json["your_ira"]["source"] == "DEFAULT"


def test_first_load_changed_field_stays_unknown_and_ok() -> None:
    session = Household()
    session.your_ira = session.your_ira + 1234.0
    hh = _first_load(session).result.household

    sev = _severities(hh, FIELDS)
    assert sev["your_ira"] == "ok"
    assert hh.your_ira.prov.source is Source.UNKNOWN
    assert sev["spouse_ira"] == "unconfirmed"


def test_existing_committed_file_is_not_restamped() -> None:
    first = _first_load(Household())
    committed = first.committed_json
    committed["your_ira"]["source"] = "UNKNOWN"  # an existing user's on-disk baseline

    out = resolve_for_app(Household(), None, {}, CandidateStore(), ChoiceMap(), committed, T1)

    assert out.migrated is False
    assert out.committed_json["your_ira"]["source"] == "UNKNOWN"
    assert out.result.household.your_ira.prov.source is Source.UNKNOWN
    assert out.result.household.spouse_ira.prov.source is Source.DEFAULT


def test_manual_edit_promotes_default_field_to_manual_ok() -> None:
    committed = _first_load(Household()).committed_json
    edited = Household()
    edited.your_ira = edited.your_ira + 5000.0

    out = resolve_for_app(edited, None, {}, CandidateStore(), ChoiceMap(), committed, T1)
    hh = out.result.household

    assert hh.your_ira.prov.source is Source.MANUAL
    assert _severities(hh, FIELDS, T1)["your_ira"] == "ok"
