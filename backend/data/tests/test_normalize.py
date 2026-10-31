import csv
from pathlib import Path

import pytest

from backend.data import timezones
from backend.data.normalize import normalize_row, parse_hhmm, qualifies

FIXTURES = Path(__file__).parent / "fixtures"
SHA = "0" * 64
AIRPORTS = {code: {"STATE": state} for code, state in {
    "SFO": "CA", "EWR": "NJ", "LAX": "CA", "SEA": "WA", "SBA": "CA", "LAS": "NV", "ORD": "IL",
    "ELP": "TX", "ATL": "GA",
}.items()}
ZONES = timezones.build_zone_table(AIRPORTS)


@pytest.fixture(scope="module")
def legs():
    with (FIXTURES / "edge_rows.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    return {r.pop("case"): normalize_row(r, i, SHA, ZONES) for i, r in enumerate(rows, start=1)}


def test_parse_hhmm():
    assert parse_hhmm("2400") == (1440, ["HHMM_2400"])
    assert parse_hhmm("0005") == (5, [])
    assert parse_hhmm("") == (None, [])
    assert parse_hhmm("2460")[1] == ["HHMM_INVALID"]


def test_flight_id_uses_checksum_and_row(legs):
    leg = legs["overnight_redeye"]
    assert leg["flight_id"] == "000000000000:1"
    assert leg["source_row_id"] == "flights.csv:1"
    assert leg["source_values"]["SCHEDULED_DEPARTURE"] == "2255"


def test_overnight_arrival_resolves_to_next_day(legs):
    leg = legs["overnight_redeye"]
    # 22:55 PDT = 05:55Z; 07:25 EDT next day = 11:25Z (330 min scheduled elapsed)
    assert leg["scheduled_gate_departure_utc"] == "2015-06-11T05:55:00Z"
    assert leg["scheduled_gate_arrival_utc"] == "2015-06-11T11:25:00Z"
    assert "OVERNIGHT_SCHEDULED" in leg["quality_flags"]


def test_2400_actual_departure(legs):
    leg = legs["departure_2400"]
    assert leg["actual_gate_departure_utc"] == "2015-06-11T07:00:00Z"  # 24:00 PDT on 10 Jun
    assert "DEPARTURE_TIME:HHMM_2400" in leg["quality_flags"]
    assert leg["wheels_off_utc"] == "2015-06-11T07:10:00Z"


def test_cancelled_keeps_schedule_and_no_actuals(legs):
    leg = legs["cancelled"]
    assert leg["cancelled"] and "CANCELLED" in leg["quality_flags"]
    assert leg["scheduled_gate_arrival_utc"] == "2015-06-10T21:05:00Z"
    assert leg["actual_gate_departure_utc"] is None


def test_diverted_has_no_arrival_actuals(legs):
    leg = legs["diverted"]
    assert leg["diverted"] and leg["actual_gate_arrival_utc"] is None and leg["wheels_on_utc"] is None


def test_numeric_ids_flagged_not_guessed(legs):
    leg = legs["numeric_ids"]
    assert leg["origin"] is None and not leg["resolved"]
    assert "NUMERIC_AIRPORT_ID_UNRESOLVED" in leg["quality_flags"]


def test_numeric_ids_resolved_with_lookup():
    with (FIXTURES / "edge_rows.csv").open(newline="") as f:
        row = next(r for r in csv.DictReader(f) if r["case"] == "numeric_ids")
    row.pop("case")
    leg = normalize_row(row, 5, SHA, ZONES, {"14771": "SFO", "12478": "ATL"})
    assert (leg["origin"], leg["destination"]) == ("SFO", "ATL") and leg["resolved"]
    assert "AIRPORT_ID_FROM_BTS_LOOKUP" in leg["quality_flags"]


def test_dst_edges_flagged(legs):
    assert "SCHEDULED_DEPARTURE:LOCAL_TIME_NONEXISTENT_DST" in legs["dst_nonexistent"]["quality_flags"]
    assert "SCHEDULED_DEPARTURE:LOCAL_TIME_AMBIGUOUS_DST" in legs["dst_ambiguous"]["quality_flags"]


def test_inconsistent_elapsed_flagged(legs):
    assert "SCHEDULED_ARRIVAL:INCONSISTENT_WITH_DURATIONS" in legs["inconsistent_elapsed"]["quality_flags"]


def test_missing_tail(legs):
    assert legs["missing_tail"]["tail_number"] is None
    assert "TAIL_NUMBER_MISSING" in legs["missing_tail"]["quality_flags"]


def test_qualifying_row_matches_real_trace(legs):
    leg = legs["qualifying"]
    assert qualifies(leg)
    assert leg["scheduled_gate_departure_utc"] == "2015-06-10T23:30:00Z"  # 18:30 CDT
    assert leg["scheduled_gate_arrival_utc"] == "2015-06-11T04:15:00Z"    # 21:15 PDT


def test_timezone_override():
    assert ZONES["ELP"] == "America/Denver"


def test_every_airport_has_a_zone():
    states = {"AK", "AL", "AR", "AS", "AZ", "CA", "CO", "CT", "DE", "FL", "GA", "GU", "HI", "IA", "ID", "IL",
              "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH",
              "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA", "PR", "RI", "SC", "SD", "TN", "TX", "UT", "VA",
              "VI", "VT", "WA", "WI", "WV", "WY"}
    assert states <= set(timezones.STATE_DEFAULT_ZONE)
