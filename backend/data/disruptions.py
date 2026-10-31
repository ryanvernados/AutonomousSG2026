"""Disruption fixtures: runway unavailability, taxiway blockage, ground equipment failure.

    python -m backend.data.disruptions [--slice-dir ...]

Each fixture is a list of timed Instruction events (common specification fields) plus
expected facts that can be computed without the engine. Start times and durations are
declared simulation inputs (SYNTHETIC); historical delay minutes do not establish a
closure time or duration.
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

from backend.airport import sfo_layout

from . import assumptions as A
from . import config


def instruction(scenario_id: str, n: int, kind: str, resource_id: str, start, end, parameters: dict) -> dict:
    """Contract Instruction. instruction_version and entered_by are server-owned, so they stay at defaults."""
    return {
        "instruction_id": f"{scenario_id}:fixture-{n}",
        "instruction_version": 0,
        "scenario_id": scenario_id,
        "base_state_version": 0,
        "kind": kind,
        "resource_id": resource_id,
        "starts_at_utc": A.iso(start),
        "ends_at_utc": A.iso(end),
        "flight_id": None,
        "parameters": parameters,
        "entered_by": "",
    }


def build(slice_dir: Path) -> list[dict]:
    facts = json.loads((slice_dir / "expected_facts.json").read_text(encoding="utf-8"))
    scenario = json.loads((config.REPO_ROOT / facts["scenario_files"][0]).read_text(encoding="utf-8"))
    sid = scenario["scenario_id"]
    layout = json.loads((config.REPO_ROOT / "data" / "layout" / "sfo_layout.json").read_text(encoding="utf-8"))
    taxi_ids = {r["resource_id"] for r in scenario["infrastructure"]["resources"] if r["kind"] == "TAXIWAY"}
    t = A.parse(scenario["source_manifest"]["member2"]["core_window"]["start_utc"])
    flights = scenario["flights"]

    def is_arrival(f):
        return f["destination"] == config.AIRPORT_IATA

    def movements_between(start, end):
        return sorted(f["flight_id"] for f in flights
                      if start <= A.parse(f["scheduled_gate_arrival_utc"] if is_arrival(f)
                                          else f["scheduled_gate_departure_utc"]) < end)

    fixtures = []

    # 1. Runway 10R-28L unavailable for 60 minutes from 30 minutes into the core window.
    s, e = t + timedelta(minutes=30), t + timedelta(minutes=90)
    fixtures.append({
        "fixture_id": "runway-10R-28L-closure",
        "disruption": "RUNWAY_UNAVAILABLE",
        "events": [{"at_utc": A.iso(s), "instruction": instruction(
            sid, 1, "RUNWAY_UNAVAILABLE", "10R-28L", s, e, {"reason": "SIMULATED"})}],
        "expected": {
            "closed_runway": "10R-28L",
            "runways_remaining": [r["runway_id"] for r in layout["runways"] if r["runway_id"] != "10R-28L"],
            "scheduled_sfo_movements_in_closure": len(movements_between(s, e)),
        },
    })

    # 2. Taxiway B blocked between B@2400 and B@2900 for 60 minutes. The contract resource is
    # the first piece of the split segment; blocking any piece blocks the whole segment.
    seg = "B:B@2400-B@2900"
    resource = next(r for r in (seg, f"{seg}#1") if r in taxi_ids)
    s, e = t + timedelta(minutes=15), t + timedelta(minutes=75)
    before = sfo_layout.shortest_path(layout, "APRON_A", "HOLD_28L")
    after = sfo_layout.shortest_path(layout, "APRON_A", "HOLD_28L", blocked={seg})
    fixtures.append({
        "fixture_id": "taxiway-B-blockage",
        "disruption": "TAXIWAY_BLOCKED",
        "events": [{"at_utc": A.iso(s), "instruction": instruction(
            sid, 2, "TAXIWAY_BLOCKED", resource, s, e, {"reason": "SIMULATED", "layout_segment": seg})}],
        "expected": {
            "blocked_segment": seg, "blocked_resource_id": resource,
            "route_APRON_A_to_HOLD_28L_m": {"unblocked": before[0], "blocked": after[0] if after else None},
            "alternate_route_exists": after is not None,
            "alternate_route": after[1] if after else None,
        },
    })

    # 3. Pushback tug pool unavailable for 30 minutes at the start of the core window. The engine
    # treats EQUIPMENT_FAILURE as the whole resource being unavailable.
    s, e = t, t + timedelta(minutes=30)
    deps = [f for f in flights if not is_arrival(f) and s <= A.parse(f["scheduled_gate_departure_utc"]) < e]
    fixtures.append({
        "fixture_id": "pushback-tug-failure",
        "disruption": "GROUND_EQUIPMENT_FAILURE",
        "events": [{"at_utc": A.iso(s), "instruction": instruction(
            sid, 3, "EQUIPMENT_FAILURE", A.EQUIPMENT_ID, s, e, {"reason": "SIMULATED"})}],
        "expected": {"scheduled_departures_in_outage": len(deps)},
    })

    for f in fixtures:
        f.update({"scenario_id": sid, "seed": scenario["seed"], "source_type": "SYNTHETIC",
                  "note": "Timing and duration are declared simulation inputs, not historical events."})
    out = slice_dir / "disruptions"
    out.mkdir(exist_ok=True)
    for f in fixtures:
        (out / f"{f['fixture_id']}.json").write_text(json.dumps(f, indent=2) + "\n", encoding="utf-8")
    return fixtures


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write disruption fixtures")
    parser.add_argument("--slice-dir", default=None)
    args = parser.parse_args(argv)
    slice_dir = Path(args.slice_dir) if args.slice_dir else max(config.PROCESSED_DIR.glob("sfo_*"))
    for f in build(slice_dir):
        print(f["fixture_id"], json.dumps(f["expected"]))


if __name__ == "__main__":
    main()
