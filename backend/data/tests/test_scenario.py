import json
from pathlib import Path

import pytest

from backend.airport import sfo_layout
from backend.data import config, contract_check
from backend.data.evaluate import run

SLICES = sorted(config.PROCESSED_DIR.glob("sfo_*"))
pytestmark = pytest.mark.skipif(not SLICES, reason="run python -m backend.data.build first")


def load(name):
    return json.loads((SLICES[-1] / name).read_text(encoding="utf-8"))


def test_layout_connected_and_sourced():
    layout = sfo_layout.build_layout(20, 5)
    assert sfo_layout.is_connected(layout)
    assert {r["runway_id"] for r in layout["runways"]} == {"10L-28R", "10R-28L", "01R-19L", "01L-19R"}
    assert layout["source"]["effective_from"] == "2026-10-29"
    assert sum(g["max_wake_category"] == "HEAVY" for g in layout["gates"]) == 5
    crossing = [r for r in layout["runway_relationships"] if r["relationship"] == "INTERSECTING"]
    assert len(crossing) == 4


def test_scenarios_pass_contract_check():
    for name in ("scenario_full.json", "scenario_dev.json"):
        assert contract_check.check_scenario(load(name)) == []


def test_dev_fixture_size_and_mix():
    dev = load("scenario_dev.json")
    assert 20 <= len(dev["flights"]) <= 40
    assert {f["operation"] for f in dev["flights"]} == {"ARRIVAL", "DEPARTURE"}


def test_expected_facts_match_scenario():
    facts, full = load("expected_facts.json"), load("scenario_full.json")
    assert facts["flights_full"] == len(full["flights"])
    assert facts["initial_occupancy"] == len(full["initial_occupancy"]) > 0
    assert facts["initial_occupancy_unassigned"] == 0
    assert any(f["flight_id"] == facts["traced_qualifying_flight_id"] for f in full["flights"])


def test_traced_row_is_raw_source_line():
    q = load("qualifying_row.json")
    raw = dict(zip(q["header"], q["raw_line"].split(",")))
    assert raw == q["normalized"]["source_values"]


def test_occupied_gates_are_unique():
    occ = load("scenario_full.json")["initial_occupancy"]
    gates = [o["gate_id"] for o in occ]
    assert len(gates) == len(set(gates))


def test_historical_actuals_not_in_decision_fields():
    for f in load("scenario_full.json")["flights"]:
        assert f["plan"] is None and f["state"] == "SCHEDULED"
        assert "actual_gate_arrival_utc" not in f


def test_evaluation_inputs_identical_across_policies():
    report = run(SLICES[-1], "runway-10R-28L-closure", {"a": "scheduled", "b": "scheduled"})
    assert report["runs"]["a"]["metrics"]["per_flight"] == report["runs"]["b"]["metrics"]["per_flight"]
    assert len(report["input_sha256"]) == 64
