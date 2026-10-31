"""Declared operational assumptions for the scenario.

The 2015 BTS rows carry no aircraft type, fuel, gate, crew, equipment or passenger
connection data. Every value below is therefore a simulation rule (SYNTHETIC) unless
it is read straight from the source row (HISTORICAL). Per-flight random draws use a
string seed of scenario seed, flight_id and field name, so a value never depends on
the order flights are processed in.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

UTC = timezone.utc

PARAMETERS = {
    "wake_category_rule": {
        "value": "HEAVY if scheduled stage length >= 2000 statute miles else LARGE",
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
    "min_turnaround_min": {"value": {"LARGE": 45, "HEAVY": 75}, "unit": "minutes", "source_type": "SYNTHETIC",
                           "basis": "Minimum gate time from on-block to departure readiness"},
    "gate_dwell_cap_min": {"value": 240, "unit": "minutes", "source_type": "SYNTHETIC",
                           "basis": "Aircraft that arrived earlier than this before scenario start with no departure "
                                    "in the scenario are assumed towed off-gate"},
    "gate_headroom_factor": {"value": 1.15, "unit": "ratio", "source_type": "SYNTHETIC",
                             "basis": "Simulated gate count = ceil(peak scheduled gate demand x factor)"},
    "crew_duty_remaining_min": {"value": [90, 360], "unit": "minutes", "source_type": "SYNTHETIC",
                                "basis": "Uniform seeded draw per aircraft rotation crew at scenario start"},
    "pushback_tugs": {"value": 6, "unit": "count", "source_type": "SYNTHETIC",
                      "basis": "Single shared ground equipment resource"},
    "pushback_tug_use_min": {"value": 10, "unit": "minutes", "source_type": "SYNTHETIC",
                             "basis": "Tug occupied from scheduled off-block per departure"},
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


def _a(name: str, value, unit: str | None, source_type: str, basis: str) -> dict:
    return {"name": name, "value": value, "unit": unit, "source_type": source_type, "basis": basis}


def parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def wake_category(distance_mi: float | None) -> str | None:
    if distance_mi is None:
        return None
    return "HEAVY" if distance_mi >= PARAMETERS["wake_category_rule"]["threshold_mi"] else "LARGE"


def flight_assumptions(leg: dict, seed: int) -> list[dict]:
    p = PARAMETERS
    out = []
    cat = wake_category(leg.get("distance_mi"))
    out.append(_a("wake_category", cat, None, "SYNTHETIC", p["wake_category_rule"]["basis"]))
    out.append(_a("stage_length", leg.get("distance_mi"), "statute miles", "HISTORICAL", "DISTANCE column"))
    if cat:
        out.append(_a("min_turnaround", p["min_turnaround_min"]["value"][cat], "minutes", "SYNTHETIC",
                      p["min_turnaround_min"]["basis"]))
    if leg["operation"] == "ARRIVAL":
        landing = parse(leg["scheduled_gate_arrival_utc"]) - timedelta(minutes=p["taxi_in_min"]["value"])
        lo, hi = p["holding_fuel_available_min"]["value"]
        hold = _rng(seed, leg["flight_id"], "holding").randint(lo, hi)
        out += [
            _a("estimated_scheduled_wheels_on_utc", iso(landing), "UTC", "SYNTHETIC",
               "scheduled_gate_arrival_utc minus taxi_in_min"),
            _a("holding_fuel_available", hold, "minutes", "SYNTHETIC", p["holding_fuel_available_min"]["basis"]),
            _a("latest_safe_landing_utc", iso(landing + timedelta(minutes=hold)), "UTC", "SYNTHETIC",
               "estimated_scheduled_wheels_on_utc plus holding_fuel_available"),
        ]
    else:
        off = parse(leg["scheduled_gate_departure_utc"]) + timedelta(minutes=p["taxi_out_min"]["value"])
        out.append(_a("estimated_scheduled_wheels_off_utc", iso(off), "UTC", "SYNTHETIC",
                      "scheduled_gate_departure_utc plus taxi_out_min"))
    return out


def crew_for(leg: dict, seed: int) -> dict:
    """One crew per aircraft rotation (airline + tail); flights without a tail get their own crew."""
    key = f"{leg['airline']}-{leg['tail_number']}" if leg.get("tail_number") else leg["flight_id"]
    lo, hi = PARAMETERS["crew_duty_remaining_min"]["value"]
    return {
        "crew_id": f"CREW-{key}",
        "duty_remaining_at_start_min": _rng(seed, key, "crew").randint(lo, hi),
        "unit": "minutes", "source_type": "SYNTHETIC",
        "basis": PARAMETERS["crew_duty_remaining_min"]["basis"],
    }


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
