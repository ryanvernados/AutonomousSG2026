"""Build contract v1 Scenario JSON from the processed traffic slice and the SFO layout.

    python -m backend.data.scenario [--slice-dir data/processed/sfo_2015-12-21]

Reads only files under data/processed/, so it runs without the raw Kaggle data.
Every scenario is validated with Member 1's backend.contracts.Scenario before it is
written. Outputs:
    data/processed/scenarios/<scenario_id>.json   (point SCENARIO_DIR here)
    data/processed/<slice>/expected_facts.json
    data/layout/sfo_layout.json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

from backend.airport import sfo_layout
from backend.contracts import RulesConfig, Scenario

from . import assumptions as A
from . import config

SEED = 20151221
SCENARIO_DIR = config.PROCESSED_DIR / "scenarios"
PROJECT_ID = "00000000-0000-0000-0000-000000000010"  # Member 1 demo project; override on import

FLIGHT_FIELDS = ("flight_id", "source_row_id", "airline", "flight_number", "tail_number", "origin",
                 "destination", "scheduled_gate_departure_utc", "scheduled_gate_arrival_utc",
                 "actual_gate_departure_utc", "actual_gate_arrival_utc", "wheels_on_utc", "wheels_off_utc")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rotations(legs: list[dict]) -> dict[str, dict]:
    """Same-tail scheduled sequence at SFO: arrival -> next departure (HISTORICAL tail order)."""
    by_tail = collections.defaultdict(list)
    for leg in legs:
        if leg.get("tail_number"):
            by_tail[(leg["airline"], leg["tail_number"])].append(leg)
    link = collections.defaultdict(dict)
    for seq in by_tail.values():
        seq.sort(key=lambda leg: (leg["sfo_scheduled_movement_utc"], leg["flight_id"]))
        for prev, nxt in zip(seq, seq[1:]):
            if prev["operation"] == "ARRIVAL" and nxt["operation"] == "DEPARTURE":
                link[prev["flight_id"]]["next_departure_flight_id"] = nxt["flight_id"]
                link[nxt["flight_id"]]["previous_arrival_flight_id"] = prev["flight_id"]
    return link


def gate_stays(slice_legs, context_legs, link, t0, t_end) -> list[dict]:
    """One stay per aircraft on a gate during the horizon, with the slice flights that use it.

    Pre-start conditions come from what happened (context arrivals that operated); in-horizon
    demand is the schedule, including legs later cancelled.
    """
    by_id = {leg["flight_id"]: leg for leg in [*slice_legs, *context_legs]}
    in_slice = {leg["flight_id"] for leg in slice_legs}
    cap = A.minutes(A.PARAMETERS["gate_dwell_cap_min"]["value"])
    stays, covered = [], set()

    def nxt_of(leg):
        return by_id.get(link.get(leg["flight_id"], {}).get("next_departure_flight_id", ""))

    for leg in context_legs:  # aircraft already on gate at t0
        if leg["operation"] != "ARRIVAL" or leg["cancelled"] or A.parse(leg["sfo_scheduled_movement_utc"]) >= t0:
            continue
        arr, nxt = A.parse(leg["sfo_scheduled_movement_utc"]), nxt_of(leg)
        dep = A.parse(nxt["sfo_scheduled_movement_utc"]) if nxt else None
        if (dep is not None and dep < t0) or (dep is None and t0 - arr > cap):
            continue
        uses = [nxt["flight_id"]] if nxt and nxt["flight_id"] in in_slice else []
        stays.append({"start": t0, "end": dep if uses else t_end, "flights": uses, "occupant": leg["flight_id"],
                      "category": A.wake_category((nxt or leg).get("distance_mi")), "tail": leg["tail_number"],
                      "source_type": "HISTORICAL",
                      "basis": "Arrived before scenario start; same tail's next SFO departure at or after start"})
        covered.update(uses)

    for leg in slice_legs:  # slice arrivals keep their gate until the rotation departure
        if leg["operation"] != "ARRIVAL":
            continue
        nxt = nxt_of(leg)
        uses = [leg["flight_id"]] + ([nxt["flight_id"]] if nxt and nxt["flight_id"] in in_slice else [])
        start = A.parse(leg["sfo_scheduled_movement_utc"])
        end = A.parse(nxt["sfo_scheduled_movement_utc"]) if len(uses) == 2 else t_end
        stays.append({"start": start, "end": max(start, end), "flights": uses, "occupant": None,
                      "category": A.wake_category(leg.get("distance_mi")), "tail": leg.get("tail_number")})
        covered.update(uses)

    for leg in slice_legs:  # departures with no covering inbound: aircraft assumed on gate at t0
        if leg["operation"] == "DEPARTURE" and leg["flight_id"] not in covered:
            stays.append({"start": t0, "end": A.parse(leg["sfo_scheduled_movement_utc"]), "flights": [leg["flight_id"]],
                          "occupant": leg["flight_id"], "category": A.wake_category(leg.get("distance_mi")),
                          "tail": leg.get("tail_number"), "source_type": "SYNTHETIC",
                          "basis": "Departure with no inbound SFO leg in the source window; aircraft assumed on gate"})
    return stays


def peak(intervals) -> int:
    events = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals])
    best = cur = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best


def assign_gates(stays: list[dict], gates: list[dict], buffer) -> list[str]:
    """Interval colouring: each stay gets a compatible gate free since the previous stay plus buffer.

    MEDIUM stays prefer MEDIUM-only gates so HEAVY-capable gates stay available.
    Returns the IDs of slice flights that could not be given a gate.
    """
    free_at = {g["gate_id"]: None for g in gates}
    heavy = {g["gate_id"] for g in gates if g["max_wake_category"] == "HEAVY"}
    order = sorted(free_at, key=lambda gid: (gid in heavy, gid))
    unassigned = []
    for stay in sorted(stays, key=lambda s: (s["start"], s["end"], s["flights"], s["occupant"] or "")):
        options = [g for g in order if stay["category"] == "MEDIUM" or g in heavy]
        gate = next((g for g in options if free_at[g] is None or free_at[g] <= stay["start"]), None)
        stay["gate_id"] = gate
        if gate is None:
            unassigned += stay["flights"]
        else:
            free_at[gate] = stay["end"] + buffer
    return unassigned


def occupancy_reservations(stays: list[dict], t0) -> list[dict]:
    """Initial gate occupancy as contract Reservations.

    The engine starts a departure's own gate reservation 20 minutes before scheduled
    departure, so an occupant's reservation ends there and the two intervals touch.
    The flight_id is a distinct OCCUPANT id so planning never drops it as a replaced flight.
    """
    lead = A.minutes(A.PARAMETERS["departure_gate_lead_min"]["value"])
    out = []
    for s in stays:
        if s["occupant"] is None or s["gate_id"] is None:
            continue
        end = s["end"] - lead if s["flights"] else s["end"]
        if end <= t0:
            continue
        out.append({"reservation_id": f"initial-occupancy:{s['occupant']}", "resource_id": s["gate_id"],
                    "flight_id": f"OCCUPANT:{s['occupant']}", "kind": "GATE", "starts_at_utc": A.iso(t0),
                    "ends_at_utc": A.iso(end), "quantity": 1, "wake_category": s["category"]})
    return out


def occupancy_provenance(stays: list[dict]) -> list[dict]:
    return [{"reservation_id": f"initial-occupancy:{s['occupant']}", "tail_number": s["tail"],
             "gate_id": s["gate_id"], "release_flight_ids": s["flights"], "source_type": s["source_type"],
             "gate_assignment_source_type": "SYNTHETIC", "basis": s["basis"]}
            for s in stays if s["occupant"] is not None]


def build(slice_dir: Path) -> dict:
    sl = load(slice_dir / "traffic_slice.json")
    ctx = load(slice_dir / "context_legs.json")
    dev = load(slice_dir / "dev_fixture.json")
    manifest = load(config.PROCESSED_DIR / "source_manifest.json")
    quality = load(slice_dir / "quality_report.json")
    w = sl["window"]
    t0, t_end = A.parse(w["buffer_start_utc"]), A.parse(w["buffer_end_utc"])
    p = A.PARAMETERS

    legs, context = sl["flights"], ctx["flights"]
    link = rotations([*legs, *context])
    stays = gate_stays(legs, context, link, t0, t_end)
    buffer = A.minutes(p["gate_assignment_buffer_min"]["value"])
    factor = p["gate_headroom_factor"]["value"]
    peak_all = peak([(s["start"], s["end"] + buffer) for s in stays])
    peak_heavy = peak([(s["start"], s["end"] + buffer) for s in stays if s["category"] == "HEAVY"])
    gate_count, heavy_count = math.ceil(peak_all * factor), math.ceil(peak_heavy * factor)

    layout = sfo_layout.build_layout(gate_count, heavy_count)
    layout["connected"] = sfo_layout.is_connected(layout)
    layout_path = config.REPO_ROOT / "data" / "layout" / "sfo_layout.json"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")

    unassigned = assign_gates(stays, layout["gates"], buffer)
    gate_of = {fid: s["gate_id"] for s in stays for fid in s["flights"]}
    occupancy = occupancy_reservations(stays, t0)

    crews = {}
    for leg in legs:
        key = A.crew_key(leg)
        crews.setdefault(key, {
            "resource_id": f"CREW-{key}", "kind": "CREW", "capacity": 1,
            "available_from_utc": A.iso(t0),
            "available_until_utc": A.iso(t_end + A.minutes(p["crew_availability_margin_min"]["value"])),
            "max_work_seconds": A.crew_duty_minutes(key, SEED) * 60,
        })
    resources = [
        *sfo_layout.to_resources(layout, taxi_speed_mps=p["taxi_speed_mps"]["value"],
                                 max_piece_m=p["taxi_piece_max_m"]["value"]),
        *sorted(crews.values(), key=lambda c: c["resource_id"]),
        {"resource_id": A.EQUIPMENT_ID, "kind": "EQUIPMENT", "capacity": p["pushback_tugs"]["value"]},
    ]
    infrastructure = {
        "resources": resources,
        "source_type": "SOURCED",
        "effective_date": layout["source"]["effective_from"],
        "coverage_limits": [
            "Runways, runway crossings and taxiway names are SOURCED from FAA AL-375 (2026); taxiway topology, "
            "segment lengths, gates, crews and equipment are SYNTHETIC.",
            *layout["coverage_limits"],
            *quality["coverage_limits"],
        ],
    }

    flights = []
    for leg in legs:
        f = {k: leg[k] for k in FLIGHT_FIELDS}
        f.update({"state": "SCHEDULED", "plan": None,
                  "assumptions": A.flight_assumptions(leg, SEED, gate_of.get(leg["flight_id"]))})
        rot = link.get(leg["flight_id"], {})
        for key, value in rot.items():
            f["assumptions"][key] = A.assumption(value, "HISTORICAL", "flight_id",
                                                 "Same airline and tail, adjacent SFO movement in the source")
        flights.append(f)

    operating = [leg for leg in legs if not leg["cancelled"]]
    member2 = {
        "core_window": {"start_utc": w["start_utc"], "end_utc": w["end_utc"]},
        "init_buffer_minutes": config.INIT_BUFFER_MINUTES,
        "recovery_buffer_minutes": config.RECOVERY_BUFFER_MINUTES,
        "window_selection": {"day": w["day_selection"], "window": w["window_selection"]},
        "assumption_parameters": p,
        "gate_sizing": {"peak_buffered_gate_demand": peak_all, "peak_heavy_gate_demand": peak_heavy,
                        "simulated_gates": gate_count, "heavy_capable_gates": heavy_count,
                        "flights_without_gate": unassigned, "source_type": "SYNTHETIC"},
        "initial_occupancy_provenance": occupancy_provenance(stays),
        "synthetic_connections": A.synthetic_connections(
            [leg for leg in operating if leg["operation"] == "ARRIVAL"],
            [leg for leg in operating if leg["operation"] == "DEPARTURE"], SEED),
        "layout": {"file": "data/layout/sfo_layout.json", "sha256": sha256_file(layout_path),
                   "runway_relationships": layout["runway_relationships"],
                   "node_positions": layout["taxiway_nodes"], "coordinate_frame": layout["coordinate_frame"]},
        "historical_outcomes": {
            "use": "Replay and calibration only. Not an input to baseline or agent decisions.",
            "source_type": "HISTORICAL",
            "flights": {leg["flight_id"]: {"cancelled": leg["cancelled"], "cancellation_reason": leg["cancellation_reason"],
                                         "diverted": leg["diverted"], "durations_min": leg["durations_min"],
                                         "attributed_delay_min": leg["attributed_delay_min"],
                                         "segment": leg["segment"], "qualifying_source_row": leg["qualifying"]}
                        for leg in legs},
        },
        "data_quality": quality["selected_slice"],
        "rules_config_note": "rules_config holds Member 1 default simulation rules unchanged",
    }
    pdf = config.REPO_ROOT / layout["source"]["local_copy"]
    source_manifest = {
        **manifest,
        "layout_source": {**layout["source"], "local_copy_sha256": sha256_file(pdf)},
        "derived_files": {name: sha256_file(slice_dir / name) for name in (
            "traffic_slice.json", "context_legs.json", "dev_fixture.json", "qualifying_row.json")},
        "member2": member2,
    }

    def scenario(scenario_id: str, chosen: list[dict]) -> Scenario:
        return Scenario.model_validate({
            "schema_version": "1", "scenario_id": scenario_id, "project_id": PROJECT_ID,
            "airport_iata": config.AIRPORT_IATA, "timezone": config.AIRPORT_TZ, "seed": SEED,
            "start_utc": w["buffer_start_utc"], "end_utc": w["buffer_end_utc"],
            "source_manifest": source_manifest, "rules_config": RulesConfig().model_dump(mode="json"),
            "flights": chosen, "infrastructure": infrastructure, "initial_occupancy": occupancy,
        })

    dev_ids = {leg["flight_id"] for leg in dev["flights"]}
    slug = f"sfo-{w['local_date']}-{w['window_selection']['start_local_hour']:02d}00"
    full = scenario(f"{slug}-full", flights)
    dev_s = scenario(f"{slug}-dev", [f for f in flights if f["flight_id"] in dev_ids])
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    for s in (full, dev_s):
        (SCENARIO_DIR / f"{s.scenario_id}.json").write_text(
            json.dumps(s.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    facts = {
        "scenario_ids": [full.scenario_id, dev_s.scenario_id],
        "scenario_files": [f"data/processed/scenarios/{s.scenario_id}.json" for s in (full, dev_s)],
        "local_date": w["local_date"], "window": [w["start_utc"], w["end_utc"]],
        "horizon": [w["buffer_start_utc"], w["buffer_end_utc"]],
        "flights_full": len(full.flights), "flights_dev": len(dev_s.flights),
        "arrivals_full": sum(f.destination == config.AIRPORT_IATA for f in full.flights),
        "departures_full": sum(f.origin == config.AIRPORT_IATA for f in full.flights),
        "historically_cancelled_full": sum(leg["cancelled"] for leg in legs),
        "qualifying_rows_full": sum(leg["qualifying"] for leg in legs),
        "traced_qualifying_flight_id": load(slice_dir / "qualifying_row.json")["normalized"]["flight_id"],
        "initial_occupancy": len(occupancy),
        "flights_without_gate": len(unassigned),
        "simulated_gates": gate_count, "heavy_capable_gates": heavy_count,
        "synthetic_connections": len(member2["synthetic_connections"]), "crews": len(crews),
        "resources": len(resources),
        "taxiway_graph_connected": layout["connected"],
        "seed": SEED,
    }
    (slice_dir / "expected_facts.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")
    return facts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build contract v1 Scenario JSON from processed data")
    parser.add_argument("--slice-dir", default=None)
    args = parser.parse_args(argv)
    slice_dir = Path(args.slice_dir) if args.slice_dir else max(config.PROCESSED_DIR.glob("sfo_*"))
    print(json.dumps(build(slice_dir), indent=2))


if __name__ == "__main__":
    main()
