import json
from datetime import timedelta

import pytest

from backend.airport import sfo_layout
from backend.contracts import Instruction, Plan, Scenario, seal_plan, utcnow
from backend.data import config, contract_check
from backend.data.evaluate import run
from backend.data.scenario import SCENARIO_DIR

SLICES = sorted(config.PROCESSED_DIR.glob("sfo_*"))
pytestmark = pytest.mark.skipif(not SLICES or not SCENARIO_DIR.is_dir(),
                                reason="run python -m backend.data.build and backend.data.scenario first")


def load(name):
    return json.loads((SLICES[-1] / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scenarios() -> dict[str, Scenario]:
    out = {}
    for p in sorted(SCENARIO_DIR.glob("*.json")):
        s = Scenario.model_validate_json(p.read_text(encoding="utf-8"))
        out["dev" if s.scenario_id.endswith("-dev") else "full"] = s
    return out


def test_layout_connected_and_sourced():
    layout = sfo_layout.build_layout(20, 5)
    assert sfo_layout.is_connected(layout)
    assert {r["runway_id"] for r in layout["runways"]} == {"10L-28R", "10R-28L", "01R-19L", "01L-19R"}
    assert layout["source"]["effective_from"] == "2026-10-29"
    assert sum(g["max_wake_category"] == "HEAVY" for g in layout["gates"]) == 5
    assert len([r for r in layout["runway_relationships"] if r["relationship"] == "INTERSECTING"]) == 4


def test_split_segment_keeps_order_and_length():
    seg = {"segment_id": "X:a-b", "from_node": "a", "to_node": "b", "length_m": 1000}
    pieces = sfo_layout.split_segment(seg, 400)
    assert [p[0] for p in pieces] == ["X:a-b#1", "X:a-b#2", "X:a-b#3"]
    assert pieces[0][1] == "a" and pieces[-1][2] == "b"
    assert all(pieces[i][2] == pieces[i + 1][1] for i in range(len(pieces) - 1))
    assert sum(p[3] for p in pieces) == pytest.approx(1000)


def test_both_scenarios_pass_member1_models_and_engine_needs(scenarios):
    assert set(scenarios) == {"full", "dev"}
    for s in scenarios.values():
        assert s.schema_version == "1"
        assert contract_check.engine_issues(s) == []


def test_member1_repository_loads_scenario_dir(scenarios):
    pytest.importorskip("simpy")
    from backend.engine.scenarios import ScenarioRepository
    repo = ScenarioRepository(str(SCENARIO_DIR))
    assert {s.scenario_id for s in repo.list_scenarios()} == {s.scenario_id for s in scenarios.values()}


def test_dev_fixture_size_and_mix(scenarios):
    dev = scenarios["dev"]
    assert 20 <= len(dev.flights) <= 40
    assert {f.destination == "SFO" for f in dev.flights} == {True, False}


def test_expected_facts_match_scenario(scenarios):
    facts, full = load("expected_facts.json"), scenarios["full"]
    assert facts["flights_full"] == len(full.flights)
    assert facts["initial_occupancy"] == len(full.initial_occupancy) > 0
    assert facts["flights_without_gate"] == 0
    assert any(f.flight_id == facts["traced_qualifying_flight_id"] for f in full.flights)


def test_traced_row_is_raw_source_line():
    q = load("qualifying_row.json")
    raw = dict(zip(q["header"], q["raw_line"].split(",")))
    assert raw == q["normalized"]["source_values"]


def test_initial_occupancy_gates_do_not_overlap(scenarios):
    occ = scenarios["full"].initial_occupancy
    assert len({o.resource_id for o in occ}) == len(occ)
    assert all(o.kind == "GATE" and o.flight_id.startswith("OCCUPANT:") for o in occ)


def test_rotation_keeps_same_gate(scenarios):
    flights = {f.flight_id: f for f in scenarios["full"].flights}
    pairs = [(f, flights[f.assumptions["next_departure_flight_id"].value]) for f in flights.values()
             if "next_departure_flight_id" in f.assumptions
             and f.assumptions["next_departure_flight_id"].value in flights]
    assert pairs
    assert all(a.assumptions["gate_id"].value == d.assumptions["gate_id"].value for a, d in pairs)


def test_historical_outcomes_not_in_assumptions(scenarios):
    for f in scenarios["full"].flights:
        assert f.state == "SCHEDULED" and f.plan is None
        assert not {"historical", "cancelled", "attributed_delay_min"} & set(f.assumptions)


def test_engine_places_first_flight_with_all_checks_passing(scenarios):
    pytest.importorskip("simpy")
    from backend.engine import planning
    from backend.engine.scenarios import initial_snapshot
    from backend.engine.validation import validate_plan

    snap = initial_snapshot(scenarios["dev"])
    f = min(snap.flights, key=lambda f: (f.scheduled_gate_arrival_utc if f.destination == "SFO"
                                         else f.scheduled_gate_departure_utc, f.flight_id))
    gate, arrival = f.assumptions["gate_id"].value, f.destination == "SFO"
    runway = "10R-28L"
    route = planning._paths(snap, runway if arrival else gate, gate if arrival else runway)[0]
    slot = max(snap.sim_time_utc + timedelta(minutes=5), f.scheduled_gate_arrival_utc - timedelta(minutes=4))
    assignment = planning._assignment(snap, f, runway, route, slot)
    plan = seal_plan(Plan(scenario_id=snap.scenario_id, base_state_version=0, instruction_version=0,
                          assignments=[assignment], expires_at_utc=utcnow() + timedelta(minutes=5)))
    failing = [c.constraint_id for c in validate_plan(snap, plan) if c.status != "PASS"]
    assert failing == []


def test_disruption_instructions_accepted_by_engine(scenarios):
    pytest.importorskip("simpy")
    from backend.engine.scenarios import initial_snapshot
    from backend.engine.validation import instruction_issues

    snap = initial_snapshot(scenarios["full"])
    fixtures = sorted((SLICES[-1] / "disruptions").glob("*.json"))
    assert len(fixtures) == 3
    for path in fixtures:
        for ev in json.loads(path.read_text(encoding="utf-8"))["events"]:
            assert instruction_issues(snap, Instruction.model_validate(ev["instruction"])) == [], path.name


def test_evaluation_inputs_identical_across_policies():
    report = run(SLICES[-1], "runway-10R-28L-closure", {"a": "scheduled", "b": "scheduled"})
    assert report["runs"]["a"]["metrics"]["per_flight"] == report["runs"]["b"]["metrics"]["per_flight"]
    assert len(report["input_sha256"]) == 64


def test_supabase_rows_and_https_verify(scenarios, monkeypatch):
    httpx = pytest.importorskip("httpx")
    from backend.data import supabase_import

    rows = supabase_import.rows_for([scenarios["dev"]], None)
    assert [len(rows[t]) for t in ("airports", "scenarios", "scenario_flights")] == [1, 1, 30]
    stored = {rows["scenario_flights"][0]["flight_id"]: rows["scenario_flights"][0]["document"]}

    def handler(request):
        assert request.method == "GET" and request.headers["apikey"] == "test-secret"
        table = request.url.path.rsplit("/", 1)[-1]
        if table == "scenario_flights":
            fid = request.url.params["flight_id"].removeprefix("eq.")
            return httpx.Response(200, json=[{"document": stored[fid]}] if fid in stored else [])
        return httpx.Response(200, json=[])

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret")
    summary = supabase_import.verify_https(rows, transport=httpx.MockTransport(handler))
    assert summary["scenario_flights"] == {"present_identical": 1, "present_different": 0, "missing": 29}
    assert summary["scenarios"]["missing"] == 1
