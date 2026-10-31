"""Declared operational assumptions for the scenario.

The 2015 BTS rows carry no aircraft type, fuel, gate, crew, equipment or passenger
connection data. Every value below is therefore a simulation rule (SYNTHETIC) unless
it is read straight from the source row (HISTORICAL). Per-flight random draws use a
string seed of scenario seed, flight_id and field name, so a value never depends on
the order flights are processed in.

Assumptions use the Member 1 contract shape: a dict keyed by name of
{value, source_type, unit, explanation}. Wake categories use the contract's MEDIUM and
HEAVY strings so they match the wake separation pairs in RulesConfig.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
EQUIPMENT_ID = "EQ-PUSHBACK-TUGS"

PARAMETERS = {
    "wake_category_rule": {
        "value": "HEAVY if scheduled stage length >= 2000 statute miles else MEDIUM",
        "threshold_mi": 2000, "unit": "statute miles", "source_type": "SYNTHETIC",
        "basis": "Proxy only. Aircraft type is not in the source and a tail number alone does not establish it.",
    },
    "taxi_in_min": {"value": 8, "unit": "minutes", "source_type": "SYNTHETIC",
                    "basis": "Unimpeded taxi-in used to estimate scheduled wheels-on from scheduled gate arrival"},
    "taxi_out_min": {"value": 12, "unit": "minutes", "source_type": "SYNTHETIC",
                     "basis": "Unimpeded taxi-out used to estimate scheduled wheels-off from scheduled gate departure"},
    "holding_fuel_available_min": {"value": [10, 60], "unit": "minutes", "source_type": "SYNTHETIC",
                                   "basis": "Uniform seeded draw per arrival: airborne holding available beyond the estimated "
                                            "scheduled landing before final reserve. Not a fuel load."},
    "gate_dwell_cap_min": {"value": 240, "unit": "minutes", "source_type": "SYNTHETIC",
                           "basis": "Aircraft that arrived earlier than this before scenario start with no departure "
                                    "in the scenario are assumed towed off-gate"},
    "gate_assignment_buffer_min": {"value": 30, "unit": "minutes", "source_type": "SYNTHETIC",
                                   "basis": "Added after each scheduled gate stay before another aircraft is given the "
                                            "same gate, so moderate delays do not create gate conflicts"},
    "gate_headroom_factor": {"value": 1.15, "unit": "ratio", "source_type": "SYNTHETIC",
                             "basis": "Simulated gate count = ceil(peak buffered gate demand x factor)"},
    "departure_gate_lead_min": {"value": 20, "unit": "minutes", "source_type": "SYNTHETIC",
                                "basis": "Member 1 engine starts a departure's gate reservation 20 min before scheduled "
                                         "departure; initial occupancy ends there"},
    "crew_duty_remaining_min": {"value": [90, 360], "unit": "minutes", "source_type": "SYNTHETIC",
                                "basis": "Uniform seeded draw per aircraft rotation crew: remaining work limit"},
    "crew_availability_margin_min": {"value": 60, "unit": "minutes", "source_type": "SYNTHETIC",
                                     "basis": "Crew available from scenario start until scenario end plus this margin"},
    "pushback_tugs": {"value": 12, "unit": "count", "source_type": "SYNTHETIC",
                      "basis": "Single shared ground equipment resource. The engine reserves it for every "
                               "movement's taxi interval, arrivals included"},
    "taxi_speed_mps": {"value": 10, "unit": "metres per second", "source_type": "SYNTHETIC",
                       "basis": "Minimum traversal time of a taxi segment = length / speed, at least 30 s"},
    "taxi_piece_max_m": {"value": 400, "unit": "metres", "source_type": "SYNTHETIC",
                         "basis": "Taxi segments are split into equal pieces no longer than this"},
    "connections": {
        "value": {"share_of_eligible_pairs": 0.15, "min_connect_min": 40, "max_gap_min": 120,
                  "passengers": [5, 40]},
        "unit": "mixed", "source_type": "SYNTHETIC",
        "basis": "Passenger connections are not in the source. Same-airline arrival to departure pairs "
                 "with a scheduled gap between 40 and 120 minutes are eligible; a seeded share is declared.",
    },
}


def _rng(seed: int, *parts: str) -> random.Random:
    return random.Random(":".join([str(seed), *parts]))


def assumption(value, source_type: str, unit: str, explanation: str) -> dict:
    return {"value": value, "source_type": source_type, "unit": unit, "explanation": explanation}


def parse(s: str) -> datetime:
    return datetime.fromisoformat(s)  # ISO 8601 with Z; C parser, much faster than strptime


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)


def wake_category(distance_mi: float | None) -> str:
    """MEDIUM unless the stage length proxy says HEAVY. Missing distance defaults to MEDIUM."""
    if distance_mi is not None and distance_mi >= PARAMETERS["wake_category_rule"]["threshold_mi"]:
        return "HEAVY"
    return "MEDIUM"


def crew_key(leg: dict) -> str:
    """One crew per aircraft rotation (airline + tail); flights without a tail get their own crew."""
    return f"{leg['airline']}-{leg['tail_number']}" if leg.get("tail_number") else leg["flight_id"]


def crew_duty_minutes(key: str, seed: int) -> int:
    lo, hi = PARAMETERS["crew_duty_remaining_min"]["value"]
    return _rng(seed, key, "crew").randint(lo, hi)


def flight_assumptions(leg: dict, seed: int, gate_id: str | None) -> dict[str, dict]:
    p = PARAMETERS
    arrival = leg["operation"] == "ARRIVAL"
    out = {
        "wake_category": assumption(wake_category(leg.get("distance_mi")), "SYNTHETIC", "category",
                                    p["wake_category_rule"]["basis"]),
        "stage_length": assumption(leg.get("distance_mi"), "HISTORICAL", "statute miles", "DISTANCE column"),
        "crew_id": assumption(f"CREW-{crew_key(leg)}", "SYNTHETIC", "resource_id",
                              "One crew per aircraft rotation (airline and tail)"),
        "equipment_id": assumption(EQUIPMENT_ID, "SYNTHETIC", "resource_id", "Shared pushback tug pool"),
        "source_scheduled_local_hhmm": assumption(
            {"scheduled_departure": leg["source_values"]["SCHEDULED_DEPARTURE"],
             "origin_timezone": leg["origin_timezone"],
             "scheduled_arrival": leg["source_values"]["SCHEDULED_ARRIVAL"],
             "destination_timezone": leg["destination_timezone"]},
            "HISTORICAL", "HHMM local", "Original clock values from the source row"),
        "source_quality_flags": assumption(leg["quality_flags"], "HISTORICAL", "flags",
                                           "Normalization flags; see quality_report.json"),
    }
    if gate_id:
        out["gate_id"] = assumption(gate_id, "SYNTHETIC", "resource_id",
                                    "Simulated gate held for the aircraft's whole scheduled stay")
    if arrival:
        landing = parse(leg["scheduled_gate_arrival_utc"]) - minutes(p["taxi_in_min"]["value"])
        lo, hi = p["holding_fuel_available_min"]["value"]
        hold = _rng(seed, leg["flight_id"], "holding").randint(lo, hi)
        out.update({
            "estimated_scheduled_wheels_on_utc": assumption(
                iso(landing), "SYNTHETIC", "UTC", "scheduled_gate_arrival_utc minus taxi_in_min"),
            "holding_fuel_available": assumption(hold, "SYNTHETIC", "minutes", p["holding_fuel_available_min"]["basis"]),
            "latest_safe_arrival_utc": assumption(
                iso(landing + minutes(hold)), "SYNTHETIC", "UTC",
                "estimated_scheduled_wheels_on_utc plus holding_fuel_available; compared with runway time"),
        })
    else:
        off = parse(leg["scheduled_gate_departure_utc"]) + minutes(p["taxi_out_min"]["value"])
        out["estimated_scheduled_wheels_off_utc"] = assumption(
            iso(off), "SYNTHETIC", "UTC", "scheduled_gate_departure_utc plus taxi_out_min")
    return out


def synthetic_connections(arrivals: list[dict], departures: list[dict], seed: int) -> list[dict]:
    cfg = PARAMETERS["connections"]["value"]
    out = []
    for a in arrivals:
        a_in = parse(a["scheduled_gate_arrival_utc"])
        for d in departures:
            if d["airline"] != a["airline"]:
                continue
            gap = (parse(d["scheduled_gate_departure_utc"]) - a_in).total_seconds() / 60
            if not cfg["min_connect_min"] <= gap <= cfg["max_gap_min"]:
                continue
            rng = _rng(seed, a["flight_id"], d["flight_id"], "connection")
            if rng.random() < cfg["share_of_eligible_pairs"]:
                out.append({
                    "connection_id": f"CX-{len(out) + 1:03d}",
                    "inbound_flight_id": a["flight_id"], "outbound_flight_id": d["flight_id"],
                    "passengers": rng.randint(*cfg["passengers"]),
                    "min_connect_min": cfg["min_connect_min"],
                    "scheduled_gap_min": round(gap),
                    "source_type": "SYNTHETIC",
                })
    return out
