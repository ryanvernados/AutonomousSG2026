from datetime import timedelta

import pytest
from pydantic import ValidationError

from backend.contracts import Instruction, Reservation, Scenario, plan_hash, seal_plan
from backend.engine import advance_clock, feasible, planning, validate_plan
from backend.engine.planning import baseline_plan, compute_impact, search_candidates, select_fallback


def change_assignment(plan, index=0, **updates):
    assignments = list(plan.assignments)
    assignments[index] = assignments[index].model_copy(update=updates)
    return seal_plan(plan.model_copy(update={"assignments": assignments}))


def failures(snapshot, plan):
    return {c.constraint_id for c in validate_plan(snapshot, plan) if c.status != "PASS"}


def test_fixture_baseline_and_fallback_use_same_checks(snapshot, plan):
    baseline = baseline_plan(snapshot)
    assert len(plan.assignments) == 24
    assert baseline.plan is not None
    assert feasible(validate_plan(snapshot, plan))
    assert feasible(validate_plan(snapshot, baseline.plan))
    assert plan.impact.objective_weights == baseline.plan.impact.objective_weights
    assert compute_impact(snapshot, plan, baseline.plan).versus_baseline["delay_minutes"] == 0
    assert snapshot.resource_reservations  # Not artificially empty at initialization.


def test_wake_and_dependent_runway_conflicts(snapshot, plan):
    first = plan.assignments[0]
    modified = change_assignment(plan, 1, runway_id="28R",
                                 runway_time_utc=first.runway_time_utc + timedelta(seconds=20))
    assert {"runway.wake", "runway.intersection"} <= failures(snapshot, modified)


def test_intersecting_runways_block_overlap_without_wake_pairs(snapshot, plan):
    # Crossing runways: occupancy may not overlap, but category wake pairs do not apply.
    resources = [r.model_copy(update={"dependent_runway_ids": [], "intersecting_runway_ids": [
        "28R" if r.resource_id == "28L" else "28L"]}) if r.kind == "RUNWAY" else r
        for r in snapshot.infrastructure.resources]
    crossing = snapshot.model_copy(update={"infrastructure": snapshot.infrastructure.model_copy(
        update={"resources": resources})})
    first = plan.assignments[0]
    overlapping = change_assignment(plan, 1, runway_id="28R",
                                    runway_time_utc=first.runway_time_utc + timedelta(seconds=20))
    found = failures(crossing, overlapping)
    assert "runway.intersection" in found and "runway.wake" not in found
    clear = change_assignment(plan, 1, runway_id="28R",
                              runway_time_utc=first.runway_time_utc + timedelta(seconds=70))
    assert not {"runway.intersection", "runway.wake"} & failures(crossing, clear)
    # The same 70 s gap on dependent runways still violates the 120 s wake pair.
    assert "runway.wake" in failures(snapshot, clear)


@pytest.mark.parametrize("kind,resource", [("RUNWAY_UNAVAILABLE", "28L"),
    ("TAXIWAY_BLOCKED", "TX-28L-G00"), ("EQUIPMENT_FAILURE", "GSE-POOL")])
def test_disruption_rejected(snapshot, plan, kind, resource):
    instruction = Instruction(scenario_id=snapshot.scenario_id, base_state_version=0,
        kind=kind, resource_id=resource, starts_at_utc=snapshot.start_utc, ends_at_utc=snapshot.end_utc)
    restricted = snapshot.model_copy(update={"active_instructions": [instruction]})
    assert "instruction.restriction" in failures(restricted, plan)


def test_protected_slot_rejected(snapshot, plan):
    a = plan.assignments[0]
    instruction = Instruction(scenario_id=snapshot.scenario_id, base_state_version=0,
        kind="PROTECTED_SLOT", resource_id="28R", flight_id=a.flight_id,
        starts_at_utc=a.runway_time_utc, ends_at_utc=a.runway_time_utc + timedelta(minutes=2))
    assert "instruction.protected_flight" in failures(snapshot.model_copy(update={
        "active_instructions": [instruction]}), plan)


def test_occupied_gate_includes_nonparticipant(snapshot, plan):
    a = plan.assignments[0]
    occupancy = Reservation(resource_id=a.gate_id, flight_id="UNRELATED", kind="GATE",
        starts_at_utc=a.gate_start_utc, ends_at_utc=a.gate_end_utc)
    restricted = snapshot.model_copy(update={"resource_reservations": snapshot.resource_reservations + [occupancy]})
    assert "resource.capacity" in failures(restricted, plan)


def test_taxi_connectivity_and_segment_capacity(snapshot, plan):
    modified = change_assignment(plan, taxi_path_ids=["TX-28L-G01"])
    assert "taxi.connectivity" in failures(snapshot, modified)
    a = plan.assignments[0]
    occupancy = Reservation(resource_id=a.taxi_path_ids[0], flight_id="UNRELATED", kind="TAXIWAY",
        starts_at_utc=a.taxi_start_utc, ends_at_utc=a.taxi_end_utc)
    assert "resource.capacity" in failures(snapshot.model_copy(update={
        "resource_reservations": [occupancy]}), plan)


def test_gate_compatibility_and_turnaround(snapshot, plan):
    modified = change_assignment(plan, gate_end_utc=plan.assignments[0].gate_start_utc + timedelta(seconds=1))
    assert "gate.turnaround" in failures(snapshot, modified)
    resources = [r.model_copy(update={"compatible_categories": ["LIGHT"]}) if r.resource_id == "G00" else r
                 for r in snapshot.infrastructure.resources]
    restricted = snapshot.model_copy(update={"infrastructure": snapshot.infrastructure.model_copy(
        update={"resources": resources})})
    assert "gate.compatibility" in failures(restricted, plan)


def test_crew_working_limit_and_unknown_duty(snapshot, plan):
    for change, expected in [({"max_work_seconds": 1}, "crew.work_limit"),
                             ({"available_until_utc": None}, "crew.duty_data")]:
        resources = [r.model_copy(update=change) if r.resource_id == "CREW-00" else r
                     for r in snapshot.infrastructure.resources]
        restricted = snapshot.model_copy(update={"infrastructure": snapshot.infrastructure.model_copy(
            update={"resources": resources})})
        assert expected in failures(restricted, plan)


def test_equipment_capacity(snapshot, plan):
    a = plan.assignments[0]
    occupancy = Reservation(resource_id="GSE-POOL", flight_id="UNRELATED", kind="EQUIPMENT", quantity=2,
        starts_at_utc=a.taxi_start_utc, ends_at_utc=a.taxi_end_utc)
    assert "resource.capacity" in failures(snapshot.model_copy(update={
        "resource_reservations": [occupancy]}), plan)


@pytest.mark.parametrize("value,status", [(None, "UNKNOWN"), ("bad-date", "UNKNOWN"),
    ("2015-01-01T16:01:00Z", "FAIL"), ("2015-01-01T16:01:00", "UNKNOWN")])
def test_unknown_and_unsafe_fuel_blocks_approval(snapshot, plan, value, status):
    flights = list(snapshot.flights)
    assumptions = dict(flights[0].assumptions)
    if value is None:
        assumptions.pop("latest_safe_arrival_utc")
    else:
        assumptions["latest_safe_arrival_utc"] = assumptions["latest_safe_arrival_utc"].model_copy(update={"value": value})
    flights[0] = flights[0].model_copy(update={"assumptions": assumptions})
    checks = validate_plan(snapshot.model_copy(update={"flights": flights}), plan)
    assert any(c.constraint_id == "fuel.latest_safe_arrival" and c.status == status for c in checks)
    assert not feasible(checks)


def test_bounded_search_and_closed_airport(snapshot, monkeypatch):
    # limit caps complete candidate plans; placement work has its own reported budget.
    plans, metadata = search_candidates(snapshot, limit=1)
    assert len(plans) <= 1 and metadata["limit"] == 1 and not metadata["exhaustive"]
    assert metadata["placement_budget"] == len(snapshot.flights)
    # Exhausting the placement budget stops the search, reports it, and returns no partial plan.
    monkeypatch.setattr(planning, "PLACEMENTS_PER_FLIGHT", 0.5)
    plans, metadata = search_candidates(snapshot, limit=1)
    assert not plans and metadata["limit_reached"] and not metadata["exhaustive"]
    instructions = [Instruction(scenario_id=snapshot.scenario_id, base_state_version=0,
        kind="RUNWAY_UNAVAILABLE", resource_id=r, starts_at_utc=snapshot.start_utc, ends_at_utc=None)
        for r in ("28L", "28R")]
    result = select_fallback(snapshot.model_copy(update={"active_instructions": instructions}), "CLOSED")
    assert result.status == "NO_FEASIBLE_PLAN_FOUND" and result.plan is None


def test_hash_detects_nested_content_change(snapshot, plan):
    assert plan.plan_hash == plan_hash(plan)
    changed = plan.model_copy(update={"assignments": list(reversed(plan.assignments))})
    assert "plan.identity" in failures(snapshot, changed)


def test_clock_preserves_actual_and_scheduled_times(snapshot, plan):
    assignments = {a.flight_id: a for a in plan.assignments}
    snapshot = snapshot.model_copy(update={"flights": [f.model_copy(update={"plan": assignments[f.flight_id]})
                                                        for f in snapshot.flights]})
    after = advance_clock(snapshot, 1200)
    assert after.sim_time_utc == snapshot.sim_time_utc + timedelta(minutes=20)
    assert after.flights[0].state == "TURNAROUND"
    assert after.flights[0].wheels_on_utc == plan.assignments[0].runway_time_utc
    assert after.flights[0].actual_gate_arrival_utc == plan.assignments[0].taxi_end_utc
    assert after.flights[0].scheduled_gate_arrival_utc == snapshot.flights[0].scheduled_gate_arrival_utc
    assert after.flights[1].state == "DEPARTED"


def test_contract_rejects_naive_times_and_extra_fields():
    from backend.engine.scenarios import load_scenario
    data = load_scenario("sfo-synthetic-v1").model_dump(mode="json")
    data["start_utc"] = "2015-01-01T16:00:00"
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_optional_snapshot_context_is_unknown_and_cannot_plan(snapshot, plan):
    from backend.contracts import Snapshot
    value = snapshot.model_dump(mode="json")
    for key in ("infrastructure", "rules_config", "start_utc", "end_utc", "airport_iata", "seed"):
        value.pop(key)
    minimal = Snapshot.model_validate(value)
    assert not feasible(validate_plan(minimal, plan))
    assert select_fallback(minimal, "MISSING_CONTEXT").status == "NO_FEASIBLE_PLAN_FOUND"


def test_future_flights_do_not_enter_holding_queue_early(snapshot, plan):
    assert all(f.state == "SCHEDULED" for f in advance_clock(snapshot, 1).flights)
    assignments = {a.flight_id: a for a in plan.assignments}
    assigned = snapshot.model_copy(update={"flights": [f.model_copy(update={"plan": assignments[f.flight_id]})
                                                       for f in snapshot.flights]})
    assert advance_clock(assigned, 1).flights[0].state == "SCHEDULED"


def test_same_tail_requires_turnaround(snapshot, plan):
    flights = list(snapshot.flights)
    flights[1] = flights[1].model_copy(update={"tail_number": flights[0].tail_number,
        "scheduled_gate_departure_utc": flights[0].scheduled_gate_arrival_utc + timedelta(minutes=5)})
    assert "rotation.turnaround" in failures(snapshot.model_copy(update={"flights": flights}), plan)
