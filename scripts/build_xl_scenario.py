"""Build the large demonstration scenario: all 218 flights with a published starting schedule.

    uv run python -m scripts.build_xl_scenario

Derived from Member 2's sfo-2015-12-21-2000-full scenario with two changes:

* the horizon ends EXTENSION later (SYNTHETIC): the full scenario's last departures are
  scheduled minutes before its end, so they can never be placed and no complete plan exists;
* every flight carries a published assignment from the engine's fuel-urgent FCFS baseline,
  computed here once with a large placement budget (minutes), with its reservations added to
  initial_occupancy. Sessions start with the whole evening scheduled; a disruption then
  replans only the flights it affects, which keeps negotiations within their time budget.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import timedelta
from pathlib import Path

import backend.engine.planning as planning
from backend.contracts import Scenario
from backend.engine import feasible, reservations_for, validate_plan
from backend.engine.scenarios import initial_snapshot

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/processed/scenarios/sfo-2015-12-21-2000-full.json"
TARGET = ROOT / "data/processed/scenarios/sfo-2015-12-21-1900-xl.json"
EXTENSION = timedelta(minutes=75)
PLACEMENTS_PER_FLIGHT = 300  # offline only; the live search keeps the engine default


def main() -> int:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    scenario = Scenario.model_validate(data)
    scenario = scenario.model_copy(update={"scenario_id": TARGET.stem, "end_utc": scenario.end_utc + EXTENSION})
    snapshot = initial_snapshot(scenario)
    planning.PLACEMENTS_PER_FLIGHT = PLACEMENTS_PER_FLIGHT
    started = time.perf_counter()
    plans, meta = planning.search_candidates(snapshot, None, 1, baseline=True)
    elapsed = time.perf_counter() - started
    if not plans:
        print(f"No complete baseline after {elapsed:.0f}s: {meta['rejection_counts']}", file=sys.stderr)
        return 1
    plan = plans[0]
    if not feasible(validate_plan(snapshot, plan)):
        print("Baseline failed final validation", file=sys.stderr)
        return 1
    assignments = {a.flight_id: a for a in plan.assignments}
    flights = [f.model_copy(update={"plan": assignments.get(f.flight_id)}) for f in scenario.flights]
    occupancy = list(scenario.initial_occupancy) + [r for a in plan.assignments for r in reservations_for(a)]
    manifest = {**scenario.source_manifest, "xl_scenario": {
        "derived_from": SOURCE.name, "horizon_extension_minutes": EXTENSION.total_seconds() / 60,
        "source_type": "SYNTHETIC", "published_schedule": "engine FUEL_URGENT_THEN_FCFS baseline",
        "placement_budget": meta["placement_budget"], "attempted_placements": meta["attempted_placements"],
        "baseline_delay_minutes": plan.impact.aggregate["delay_minutes"]}}
    out = scenario.model_copy(update={"flights": flights, "initial_occupancy": occupancy,
                                      "source_manifest": manifest})
    TARGET.write_text(json.dumps(Scenario.model_validate(out.model_dump()).model_dump(mode="json"), indent=2,
                                 ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{TARGET.name}: {len(flights)} flights scheduled in {elapsed:.0f}s, "
          f"{len(occupancy)} reservations, baseline delay {plan.impact.aggregate['delay_minutes']:.0f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
