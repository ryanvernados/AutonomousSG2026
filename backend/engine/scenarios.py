import json
from pathlib import Path

from backend.contracts import Scenario, Snapshot


class ScenarioRepository:
    def __init__(self, directory: str = "fixtures"):
        self.directory = Path(directory)

    def list_scenarios(self) -> list[Scenario]:
        return [Scenario.model_validate_json(path.read_text(encoding="utf-8"))
                for path in sorted(self.directory.glob("*.json"))]

    def load_scenario(self, scenario_id: str) -> Scenario:
        # No user-supplied path interpolation.
        for scenario in self.list_scenarios():
            if scenario.scenario_id == scenario_id:
                return scenario
        raise KeyError(scenario_id)


def load_scenario(scenario_id: str) -> Scenario:
    return ScenarioRepository().load_scenario(scenario_id)


def initial_snapshot(scenario: Scenario) -> Snapshot:
    return Snapshot(scenario_id=scenario.scenario_id, state_version=0,
                    sim_time_utc=scenario.start_utc, flights=scenario.flights,
                    resource_reservations=scenario.initial_occupancy, active_instructions=[],
                    data_quality={"source_manifest": scenario.source_manifest,
                                  "coverage_limits": scenario.infrastructure.coverage_limits},
                    infrastructure=scenario.infrastructure, rules_config=scenario.rules_config,
                    start_utc=scenario.start_utc, end_utc=scenario.end_utc,
                    airport_iata=scenario.airport_iata, seed=scenario.seed)


def export_scenario(scenario: Scenario, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(scenario.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
