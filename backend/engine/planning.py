"""Bounded constructive search. Finding no candidate is not an infeasibility proof."""
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
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
    # Adjacency built once per call in resource-ID order, so expansion order (and therefore the
    # routes found) matches scanning every sorted edge at each step.
    adjacency = defaultdict(list)
    for edge in sorted((r for r in snapshot.infrastructure.resources if r.kind == "TAXIWAY"),
                       key=lambda x: x.resource_id):
        adjacency[edge.node_from].append((edge.resource_id, edge.node_to))
        if edge.bidirectional and edge.node_to != edge.node_from:
            adjacency[edge.node_to].append((edge.resource_id, edge.node_from))
    queue = deque([(start, [], {start})])
    found = []
    while queue and len(found) < max_paths:
        node, path, seen = queue.popleft()
        if node == end:
            found.append(path)
            continue
        for edge_id, nxt in adjacency.get(node, ()):
            if nxt and nxt not in seen:
                queue.append((nxt, path + [edge_id], seen | {nxt}))
    return found


@dataclass(frozen=True)
class FlightRequest:
    """A coordinator agent's request for one flight. The search honours it or places nothing.

    runway_ids limits runway choice; earliest/latest bound the runway time (UTC); gate_id
    replaces the scenario's gate. Requests never bypass a hard check: every placement is still
    validated, so an unsafe request simply yields no candidate.
    """
    runway_ids: tuple[str, ...] = ()
    earliest_utc: datetime | None = None
    latest_utc: datetime | None = None
    gate_id: str | None = None


def _assignment(snapshot, flight, runway, route, slot, gate_id=None, gate_start=None):
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
    # A turnaround departure takes over the gate exactly when its inbound arrival's occupancy ends.
    gate_start = taxi_end if arrival else gate_start or max(snapshot.sim_time_utc,
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
                      gate_id=gate_id or str(gate.value), gate_start_utc=gate_start, gate_end_utc=gate_end,
                      crew_reservations=[Interval(resource_id=str(crew.value), starts_at_utc=crew_start,
                                                  ends_at_utc=crew_end)],
                      equipment_reservations=[Interval(resource_id=str(equipment.value),
                                                       starts_at_utc=taxi_start, ends_at_utc=taxi_end)])


PLACEMENTS_PER_FLIGHT = 1
ALTERNATIVE_GATES = 4  # compatible gates tried, in ID order, when the scheduled gate cannot be used
GATE_HOLD = timedelta(minutes=15)  # delay accepted at the scheduled gate before alternatives are tried


def _gate_choices(snapshot, flight, scheduled_gate):
    """Scheduled gate first, then compatible alternatives (a blocked gate is the usual reason a
    delayed arrival's turnaround makes a later departure unplaceable)."""
    category = flight.assumptions.get("wake_category")
    alternatives = sorted(r.resource_id for r in snapshot.infrastructure.resources
                          if r.kind == "GATE" and r.resource_id != scheduled_gate
                          and (category is None or str(category.value) in r.compatible_categories))
    # Deterministic spread: start the alternatives near the scheduled gate's position.
    if alternatives:
        offset = sum(map(ord, scheduled_gate)) % len(alternatives)
        alternatives = alternatives[offset:] + alternatives[:offset]
    return [scheduled_gate] + alternatives[:ALTERNATIVE_GATES]


def _place(snapshot, flight, options, anchor, last, assignments, baseline, budget, attempted, rejected,
           gate_start=None):
    """Earliest validated slot for one flight over (runway, route, gate) options, anchor to last.

    Returns (assignment or None, placements attempted so far). Every attempt validates the
    partial plan's hard checks, so a returned assignment is safe alongside earlier placements.
    """
    offset = 0
    while attempted < budget and anchor + timedelta(seconds=offset) <= last:
        for runway, route, gate_id in options:
            if attempted >= budget:
                break
            candidate = _assignment(snapshot, flight, runway, route, anchor + timedelta(seconds=offset), gate_id,
                                    gate_start)
            attempted += 1
            if not candidate:
                rejected["flight.missing_assumptions"] += 1
                continue
            plan = seal_plan(Plan(scenario_id=snapshot.scenario_id,
                base_state_version=snapshot.state_version, instruction_version=snapshot.instruction_version,
                assignments=assignments + [candidate], source="BASELINE" if baseline else "FALLBACK",
                expires_at_utc=utcnow() + timedelta(seconds=snapshot.rules_config.proposal_validity_seconds)))
            # Failures only: accepted exactly when no hard check is FAIL or UNKNOWN,
            # the same decision feasible() makes on the full check list.
            failed = [c for c in validate_plan(snapshot, plan, include_passes=False) if c.severity == "HARD"]
            if not failed:
                return candidate, attempted
            rejected.update(c.constraint_id for c in failed)
        offset += snapshot.rules_config.search_step_seconds
    return None, attempted


def search_candidates(snapshot: Snapshot, constraints=None, limit=100, *, baseline=False, requests=None,
                      flight_ids=None):
    """Bounded search with two separate limits.

    ``limit`` (at most rules_config.search_limit and 100) caps complete candidate plans, as the
    shared specification requires. Placement work has its own deterministic budget of
    PLACEMENTS_PER_FLIGHT x plan limit x flights to place, counting every placement validation,
    so a full traffic slice can be placed at all. No wall-clock limit is used, so baseline and
    fallback runs stay reproducible. constraints is a typed Instruction list from Member 3's
    tools, never arbitrary executable input. Two stable orderings share the placement budget.
    The search is non-exhaustive. ``requests`` maps flight IDs to FlightRequest; when given,
    flights are sequenced by their requested times and placed only inside their requests.
    """
    requests = requests or {}
    if not all((snapshot.infrastructure, snapshot.rules_config, snapshot.start_utc,
                snapshot.end_utc, snapshot.airport_iata)):
        return [], {"attempted_placements": 0, "limit": min(max(int(limit), 1), 100),
                    "exhaustive": False, "limit_reached": False,
                    "rejection_counts": {"snapshot.context_unknown": 1}}
    if constraints:
        snapshot = snapshot.model_copy(update={"active_instructions": snapshot.active_instructions + list(constraints)})
    plan_limit = min(max(int(limit), 1), snapshot.rules_config.search_limit, 100)
    attempted = 0
    rejected = Counter()
    plans = []
    runways = sorted(r.resource_id for r in snapshot.infrastructure.resources if r.kind == "RUNWAY")
    # flight_ids limits replanning to an affected subset; every other published assignment stays
    # fixed and is validated alongside the new placements.
    flights = [f for f in snapshot.flights if f.state not in {"DEPARTED", "COMPLETED"}
               and (flight_ids is None or f.flight_id in flight_ids)
               and not any(r.flight_id == f.flight_id and r.starts_at_utc < snapshot.sim_time_utc
                           and r.ends_at_utc > snapshot.sim_time_utc for r in snapshot.resource_reservations)]
    budget = max(1, int(PLACEMENTS_PER_FLIGHT * plan_limit * max(1, len(flights))))

    def scheduled(f):
        return f.scheduled_gate_arrival_utc if f.destination == snapshot.airport_iata else f.scheduled_gate_departure_utc

    def urgency(f):
        value = f.assumptions.get("latest_safe_arrival_utc")
        try:
            deadline = datetime.fromisoformat(str(value.value).replace("Z", "+00:00")) if value else snapshot.end_utc
            return deadline if deadline.tzinfo else snapshot.end_utc
        except (ValueError, TypeError):
            return snapshot.end_utc

    # Same-tail rotations, matching validation: each departure's immediately preceding leg, when that
    # leg is an arrival here, is its inbound aircraft and fixes its gate and earliest pushback.
    tails = defaultdict(list)
    for f in snapshot.flights:
        if f.tail_number:
            tails[f.tail_number].append(f)
    inbound = {}
    for legs in tails.values():
        legs.sort(key=lambda f: (scheduled(f), f.flight_id))
        for prev, nxt in zip(legs, legs[1:]):
            if prev.destination == snapshot.airport_iata and nxt.origin == snapshot.airport_iata:
                inbound[nxt.flight_id] = prev.flight_id
    turnaround = timedelta(seconds=snapshot.rules_config.minimum_turnaround_seconds)
    published = {f.flight_id: f.plan for f in snapshot.flights if f.plan}

    # Fuel-urgent flights take precedence; otherwise first come first served.
    orderings = [sorted(flights, key=lambda f: (
        urgency(f) > snapshot.sim_time_utc + timedelta(minutes=30), scheduled(f), f.flight_id))]
    if requests:
        # The agents' sequence first: requested earliest time, else the schedule.
        orderings.insert(0, sorted(flights, key=lambda f: (
            (requests[f.flight_id].earliest_utc if f.flight_id in requests and requests[f.flight_id].earliest_utc
             else scheduled(f)), f.flight_id)))
    if not baseline:
        orderings.append(sorted(flights, key=lambda f: (urgency(f), scheduled(f), f.flight_id)))
    for ordered in orderings:
        if attempted >= budget:
            break
        assignments = []
        for flight in ordered:
            arrival = flight.destination == snapshot.airport_iata
            request = requests.get(flight.flight_id, FlightRequest())
            gate = flight.assumptions.get("gate_id")
            if gate is None:
                rejected["gate.missing_assumption"] += 1
                break
            # A gate the agents chose is binding; otherwise fall back to compatible alternatives.
            gate_ids = [request.gate_id] if request.gate_id else _gate_choices(snapshot, flight, str(gate.value))
            placed = {a.flight_id: a for a in assignments}
            prior = placed.get(inbound.get(flight.flight_id)) or published.get(inbound.get(flight.flight_id))
            hand_over = None
            if prior and prior.gate_id and prior.gate_end_utc:
                # The aircraft is already at its inbound gate: stay there, push after turnaround.
                gate_ids, hand_over = [prior.gate_id], prior.gate_end_utc
            usable = [r for r in runways if not request.runway_ids or r in request.runway_ids]
            earliest = snapshot.sim_time_utc + timedelta(minutes=5)
            anchor = max(earliest, request.earliest_utc) if request.earliest_utc else max(
                earliest, scheduled(flight) + (timedelta(minutes=-4) if arrival else timedelta(minutes=3)))
            last = min(snapshot.end_utc, request.latest_utc) if request.latest_utc else snapshot.end_utc
            if hand_over:
                anchor = max(anchor, prior.taxi_end_utc + turnaround)
            options = {g: [(r, path, g) for r in usable for path in _paths(snapshot,
                r if arrival else g, g if arrival else r)] for g in gate_ids}
            # Scheduled gate first for a short delay; beyond that, the earliest slot at any choice.
            phases = [(options[gate_ids[0]], min(last, anchor + GATE_HOLD))]
            if len(gate_ids) > 1:
                phases.append(([o for g in gate_ids for o in options[g]], last))
            chosen = None
            for opts, until in phases:
                if not opts:
                    rejected["taxi.no_connected_route"] += 1
                    continue
                chosen, attempted = _place(snapshot, flight, opts, anchor, until, assignments,
                                           baseline, budget, attempted, rejected, hand_over)
                if chosen:
                    break
            if chosen is None:
                if flight.flight_id in requests:
                    rejected["request.unplaceable:" + flight.flight_id] += 1
                break
            assignments.append(chosen)
        if assignments and len(assignments) == len(ordered):
            plan = seal_plan(Plan(scenario_id=snapshot.scenario_id,
                base_state_version=snapshot.state_version, instruction_version=snapshot.instruction_version,
                assignments=assignments, source="BASELINE" if baseline else "FALLBACK",
                expires_at_utc=utcnow() + timedelta(seconds=snapshot.rules_config.proposal_validity_seconds)))
            plan = seal_plan(plan.model_copy(update={"impact": compute_impact(snapshot, plan)}))
            if feasible(validate_plan(snapshot, plan)) and len(plans) < plan_limit:
                plans.append(plan)
    return plans, {"attempted_placements": attempted, "limit": plan_limit, "placement_budget": budget,
                   "exhaustive": False, "limit_reached": attempted >= budget,
                   "rejection_counts": dict(rejected),
                   "objective_weights": snapshot.rules_config.objective_weights,
                   "requests": len(requests),
                   "policy": "AGENT_REQUESTS" if requests else
                   "FUEL_URGENT_THEN_FCFS" if baseline else "BOUNDED_STABLE_SEARCH"}


def enumerate_candidates(snapshot: Snapshot, constraints=None, limit=100) -> list[Plan]:
    return search_candidates(snapshot, constraints, limit)[0]


def select_fallback(snapshot: Snapshot, reason: str, flight_ids=None) -> FallbackResult:
    plans, metadata = search_candidates(snapshot, flight_ids=flight_ids)
    selected = min(plans, key=lambda p: (p.impact.score, tuple(a.flight_id for a in p.assignments))) if plans else None
    return FallbackResult(status="AWAITING_AUTHORIZATION" if selected else "NO_FEASIBLE_PLAN_FOUND",
                          reason=reason, plan=selected, checks=validate_plan(snapshot, selected) if selected else [],
                          search_metadata=metadata)


def baseline_plan(snapshot: Snapshot) -> FallbackResult:
    plans, metadata = search_candidates(snapshot, baseline=True)
    return FallbackResult(status="AWAITING_AUTHORIZATION" if plans else "NO_FEASIBLE_PLAN_FOUND",
                          reason="Safety-constrained FCFS baseline", plan=plans[0] if plans else None,
                          checks=validate_plan(snapshot, plans[0]) if plans else [], search_metadata=metadata)
