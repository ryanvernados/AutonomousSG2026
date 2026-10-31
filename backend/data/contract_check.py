"""Validate exported files against the shared contract.

    python -m backend.data.contract_check

Uses Member 1's Pydantic models from backend.contracts when they exist. Until then it
checks the required fields listed in the common specification, which is a presence check
only and not a competing schema.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config

REQUIRED = {
    "Scenario": ["schema_version", "scenario_id", "airport_iata", "timezone", "seed", "start_utc", "end_utc",
                 "source_manifest", "rules_config", "flights", "infrastructure", "initial_occupancy"],
    "Flight": ["flight_id", "source_row_id", "airline", "flight_number", "tail_number", "origin", "destination",
               "scheduled_gate_departure_utc", "scheduled_gate_arrival_utc", "state", "plan", "assumptions"],
    "Instruction": ["instruction_id", "instruction_version", "scenario_id", "base_state_version", "kind",
                    "resource_id", "starts_at_utc", "ends_at_utc", "flight_id", "parameters", "entered_by"],
}
SOURCE_TYPES = {"HISTORICAL", "SOURCED", "SYNTHETIC"}


def member1_models():
    try:
        from backend import contracts  # type: ignore
        return getattr(contracts, "Scenario", None), getattr(contracts, "Instruction", None)
    except ImportError:
        return None, None


def missing(obj: dict, entity: str) -> list[str]:
    return [k for k in REQUIRED[entity] if k not in obj]


def check_scenario(s: dict) -> list[str]:
    errors = [f"Scenario missing {k}" for k in missing(s, "Scenario")]
    ids = set()
    for f in s.get("flights", []):
        errors += [f"{f.get('flight_id')}: Flight missing {k}" for k in missing(f, "Flight")]
        if f["flight_id"] in ids:
            errors.append(f"duplicate flight_id {f['flight_id']}")
        ids.add(f["flight_id"])
        for a in f["assumptions"]:
            if a.get("source_type") not in SOURCE_TYPES:
                errors.append(f"{f['flight_id']}: assumption {a.get('name')} has source_type {a.get('source_type')}")
    for occ in s.get("initial_occupancy", []):
        if occ.get("source_type") not in SOURCE_TYPES:
            errors.append(f"initial_occupancy entry without valid source_type: {occ}")
    return errors


def main() -> None:
    scenario_model, instruction_model = member1_models()
    mode = "Member 1 models" if scenario_model else "required-field check (Member 1 models not published yet)"
    print("Validation mode:", mode)
    failures = 0
    for slice_dir in sorted(config.PROCESSED_DIR.glob("sfo_*")):
        for path in sorted(slice_dir.glob("scenario_*.json")):
            s = json.loads(path.read_text(encoding="utf-8"))
            errors = check_scenario(s)
            if scenario_model:
                try:
                    scenario_model.model_validate(s)
                except Exception as e:  # report every model error, keep going
                    errors.append(str(e))
            failures += bool(errors)
            print(f"{'FAIL' if errors else 'PASS'} {path.relative_to(config.REPO_ROOT)} ({len(s['flights'])} flights)")
            for e in errors[:20]:
                print("   ", e)
        for path in sorted((slice_dir / "disruptions").glob("*.json")):
            fx = json.loads(path.read_text(encoding="utf-8"))
            errors = [f"Instruction missing {k}" for ev in fx["events"] for k in missing(ev["instruction"], "Instruction")]
            if instruction_model:
                for ev in fx["events"]:
                    try:
                        instruction_model.model_validate(ev["instruction"])
                    except Exception as e:
                        errors.append(str(e))
            failures += bool(errors)
            print(f"{'FAIL' if errors else 'PASS'} {path.relative_to(config.REPO_ROOT)}")
            for e in errors:
                print("   ", e)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
