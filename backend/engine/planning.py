"""Bounded constructive search. Finding no candidate is not an infeasibility proof."""
from collections import Counter
from datetime import datetime, timedelta

from backend.contracts import (
    Assignment,
    FallbackResult,
    Impact,
    Interval,
    Plan,
    Snapshot,
    seal_plan,
    utcnow,
)
from backend.engine.validation import feasible, validate_plan


def compute_impact(snapshot: Snapshot, plan: Plan, baseline_plan: Plan | None = None) -> Impact:
    if snapshot.end_utc is None or snapshot.rules_config is None:
        raise ValueError("Authoritative snapshot horizon and rules required for impact computation")
    assignments = {f.flight_id: f.plan for f in snapshot.flights if f.plan}
    assignments.update({a.flight_id: a for a in plan.assignments})
    per_flight = {}
    unresolved, unfinished = [], []
    for f in snapshot.flights:
        a = assignments.get(f.flight_id)
        if not a:
            unresolved.append(f.flight_id)
            unfinished.append(f.flight_id)
            continue
        taxi = (a.taxi_end_utc - a.taxi_start_utc).total_seconds() / 60
        if a.operation == "ARRIVAL":
            delay = max(0, (a.taxi_end_utc - f.scheduled_gate_arrival_utc).total_seconds() / 60)
            holding = max(0, delay)  # fixed taxi proxy; no fuel savings claim
            arrival_delay, departure_delay = delay, 0
        else:
            delay = max(0, (a.taxi_start_utc - f.scheduled_gate_departure_utc).total_seconds() / 60)
            holding = 0
            arrival_delay, departure_delay = 0, delay
        completion = a.taxi_end_utc if a.operation == "ARRIVAL" else a.runway_time_utc
        planned_complete_in_horizon = completion <= snapshot.end_utc
        observed_completion = f.actual_gate_arrival_utc if a.operation == "ARRIVAL" else f.wheels_off_utc
        complete_in_horizon = bool(observed_completion and snapshot.start_utc <= observed_completion
                                   <= min(snapshot.sim_time_utc, snapshot.end_utc))
        if not complete_in_horizon or not planned_complete_in_horizon:
            unfinished.append(f.flight_id)
        per_flight[f.flight_id] = {"delay_minutes": delay, "arrival_delay_minutes": arrival_delay,
                                  "departure_delay_minutes": departure_delay, "holding_minutes": holding,
                                  "taxi_minutes": taxi, "completed_in_horizon": complete_in_horizon,
                                  "planned_completion_in_horizon": planned_complete_in_horizon,
                                  "turnaround_delay_minutes": None, "connection_penalty": None,
                                  "permitted_overtime_minutes": None}
    aggregates = {key: sum(float(v[key]) for v in per_flight.values()) for key in (
        "delay_minutes", "arrival_delay_minutes", "departure_delay_minutes", "holding_minutes", "taxi_minutes")}
    aggregates["max_wait_minutes"] = max((float(v["delay_minutes"]) for v in per_flight.values()), default=0)
    aggregates["completed_movements"] = sum(bool(v["completed_in_horizon"]) for v in per_flight.values())
    aggregates["planned_completed_movements"] = sum(bool(v["planned_completion_in_horizon"]) for v in per_flight.values())
    aggregates["unresolved_flights"] = len(unresolved)
    aggregates["unfinished_flights"] = len(unfinished)
    aggregates.update({k: None for k in ("turnaround_delay_minutes", "connection_penalty", "permitted_overtime_minutes")})
    weights = snapshot.rules_config.objective_weights
    score = sum(aggregates.get(key, 0) * weight for key, weight in weights.items())
    comparison = {}
    if baseline_plan:
        baseline = compute_impact(snapshot, baseline_plan)
        comparison = {k: v - baseline.aggregate[k] for k, v in aggregates.items()
                      if v is not None and baseline.aggregate[k] is not None}
    return Impact(per_flight=per_flight, aggregate=aggregates, versus_baseline=comparison,
                  objective_weights=weights, score=score, unresolved_flight_ids=unresolved,
                  unfinished_flight_ids=unfinished)


def _paths(snapshot, start, end, max_paths=4):
    edges = [r for r in snapshot.infrastructure.resources if r.kind == "TAXIWAY"]
    queue = [(start, [], {start})]
    found = []
    while queue and len(found) < max_paths:
        node, path, seen = queue.pop(0)
        if node == end:
            found.append(path)
            continue
        for edge in sorted(edges, key=lambda x: x.resource_id):
            nxt = edge.node_to if edge.node_from == node else (
                edge.node_from if edge.bidirectional and edge.node_to == node else None)
            if nxt and nxt not in seen:
                queue.append((nxt, path + [edge.resource_id], seen | {nxt}))
    return found


def _assignment(snapshot, flight, runway, route, slot):
    assumptions = flight.assumptions
    gate = assumptions.get("gate_id")
    crew = assumptions.get("crew_id")
    equipment = assumptions.get("equipment_id")
    if not all((gate, crew, equipment)):
        return None
    taxi_seconds = max(180, len(route) * max(
        (r.minimum_traversal_seconds for r in snapshot.infrastructure.resources if r.resource_id in route),
        default=30))
    arrival = flight.destination == snapshot.airport_iata
    taxi_start = slot + timedelta(seconds=snapshot.rules_config.runway_occupancy_seconds) if arrival else (
        slot - timedelta(seconds=taxi_seconds))
    taxi_end = taxi_start + timedelta(seconds=taxi_seconds)
    gate_start = taxi_end if arrival else max(snapshot.sim_time_utc,
        flight.scheduled_gate_departure_utc - timedelta(minutes=20))
    gate_end = gate_start + timedelta(seconds=snapshot.rules_config.minimum_turnaround_seconds) if arrival else (
        taxi_start)
    if gate_end <= gate_start:
        return None
    crew_start = min(taxi_start, slot)
    crew_end = max(taxi_end, slot + timedelta(seconds=snapshot.rules_config.runway_occupancy_seconds))
    return Assignment(flight_id=flight.flight_id, operation="ARRIVAL" if arrival else "DEPARTURE",
                      runway_id=runway, runway_time_utc=slot, taxi_path_ids=route,
                      taxi_start_utc=taxi_start, taxi_end_utc=taxi_end,
                      gate_id=str(gate.value), gate_start_utc=gate_start, gate_end_utc=gate_end,
                      crew_reservations=[Interval(resource_id=str(crew.value), starts_at_utc=crew_start,
                                                  ends_at_utc=crew_end)],
                      equipment_reservations=[Interval(resource_id=str(equipment.value),
                                                       starts_at_utc=taxi_start, ends_at_utc=taxi_end)])


def search_candidates(snapshot: Snapshot, constraints=None, limit=100, *, baseline=False):
    """Budget counts every placement validation, not only successful full schedules.

    constraints is a typed Instruction list from Member 3's tools, never arbitrary executable input.
    Two stable orderings are explored within one shared budget. The search is non-exhaustive.
    """
    if not all((snapshot.infrastructure, snapshot.rules_config, snapshot.start_utc,
                snapshot.end_utc, snapshot.airport_iata)):
        return [], {"attempted_placements": 0, "limit": min(max(int(limit), 1), 100),
                    "exhaustive": False, "limit_reached": False,
                    "rejection_counts": {"snapshot.context_unknown": 1}}
    if constraints:
        snapshot = snapshot.model_copy(update={"active_instructions": snapshot.active_instructions + list(constraints)})
    budget = min(max(int(limit), 1), snapshot.rules_config.search_limit, 100)
    attempted = 0
    rejected = Counter()
    plans = []
    runways = sorted(r.resource_id for r in snapshot.infrastructure.resources if r.kind == "RUNWAY")
    flights = [f for f in snapshot.flights if f.state not in {"DEPARTED", "COMPLETED"}
               and not any(r.flight_id == f.flight_id and r.starts_at_utc < snapshot.sim_time_utc
                           and r.ends_at_utc > snapshot.sim_time_utc for r in snapshot.resource_reservations)]

    def scheduled(f):
        return f.scheduled_gate_arrival_utc if f.destination == snapshot.airport_iata else f.scheduled_gate_departure_utc

    def urgency(f):
        value = f.assumptions.get("latest_safe_arrival_utc")
        try:
            deadline = datetime.fromisoformat(str(value.value).replace("Z", "+00:00")) if value else snapshot.end_utc
            return deadline if deadline.tzinfo else snapshot.end_utc
        except (ValueError, TypeError):
            return snapshot.end_utc

    # Fuel-urgent flights take precedence; otherwise first come first served.
    orderings = [sorted(flights, key=lambda f: (
        urgency(f) > snapshot.sim_time_utc + timedelta(minutes=30), scheduled(f), f.flight_id))]
    if not baseline:
        orderings.append(sorted(flights, key=lambda f: (urgency(f), scheduled(f), f.flight_id)))
    for ordered in orderings:
        if attempted >= budget:
            break
        assignments = []
        for flight in ordered:
            arrival = flight.destination == snapshot.airport_iata
            gate = flight.assumptions.get("gate_id")
            if gate is None:
                rejected["gate.missing_assumption"] += 1
                break
            options = [(r, path) for r in runways for path in _paths(snapshot,
                r if arrival else str(gate.value), str(gate.value) if arrival else r)]
            if not options:
                rejected["taxi.no_connected_route"] += 1
                break
            anchor = max(snapshot.sim_time_utc + timedelta(minutes=5), scheduled(flight) + (
                timedelta(minutes=-4) if arrival else timedelta(minutes=3)))
            chosen = None
            offset = 0
            while attempted < budget and anchor + timedelta(seconds=offset) <= snapshot.end_utc:
                for runway, route in options:
                    if attempted >= budget:
                        break
                    candidate = _assignment(snapshot, flight, runway, route, anchor + timedelta(seconds=offset))
                    attempted += 1
                    if not candidate:
                        rejected["flight.missing_assumptions"] += 1
                        continue
                    plan = seal_plan(Plan(scenario_id=snapshot.scenario_id,
                        base_state_version=snapshot.state_version, instruction_version=snapshot.instruction_version,
                        assignments=assignments + [candidate], source="BASELINE" if baseline else "FALLBACK",
                        expires_at_utc=utcnow() + timedelta(seconds=snapshot.rules_config.proposal_validity_seconds)))
                    checks = validate_plan(snapshot, plan)
                    if feasible(checks):
                        chosen = candidate
                        break
                    rejected.update(c.constraint_id for c in checks if c.severity == "HARD" and c.status != "PASS")
                if chosen:
                    break
                offset += snapshot.rules_config.search_step_seconds
            if chosen is None:
                break
            assignments.append(chosen)
        if assignments and len(assignments) == len(ordered):
            plan = seal_plan(Plan(scenario_id=snapshot.scenario_id,
                base_state_version=snapshot.state_version, instruction_version=snapshot.instruction_version,
                assignments=assignments, source="BASELINE" if baseline else "FALLBACK",
                expires_at_utc=utcnow() + timedelta(seconds=snapshot.rules_config.proposal_validity_seconds)))
            plan = seal_plan(plan.model_copy(update={"impact": compute_impact(snapshot, plan)}))
            if feasible(validate_plan(snapshot, plan)):
                plans.append(plan)
    return plans, {"attempted_placements": attempted, "limit": budget, "exhaustive": False,
                   "limit_reached": attempted >= budget, "rejection_counts": dict(rejected),
                   "objective_weights": snapshot.rules_config.objective_weights,
                   "policy": "FUEL_URGENT_THEN_FCFS" if baseline else "BOUNDED_STABLE_SEARCH"}


def enumerate_candidates(snapshot: Snapshot, constraints=None, limit=100) -> list[Plan]:
    return search_candidates(snapshot, constraints, limit)[0]


def select_fallback(snapshot: Snapshot, reason: str) -> FallbackResult:
    plans, metadata = search_candidates(snapshot)
    selected = min(plans, key=lambda p: (p.impact.score, tuple(a.flight_id for a in p.assignments))) if plans else None
    return FallbackResult(status="AWAITING_AUTHORIZATION" if selected else "NO_FEASIBLE_PLAN_FOUND",
                          reason=reason, plan=selected, checks=validate_plan(snapshot, selected) if selected else [],
                          search_metadata=metadata)


def baseline_plan(snapshot: Snapshot) -> FallbackResult:
    plans, metadata = search_candidates(snapshot, baseline=True)
    return FallbackResult(status="AWAITING_AUTHORIZATION" if plans else "NO_FEASIBLE_PLAN_FOUND",
                          reason="Safety-constrained FCFS baseline", plan=plans[0] if plans else None,
                          checks=validate_plan(snapshot, plans[0]) if plans else [], search_metadata=metadata)
