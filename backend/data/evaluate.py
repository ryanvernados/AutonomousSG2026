"""Evaluation runner: feed identical scenario, seed and disruption events to each policy.

    python -m backend.data.evaluate --fixture runway-10R-28L-closure \
        --policy baseline=backend.engine.baseline:run --policy agent=backend.coordination.runner:run

A policy is any callable `run(scenario: dict, events: list[dict], seed: int) -> list[dict]`
returning one outcome per flight:
    {"flight_id", "gate_departure_utc" | None, "gate_arrival_utc" | None,
     "holding_min", "taxi_min", "completed": bool}
Built-in policies:
    scheduled        keeps every scheduled time; exercises the runner only
    engine-baseline  Member 1 safety-constrained FCFS baseline (backend.engine), with the
                     fixture's instructions active from the start
Member 3 supplies the agent policy.

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


def is_arrival(flight: dict) -> bool:
    return flight["destination"] == config.AIRPORT_IATA


def scheduled_policy(scenario, events, seed):
    return [{"flight_id": f["flight_id"],
             "gate_departure_utc": None if is_arrival(f) else f["scheduled_gate_departure_utc"],
             "gate_arrival_utc": f["scheduled_gate_arrival_utc"] if is_arrival(f) else None,
             "holding_min": 0, "taxi_min": None, "completed": True} for f in scenario["flights"]]


def engine_baseline_policy(scenario, events, seed):
    """Member 1 FCFS baseline. Flights it cannot place are returned as not completed."""
    from backend.contracts import Instruction, Scenario
    from backend.engine.planning import baseline_plan
    from backend.engine.scenarios import initial_snapshot

    snap = initial_snapshot(Scenario.model_validate(scenario))
    snap = snap.model_copy(update={"active_instructions": [Instruction.model_validate(e["instruction"])
                                                           for e in events]})
    result = baseline_plan(snap)
    placed = {a.flight_id: a for a in (result.plan.assignments if result.plan else [])}
    out = []
    for f in scenario["flights"]:
        a = placed.get(f["flight_id"])
        out.append({"flight_id": f["flight_id"],
                    "gate_departure_utc": A.iso(a.taxi_start_utc) if a and not is_arrival(f) else None,
                    "gate_arrival_utc": A.iso(a.taxi_end_utc) if a and is_arrival(f) else None,
                    "holding_min": None,
                    "taxi_min": (a.taxi_end_utc - a.taxi_start_utc).total_seconds() / 60 if a else None,
                    "completed": a is not None})
    engine_baseline_policy.last_metadata = {"status": result.status, **result.search_metadata}
    return out


def load_policy(spec: str):
    if spec == "scheduled":
        return scheduled_policy
    if spec == "engine-baseline":
        return engine_baseline_policy
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
        op = "ARRIVAL" if is_arrival(f) else "DEPARTURE"
        key, sched = (("gate_arrival_utc", f["scheduled_gate_arrival_utc"]) if op == "ARRIVAL"
                      else ("gate_departure_utc", f["scheduled_gate_departure_utc"]))
        delay = None
        if o.get(key):
            delay = (A.parse(o[key]) - A.parse(sched)).total_seconds() / 60
        per_flight.append({"flight_id": f["flight_id"], "operation": op, "delay_min": delay,
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


def run(slice_dir: Path, fixture_id: str, policies: dict[str, str], variant: str = "full") -> dict:
    facts = json.loads((slice_dir / "expected_facts.json").read_text(encoding="utf-8"))
    path = next(p for p in facts["scenario_files"] if p.endswith(f"-{variant}.json"))
    scenario = json.loads((config.REPO_ROOT / path).read_text(encoding="utf-8"))
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
        policy = load_policy(spec)
        outcomes = policy(s, ev, seed)
        report["runs"][name] = {"policy": spec, "metrics": metrics(scenario, outcomes),
                                "policy_metadata": getattr(policy, "last_metadata", None)}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run policies on one disruption fixture")
    parser.add_argument("--slice-dir", default=None)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--variant", choices=["full", "dev"], default="full")
    parser.add_argument("--policy", action="append", default=[],
                        help="name=module:function, name=scheduled or name=engine-baseline (repeatable)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    slice_dir = Path(args.slice_dir) if args.slice_dir else max(config.PROCESSED_DIR.glob("sfo_*"))
    policies = dict(p.split("=", 1) for p in args.policy) or {"scheduled": "scheduled"}
    report = run(slice_dir, args.fixture, policies, args.variant)
    out = Path(args.out) if args.out else slice_dir / "evaluations" / f"{args.fixture}-{args.variant}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for name, r in report["runs"].items():
        m = r["metrics"]
        print(name, "input", report["input_sha256"][:12], "completed", m["completed_movements"],
              "unresolved", len(m["unresolved_flights"]))
    print("wrote", out)


if __name__ == "__main__":
    main()
