"""Validate Member 2 exports against Member 1's contract models.

    python -m backend.data.contract_check

Every scenario in data/processed/scenarios/ must pass backend.contracts.Scenario, and every
disruption fixture instruction must pass backend.contracts.Instruction. On top of the models,
this checks what the engine needs to plan rather than return UNKNOWN: the required flight
assumptions, gate references, and that initial occupancy points at real gates.
"""
from __future__ import annotations

import json
import sys

from pydantic import ValidationError

from backend.contracts import Instruction, Scenario

from . import config
from .scenario import SCENARIO_DIR

ENGINE_REQUIRED = {"wake_category": "category", "gate_id": "resource_id", "crew_id": "resource_id",
                   "equipment_id": "resource_id"}
ARRIVAL_REQUIRED = {"latest_safe_arrival_utc": "UTC"}


def engine_issues(s: Scenario) -> list[str]:
    resources = {r.resource_id: r for r in s.infrastructure.resources}
    wake_categories = {c for pair in s.rules_config.wake_separation_seconds for c in pair.split(":")}
    issues = []
    for f in s.flights:
        required = {**ENGINE_REQUIRED, **(ARRIVAL_REQUIRED if f.destination == s.airport_iata else {})}
        for name, unit in required.items():
            a = f.assumptions.get(name)
            if a is None:
                issues.append(f"{f.flight_id}: missing assumption {name}")
            elif a.unit != unit:
                issues.append(f"{f.flight_id}: {name} unit {a.unit!r}, engine expects {unit!r}")
        for name in ("gate_id", "crew_id", "equipment_id"):
            a = f.assumptions.get(name)
            if a and a.value not in resources:
                issues.append(f"{f.flight_id}: {name} {a.value} is not a resource")
        cat = f.assumptions.get("wake_category")
        if cat and cat.value not in wake_categories:
            issues.append(f"{f.flight_id}: wake category {cat.value} has no separation pair in rules_config")
    for r in s.initial_occupancy:
        if resources.get(r.resource_id) is None or resources[r.resource_id].kind != r.kind:
            issues.append(f"initial occupancy {r.reservation_id} references {r.resource_id}")
    return issues


def main() -> None:
    failures = 0
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        try:
            s = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
            errors = engine_issues(s)
        except ValidationError as e:
            errors = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]
        failures += bool(errors)
        print(f"{'FAIL' if errors else 'PASS'} {path.relative_to(config.REPO_ROOT)}"
              f"{f' ({len(errors)} issues)' if errors else ''}")
        for e in errors[:20]:
            print("   ", e)
    for path in sorted(config.PROCESSED_DIR.glob("sfo_*/disruptions/*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        errors = []
        for ev in fixture["events"]:
            try:
                Instruction.model_validate(ev["instruction"])
            except ValidationError as e:
                errors += [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]
        failures += bool(errors)
        print(f"{'FAIL' if errors else 'PASS'} {path.relative_to(config.REPO_ROOT)}")
        for e in errors:
            print("   ", e)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
