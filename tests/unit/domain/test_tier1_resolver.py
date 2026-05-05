from __future__ import annotations

from domain.enums.failure_code import FailureCode
from domain.rules.tier1_resolver import build_missing_fields


def test_unknown_codes_are_silently_ignored():
    out = build_missing_fields(["NOT_A_REAL_CODE"])
    assert out == []


def test_returns_symbolic_paths_when_no_fare_event():
    out = build_missing_fields([FailureCode.T1_R3_CITY_DATE_REQUIRED])
    paths = [mf.path for mf in out]
    assert (
        "itineraries[*].segments[*].departure.iataCode" in paths
    ), f"got {paths}"
    assert all(mf.code == "T1_R3_CITY_DATE_REQUIRED" for mf in out)
    assert all(mf.label for mf in out)


def test_resolves_concrete_indices_against_fare_event():
    fare_event = {
        "itineraries": [
            {
                "segments": [
                    {
                        "departure": {"iataCode": "CDG", "at": "2026-06-01T08:00"},
                        "arrival": {"iataCode": "JFK", "at": "2026-06-01T11:00"},
                    },
                    {
                        "departure": {"iataCode": None, "at": None},
                        "arrival": {"iataCode": "LHR", "at": "2026-06-02T07:00"},
                    },
                ]
            }
        ]
    }
    out = build_missing_fields(
        [FailureCode.T1_R3_CITY_DATE_REQUIRED], fare_event=fare_event
    )
    paths = sorted(mf.path for mf in out)
    assert any("segments[1].departure.iataCode" in p for p in paths)
    assert any("segments[1].departure.at" in p for p in paths)
    assert not any("segments[0].departure.iataCode" in p for p in paths)


def test_locale_switches_label_and_fix_hint():
    fr = build_missing_fields(
        [FailureCode.T1_R4_PRICE_REQUIRED], locale="fr"
    )
    en = build_missing_fields(
        [FailureCode.T1_R4_PRICE_REQUIRED], locale="en"
    )
    assert fr and en
    assert fr[0].label != en[0].label
    assert fr[0].fix_hint != en[0].fix_hint


def test_accepts_string_codes_or_enum():
    out = build_missing_fields(
        ["T1_R4_PRICE_REQUIRED", FailureCode.T1_R5_CABIN_REQUIRED]
    )
    codes = {mf.code for mf in out}
    assert codes == {"T1_R4_PRICE_REQUIRED", "T1_R5_CABIN_REQUIRED"}
