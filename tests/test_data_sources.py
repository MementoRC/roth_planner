"""Tests for models/sourced.py — provenance-carrying value/dict/list wrappers.

Pure model tests: no streamlit, no engine imports, no pytest fixtures requiring
external services. All datetimes are fixed literals (never datetime.now()).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

from models.sourced import Provenance, Source, SourcedDict, SourcedList, SourcedValue

FIXED_DT = datetime(2026, 7, 16, 12, 30, 45, 123456)


class TestSourcedModel:
    # -- Provenance -------------------------------------------------------

    def test_provenance_to_json_from_json_round_trip(self) -> None:
        prov = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT, detail="user entered")
        payload = prov.to_json()
        assert payload == {
            "source": "MANUAL",
            "recorded_at": FIXED_DT.isoformat(),
            "detail": "user entered",
        }
        restored = Provenance.from_json(payload)
        assert restored == prov
        assert restored.source == Source.MANUAL
        assert restored.recorded_at == FIXED_DT
        assert restored.detail == "user entered"

    def test_provenance_is_frozen(self) -> None:
        prov = Provenance(source=Source.PDF, recorded_at=FIXED_DT)
        assert dataclasses.fields(prov)  # sanity: it is a dataclass
        with pytest.raises(dataclasses.FrozenInstanceError):
            prov.source = Source.MANUAL  # type: ignore[misc]

    def test_provenance_default_detail_is_empty_string(self) -> None:
        prov = Provenance(source=Source.ESTIMATE, recorded_at=FIXED_DT)
        assert prov.detail == ""

    # -- SourcedValue -------------------------------------------------------

    def test_sourced_value_arithmetic_returns_plain_float(self) -> None:
        prov = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT)
        sv = SourcedValue(100.0, prov)

        product = sv * 1.07
        assert type(product) is float
        assert product == pytest.approx(107.0)

        added = sv + 5.0
        assert type(added) is float
        assert added == pytest.approx(105.0)

        subtracted = sv - 5.0
        assert type(subtracted) is float
        assert subtracted == pytest.approx(95.0)

        divided = sv / 4.0
        assert type(divided) is float
        assert divided == pytest.approx(25.0)

    def test_sourced_value_float_conversion_and_prov_access(self) -> None:
        prov = Provenance(source=Source.FINEXTRACT_LIVE, recorded_at=FIXED_DT, detail="synced")
        sv = SourcedValue(285000.0, prov)
        assert float(sv) == 285000.0
        assert sv.prov is prov
        assert sv.prov.source == Source.FINEXTRACT_LIVE
        assert sv.prov.detail == "synced"

    def test_sourced_value_round_trip_with_microseconds(self) -> None:
        prov = Provenance(source=Source.BUNDLE, recorded_at=FIXED_DT, detail="imported")
        sv = SourcedValue(42.5, prov)
        payload = sv.to_json()
        assert payload == {
            "value": 42.5,
            "source": "BUNDLE",
            "recorded_at": FIXED_DT.isoformat(),
            "detail": "imported",
        }
        restored = SourcedValue.from_json(payload)
        assert float(restored) == 42.5
        assert restored.prov.source == Source.BUNDLE
        assert restored.prov.detail == "imported"
        assert restored.prov.recorded_at == FIXED_DT
        assert restored.prov.recorded_at.microsecond == 123456

    # -- SourcedDict -------------------------------------------------------

    def test_sourced_dict_per_key_provenance_and_get(self) -> None:
        prov_2024 = Provenance(source=Source.PDF, recorded_at=FIXED_DT, detail="1040 2024")
        prov_2025 = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT, detail="estimate 2025")
        data = {2024: 285000.0, 2025: 290000.0}
        prov = {2024: prov_2024, 2025: prov_2025}
        sd = SourcedDict(data, prov)

        assert sd.get(2024) == 285000.0
        assert sd[2025] == 290000.0
        assert sd.prov[2024].source == Source.PDF
        assert sd.prov[2025].source == Source.MANUAL
        assert sd.prov[2024].detail == "1040 2024"

    def test_sourced_dict_round_trip_preserves_int_keys_and_prov(self) -> None:
        prov_2024 = Provenance(source=Source.PDF, recorded_at=FIXED_DT, detail="1040 2024")
        prov_2025 = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT, detail="estimate 2025")
        sd = SourcedDict(
            {2024: 285000.0, 2025: 290000.0},
            {2024: prov_2024, 2025: prov_2025},
        )
        payload = sd.to_json()
        restored = SourcedDict.from_json(payload, key_type=int)

        assert set(restored.keys()) == {2024, 2025}
        assert all(isinstance(k, int) for k in restored)
        assert restored[2024] == 285000.0
        assert restored[2025] == 290000.0
        assert restored.prov[2024].source == Source.PDF
        assert restored.prov[2025].source == Source.MANUAL
        assert restored.prov[2024].detail == "1040 2024"
        assert restored.prov[2025].recorded_at == FIXED_DT

    # -- SourcedList -------------------------------------------------------

    def test_sourced_list_per_element_provenance_aligned_by_index(self) -> None:
        prov_a = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT, detail="first")
        prov_b = Provenance(source=Source.ESTIMATE, recorded_at=FIXED_DT, detail="second")
        sl = SourcedList([100.0, 200.0], [prov_a, prov_b])

        assert sl[0] == 100.0
        assert sl[1] == 200.0
        assert sl.prov[0].source == Source.MANUAL
        assert sl.prov[1].source == Source.ESTIMATE
        assert sl.prov[0].detail == "first"
        assert sl.prov[1].detail == "second"

    def test_sourced_list_round_trip_preserves_alignment(self) -> None:
        prov_a = Provenance(source=Source.DEFAULT, recorded_at=FIXED_DT)
        prov_b = Provenance(source=Source.UNKNOWN, recorded_at=FIXED_DT, detail="fallback")
        sl = SourcedList([1.5, 2.5, 3.5], [prov_a, prov_b, prov_b])
        payload = sl.to_json()
        restored = SourcedList.from_json(payload)

        assert list(restored) == [1.5, 2.5, 3.5]
        assert len(restored.prov) == 3
        assert restored.prov[0].source == Source.DEFAULT
        assert restored.prov[1].source == Source.UNKNOWN
        assert restored.prov[1].detail == "fallback"
        assert restored.prov[2].source == Source.UNKNOWN
