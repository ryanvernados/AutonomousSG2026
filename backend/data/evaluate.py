"""Evaluation runner: feed identical scenario, seed and disruption events to each policy.

    python -m backend.data.evaluate --fixture runway-10R-28L-closure \
        --policy baseline=backend.engine.baseline:run --policy agent=backend.coordination.runner:run

A policy is any callable `run(scenario: dict, events: list[dict], seed: int) -> list[dict]`
returning one outcome per flight:
    {"flight_id", "gate_departure_utc" | None, "gate_arrival_utc" | None,
     "holding_min", "taxi_min", "completed": bool}
Member 1 and Member 3 supply the real policies. The built-in `scheduled` policy keeps
every scheduled time and exists only to exercise the runner.

The runner hashes the exact inputs handed to each policy, so a comparison report
proves both runs used the same initial state, traffic, seed and events.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
from pathlib import Path

from . import assumptions as A
from . import config


def canonical_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def scheduled_policy(scenario, events, seed):
    return [{"flight_id": f["flight_id"],
             "gate_departure_utc": f["scheduled_gate_departure_utc"] if f["operation"] == "DEPARTURE" else None,
             "gate_arrival_utc": f["scheduled_gate_arrival_utc"] if f["operation"] == "ARRIVAL" else None,
             "holding_min": 0, "taxi_min": None, "completed": True} for f in scenario["flights"]]


def load_policy(spec: str):
    if spec == "scheduled":
        return scheduled_policy
    module, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module), attr)


def metrics(scenario: dict, outcomes: list[dict]) -> dict:
    by_id = {o["flight_id"]: o for o in outcomes}
    per_flight, missing = [], []
    for f in scenario["flights"]:
        o = by_id.get(f["flight_id"])
        if o is None:
            missing.append(f["flight_id"])
            continue
        key, sched = (("gate_arrival_utc", f["scheduled_gate_arrival_utc"]) if f["operation"] == "ARRIVAL"
                      else ("gate_departure_utc", f["scheduled_gate_departure_utc"]))
        delay = None
        if o.get(key):
            delay = (A.parse(o[key]) - A.parse(sched)).total_seconds() / 60
        per_flight.append({"flight_id": f["flight_id"], "operation": f["operation"], "delay_min": delay,
                           "holding_min": o.get("holding_min"), "taxi_min": o.get("taxi_min"),
                           "completed": bool(o.get("completed"))})
    delays = [p["delay_min"] for p in per_flight if p["delay_min"] is not None]

    def agg(op):
        d = [p["delay_min"] for p in per_flight if p["operation"] == op and p["delay_min"] is not None]
        return {"flights": len(d), "total_delay_min": sum(d), "max_delay_min": max(d, default=None)}

    return {
        "arrival_delay": agg("ARRIVAL"), "departure_delay": agg("DEPARTURE"),
        "max_delay_min": max(delays, default=None),
        "holding_min_total": sum(p["holding_min"] or 0 for p in per_flight),
        "completed_movements": sum(p["completed"] for p in per_flight),
        "unresolved_flights": missing + [p["flight_id"] for p in per_flight if not p["completed"]],
        "per_flight": per_flight,
    }


def run(slice_dir: Path, fixture_id: str, policies: dict[str, str]) -> dict:
    scenario = json.loads((slice_dir / "scenario_full.json").read_text(encoding="utf-8"))
    fixture = json.loads((slice_dir / "disruptions" / f"{fixture_id}.json").read_text(encoding="utf-8"))
    seed = fixture["seed"]
    input_hash = canonical_hash({"scenario": scenario, "events": fixture["events"], "seed": seed})
    report = {"fixture_id": fixture_id, "scenario_id": scenario["scenario_id"], "seed": seed,
              "input_sha256": input_hash, "runs": {}}
    for name, spec in policies.items():
        # Each policy gets its own deep copy so one run cannot alter the next run's inputs.
        s, ev = copy.deepcopy(scenario), copy.deepcopy(fixture["events"])
        if canonical_hash({"scenario": s, "events": ev, "seed": seed}) != input_hash:
            raise RuntimeError("Input copy mismatch")
        outcomes = load_policy(spec)(s, ev, seed)
        report["runs"][name] = {"policy": spec, "metrics": metrics(scenario, outcomes)}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run policies on one disruption fixture")
    parser.add_argument("--slice-dir", default=None)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--policy", action="append", default=[],
                        help="name=module:function, or name=scheduled (repeatable)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    slice_dir = Path(args.slice_dir) if args.slice_dir else max(config.PROCESSED_DIR.glob("sfo_*"))
    policies = dict(p.split("=", 1) for p in args.policy) or {"scheduled": "scheduled"}
    report = run(slice_dir, args.fixture, policies)
    out = Path(args.out) if args.out else slice_dir / "evaluations" / f"{args.fixture}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for name, r in report["runs"].items():
        m = r["metrics"]
        print(name, "input", report["input_sha256"][:12], "completed", m["completed_movements"],
              "unresolved", len(m["unresolved_flights"]))
    print("wrote", out)


if __name__ == "__main__":
    main()
