"""Build Scenario JSON from the processed traffic slice and the SFO layout.

    python -m backend.data.scenario [--slice-dir data/processed/sfo_2015-12-21]

Reads only files under data/processed/, so it runs without the raw Kaggle data.
Writes scenario_full.json, scenario_dev.json and expected_facts.json next to the
slice, and data/layout/sfo_layout.json.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

from backend.airport import sfo_layout

from . import assumptions as A
from . import config

SCHEMA_VERSION = "contract-v1-draft"
SEED = 20151221

FLIGHT_FIELDS = ("flight_id", "source_row_id", "airline", "flight_number", "tail_number", "origin",
                 "destination", "scheduled_gate_departure_utc", "scheduled_gate_arrival_utc")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rotations(legs: list[dict]) -> dict[str, dict]:
    """Same-tail sequence at SFO: arrival -> next departure (HISTORICAL tail order)."""
    by_tail = collections.defaultdict(list)
    for l in legs:
        if l.get("tail_number") and not l["cancelled"]:
            by_tail[(l["airline"], l["tail_number"])].append(l)
    link = collections.defaultdict(dict)
    for seq in by_tail.values():
        seq.sort(key=lambda l: (l["sfo_scheduled_movement_utc"], l["flight_id"]))
        for prev, nxt in zip(seq, seq[1:]):
            if prev["operation"] == "ARRIVAL" and nxt["operation"] == "DEPARTURE":
                link[prev["flight_id"]]["next_departure_flight_id"] = nxt["flight_id"]
                link[nxt["flight_id"]]["previous_arrival_flight_id"] = prev["flight_id"]
    return link


def gate_demand(slice_legs, context_legs, link, t0, t_end):
    """Scheduled gate intervals: aircraft on gate at t0 plus slice arrivals."""
    by_id = {l["flight_id"]: l for l in [*slice_legs, *context_legs]}
    cap = A.PARAMETERS["gate_dwell_cap_min"]["value"] * 60
    occupants, intervals = [], []

    def category(leg):
        return A.wake_category(leg.get("distance_mi")) or "LARGE"

    # Aircraft that arrived before t0 (context) and are still on gate.
    for l in context_legs:
        if l["operation"] != "ARRIVAL" or l["cancelled"]:
            continue
        arr = A.parse(l["sfo_scheduled_movement_utc"])
        if arr >= t0:
            continue
        nxt = by_id.get(link.get(l["flight_id"], {}).get("next_departure_flight_id", ""))
        dep = A.parse(nxt["sfo_scheduled_movement_utc"]) if nxt else None
        if dep is not None and dep < t0:
            continue
        if dep is None and (t0 - arr).total_seconds() > cap:
            continue
        cat = category(nxt) if nxt and "distance_mi" in nxt else "LARGE"
        occupants.append({"tail_number": l["tail_number"], "airline": l["airline"],
                          "arrived_flight_id": l["flight_id"], "arrived_utc": A.iso(arr),
                          "release_flight_id": nxt["flight_id"] if nxt else None,
                          "wake_category": cat, "source_type": "HISTORICAL",
                          "basis": "Scheduled arrival before scenario start with the same tail's next "
                                   "SFO departure at or after start"})
        intervals.append((t0, dep or t_end, cat))

    # Slice departures with no inbound record: assume the aircraft is already on gate.
    for l in slice_legs:
        if l["operation"] != "DEPARTURE" or l["cancelled"]:
            continue
        prev = link.get(l["flight_id"], {}).get("previous_arrival_flight_id")
        if prev is None:
            occupants.append({"tail_number": l.get("tail_number"), "airline": l["airline"],
                              "arrived_flight_id": None, "arrived_utc": None,
                              "release_flight_id": l["flight_id"], "wake_category": category(l),
                              "source_type": "SYNTHETIC",
                              "basis": "Departure with no inbound SFO leg in the source window; "
                                       "aircraft assumed on gate at scenario start"})
            intervals.append((t0, A.parse(l["sfo_scheduled_movement_utc"]), category(l)))

    # Slice arrivals occupy a gate until their rotation departure or the scenario end.
    for l in slice_legs:
        if l["operation"] != "ARRIVAL" or l["cancelled"]:
            continue
        nxt = by_id.get(link.get(l["flight_id"], {}).get("next_departure_flight_id", ""))
        start = A.parse(l["sfo_scheduled_movement_utc"])
        end = A.parse(nxt["sfo_scheduled_movement_utc"]) if nxt else t_end
        intervals.append((start, max(end, start), category(l)))
    return occupants, intervals


def peak(intervals, only=None) -> int:
    events = []
    for s, e, cat in intervals:
        if only is None or cat == only:
            events += [(s, 1), (e, -1)]
    events.sort(key=lambda x: (x[0], x[1]))
    best = cur = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best


def assign_occupants(occupants: list[dict], gates: list[dict]) -> list[dict]:
    free = [g for g in gates]
    out = []
    for occ in sorted(occupants, key=lambda o: (o["wake_category"] != "HEAVY", o["arrived_utc"] or "",
                                                o["release_flight_id"] or "")):
        gate = next((g for g in free if occ["wake_category"] == "LARGE" or g["max_wake_category"] == "HEAVY"), None)
        if gate is None:
            out.append({**occ, "gate_id": None, "issue": "NO_COMPATIBLE_SIMULATED_GATE"})
            continue
        free.remove(gate)
        out.append({**occ, "gate_id": gate["gate_id"], "gate_assignment_source_type": "SYNTHETIC"})
    return out


def to_flight(leg: dict, link: dict, seed: int) -> dict:
    flight = {k: leg[k] for k in FLIGHT_FIELDS}
    rot = link.get(leg["flight_id"], {})
    assumptions = A.flight_assumptions(leg, seed)
    crew = A.crew_for(leg, seed)
    assumptions.append(A._a("crew_id", crew["crew_id"], None, "SYNTHETIC", "One crew per aircraft rotation"))
    for k, v in rot.items():
        assumptions.append(A._a(k, v, None, "HISTORICAL", "Same airline and tail, next SFO movement in the source"))
    flight.update({
        "state": "SCHEDULED",
        "plan": None,
        "assumptions": assumptions,
        # Optional fields (contract change request pending with Member 1)
        "operation": leg["operation"],
        "segment": leg["segment"],
        "qualifying_source_row": leg["qualifying"],
        "quality_flags": leg["quality_flags"],
        "historical": {
            "use": "Replay and calibration only. Not an input to baseline or agent decisions.",
            "source_type": "HISTORICAL",
            "cancelled": leg["cancelled"], "cancellation_reason": leg["cancellation_reason"],
            "diverted": leg["diverted"],
            "actual_gate_departure_utc": leg["actual_gate_departure_utc"],
            "wheels_off_utc": leg["wheels_off_utc"], "wheels_on_utc": leg["wheels_on_utc"],
            "actual_gate_arrival_utc": leg["actual_gate_arrival_utc"],
            "durations_min": leg["durations_min"],
            "attributed_delay_min": leg["attributed_delay_min"],
        },
    })
    return flight


def build(slice_dir: Path) -> dict:
    sl = load(slice_dir / "traffic_slice.json")
    ctx = load(slice_dir / "context_legs.json")
    dev = load(slice_dir / "dev_fixture.json")
    manifest_path = config.PROCESSED_DIR / "source_manifest.json"
    manifest = load(manifest_path)
    w = sl["window"]
    t0, t_end = A.parse(w["buffer_start_utc"]), A.parse(w["buffer_end_utc"])

    legs, context = sl["flights"], ctx["flights"]
    link = rotations([*legs, *context])
    occupants, intervals = gate_demand(legs, context, link, t0, t_end)
    factor = A.PARAMETERS["gate_headroom_factor"]["value"]
    peak_all, peak_heavy = peak(intervals), peak(intervals, "HEAVY")
    gate_count, heavy_count = math.ceil(peak_all * factor), math.ceil(peak_heavy * factor)

    layout = sfo_layout.build_layout(gate_count, heavy_count)
    layout["connected"] = sfo_layout.is_connected(layout)
    layout_path = config.REPO_ROOT / "data" / "layout" / "sfo_layout.json"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")
    pdf = config.REPO_ROOT / layout["source"]["local_copy"]

    occupancy = assign_occupants(occupants, layout["gates"])
    flights = [to_flight(l, link, SEED) for l in legs]
    operating = [l for l in legs if not l["cancelled"]]
    connections = A.synthetic_connections(
        [l for l in operating if l["operation"] == "ARRIVAL"],
        [l for l in operating if l["operation"] == "DEPARTURE"], SEED)
    crews = {}
    for l in legs:
        c = A.crew_for(l, SEED)
        crews.setdefault(c["crew_id"], c)

    infrastructure = {
        "layout": layout,
        "equipment": [{
            "resource_id": "EQ-PUSHBACK-TUGS", "kind": "PUSHBACK_TUG",
            "capacity": A.PARAMETERS["pushback_tugs"]["value"],
            "use_per_departure_min": A.PARAMETERS["pushback_tug_use_min"]["value"],
            "unit": "count", "source_type": "SYNTHETIC",
        }],
        "crews": sorted(crews.values(), key=lambda c: c["crew_id"]),
        "connections": connections,
    }
    rules_config = {
        "core_window": {"start_utc": w["start_utc"], "end_utc": w["end_utc"]},
        "init_buffer_minutes": config.INIT_BUFFER_MINUTES,
        "recovery_buffer_minutes": config.RECOVERY_BUFFER_MINUTES,
        "member2_assumptions": A.PARAMETERS,
        "gate_sizing": {"peak_scheduled_gate_demand": peak_all, "peak_heavy_gate_demand": peak_heavy,
                        "simulated_gates": gate_count, "heavy_capable_gates": heavy_count,
                        "source_type": "SYNTHETIC"},
        "member1_rules": {
            "note": "Separation, runway occupancy, intersection timing and objective weights are Member 1 "
                    "simulation rules and are intentionally not set here.",
            "values": None,
        },
        "historical_cancellations": "Included as scheduled demand; historical outcome kept under flight.historical",
    }
    source_manifest = {
        **manifest,
        "layout_source": {**layout["source"], "local_copy_sha256": sha256_file(pdf)},
        "derived_files": {
            name: sha256_file(slice_dir / name)
            for name in ("traffic_slice.json", "context_legs.json", "dev_fixture.json", "qualifying_row.json")
        },
    }
    quality = load(slice_dir / "quality_report.json")

    def scenario(scenario_id: str, chosen: list[dict]) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "scenario_id": scenario_id,
            "airport_iata": config.AIRPORT_IATA,
            "timezone": config.AIRPORT_TZ,
            "seed": SEED,
            "start_utc": w["buffer_start_utc"],
            "end_utc": w["buffer_end_utc"],
            "source_manifest": source_manifest,
            "rules_config": rules_config,
            "flights": chosen,
            "infrastructure": infrastructure,
            "initial_occupancy": occupancy,
            # Optional
            "data_quality": {"selected_slice": quality["selected_slice"],
                             "coverage_limits": quality["coverage_limits"] + layout["coverage_limits"]},
        }

    dev_ids = {l["flight_id"] for l in dev["flights"]}
    slug = f"sfo-{w['local_date']}-{w['window_selection']['start_local_hour']:02d}00"
    full = scenario(f"{slug}-full", flights)
    dev_s = scenario(f"{slug}-dev", [f for f in flights if f["flight_id"] in dev_ids])
    for name, s in (("scenario_full.json", full), ("scenario_dev.json", dev_s)):
        (slice_dir / name).write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    facts = {
        "scenario_ids": [full["scenario_id"], dev_s["scenario_id"]],
        "local_date": w["local_date"], "window": [w["start_utc"], w["end_utc"]],
        "horizon": [w["buffer_start_utc"], w["buffer_end_utc"]],
        "flights_full": len(flights), "flights_dev": len(dev_s["flights"]),
        "arrivals_full": sum(f["operation"] == "ARRIVAL" for f in flights),
        "departures_full": sum(f["operation"] == "DEPARTURE" for f in flights),
        "historically_cancelled_full": sum(f["historical"]["cancelled"] for f in flights),
        "qualifying_rows_full": sum(f["qualifying_source_row"] for f in flights),
        "traced_qualifying_flight_id": load(slice_dir / "qualifying_row.json")["normalized"]["flight_id"],
        "initial_occupancy": len(occupancy),
        "initial_occupancy_unassigned": sum(o["gate_id"] is None for o in occupancy),
        "simulated_gates": gate_count, "heavy_capable_gates": heavy_count,
        "synthetic_connections": len(connections), "crews": len(crews),
        "taxiway_graph_connected": layout["connected"],
        "seed": SEED,
    }
    (slice_dir / "expected_facts.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")
    return facts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build Scenario JSON from processed data")
    parser.add_argument("--slice-dir", default=None)
    args = parser.parse_args(argv)
    slice_dir = Path(args.slice_dir) if args.slice_dir else max(config.PROCESSED_DIR.glob("sfo_*"))
    print(json.dumps(build(slice_dir), indent=2))


if __name__ == "__main__":
    main()
