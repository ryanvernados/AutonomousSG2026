"""Deterministic model checks shared by baseline, fallback, agents and authorization.

These are declared simulator constraints, not operational aviation certification.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import combinations

from backend.contracts import Assignment, CheckResult, Plan, Reservation, Snapshot, plan_hash


def overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def feasible(checks: list[CheckResult]) -> bool:
    return bool(checks) and all(c.status == "PASS" for c in checks if c.severity == "HARD")


def reservations_for(snapshot: Snapshot, assignment: Assignment) -> list[Reservation]:
    """Taxi intervals split equally in path order. Never silently omit invalid intervals."""
    a = assignment
    flight = next(f for f in snapshot.flights if f.flight_id == a.flight_id)
    category = flight.assumptions.get("wake_category")
    result = [Reservation(resource_id=a.runway_id, flight_id=a.flight_id, kind="RUNWAY",
                          starts_at_utc=a.runway_time_utc,
                          ends_at_utc=a.runway_time_utc + timedelta(
                              seconds=snapshot.rules_config.runway_occupancy_seconds),
                          wake_category=str(category.value) if category else None)]
    if a.taxi_path_ids:
        duration = (a.taxi_end_utc - a.taxi_start_utc) / len(a.taxi_path_ids)
        for i, segment in enumerate(a.taxi_path_ids):
            result.append(Reservation(resource_id=segment, flight_id=a.flight_id, kind="TAXIWAY",
                                      starts_at_utc=a.taxi_start_utc + duration * i,
                                      ends_at_utc=a.taxi_start_utc + duration * (i + 1)))
    if a.gate_id and a.gate_start_utc and a.gate_end_utc:
        result.append(Reservation(resource_id=a.gate_id, flight_id=a.flight_id, kind="GATE",
                                  starts_at_utc=a.gate_start_utc, ends_at_utc=a.gate_end_utc))
    for kind, intervals in (("CREW", a.crew_reservations), ("EQUIPMENT", a.equipment_reservations)):
        result.extend(Reservation(**i.model_dump(), flight_id=a.flight_id, kind=kind) for i in intervals)
    return result


def instruction_issues(snapshot: Snapshot, instruction) -> list[str]:
    resources = {r.resource_id: r for r in snapshot.infrastructure.resources}
    expected_kind = {"RUNWAY_UNAVAILABLE": "RUNWAY", "TAXIWAY_BLOCKED": "TAXIWAY",
                     "EQUIPMENT_FAILURE": "EQUIPMENT", "PROTECTED_SLOT": "RUNWAY"}
    issues = []
    if instruction.scenario_id != snapshot.scenario_id:
        issues.append("Wrong scenario")
    if instruction.base_state_version != snapshot.state_version:
        issues.append("Stale instruction state version")
    resource = resources.get(instruction.resource_id)
    if not resource or resource.kind != expected_kind[instruction.kind]:
        issues.append("Missing resource or wrong resource kind")
    if instruction.kind == "PROTECTED_SLOT":
        if instruction.flight_id not in {f.flight_id for f in snapshot.flights}:
            issues.append("Protected slot requires a known flight")
        if instruction.ends_at_utc is None:
            issues.append("Protected slot requires an end time")
    return issues


def validate_plan(snapshot: Snapshot, plan: Plan, *, include_passes: bool = True) -> list[CheckResult]:
    """All checks for the plan. include_passes=False returns only non-PASS checks; the search uses
    it to decide feasibility without building ~10^3 PASS records per placement attempt."""
    if not all((snapshot.infrastructure, snapshot.rules_config, snapshot.start_utc,
                snapshot.end_utc, snapshot.airport_iata)):
        return [CheckResult(constraint_id="snapshot.context", domain="PLATFORM", status="UNKNOWN",
                            affected_ids=[snapshot.scenario_id],
                            explanation="Infrastructure, rules, UTC horizon and airport context required")]
    results: list[CheckResult] = []

    def number(value):
        # Same coercion full validation applies to float | str | None (ints become floats).
        return float(value) if isinstance(value, int) and not isinstance(value, bool) else value

    def check(cid, domain, passed, ids, explanation, measured=None, limit=None, unit=None):
        status = "UNKNOWN" if passed is None else "PASS" if passed else "FAIL"
        if status == "PASS" and not include_passes:
            return
        # Values are generated here from typed models, so skip re-validating ~10^6 records per search.
        results.append(CheckResult.model_construct(
            constraint_id=cid, domain=domain, severity="HARD",
            status=status,
            affected_ids=list(ids), explanation=explanation, measured_value=number(measured),
            limit_value=number(limit), unit=unit))

    check("plan.identity", "PLATFORM", plan_hash(plan) == plan.plan_hash, [plan.proposal_id],
          "Canonical SHA-256 content identity")
    check("plan.versions", "PLATFORM", plan.scenario_id == snapshot.scenario_id
          and plan.base_state_version == snapshot.state_version
          and plan.instruction_version == snapshot.instruction_version, [plan.proposal_id],
          "Plan must reference current scenario, state and instruction versions")
    resources = {r.resource_id: r for r in snapshot.infrastructure.resources}
    flights = {f.flight_id: f for f in snapshot.flights}
    ids = [a.flight_id for a in plan.assignments]
    check("plan.assignments", "PLATFORM", bool(ids) and len(ids) == len(set(ids))
          and set(ids) <= flights.keys(), ids, "Unique assignments for known flights")
    # Preserve started commitments and initial occupancy even for reassigned flights.
    replaced = set(ids)
    occupied = [r for r in snapshot.resource_reservations
                if r.flight_id not in replaced or r.starts_at_utc < snapshot.sim_time_utc]
    for a in plan.assignments:
        f = flights.get(a.flight_id)
        if not f:
            continue
        aid = [a.flight_id]
        category = f.assumptions.get("wake_category")
        check("flight.category", "ATC", category is not None or None, aid,
              "Wake category must be declared with provenance")
        check("flight.operation", "ATC", a.operation == (
            "ARRIVAL" if f.destination == snapshot.airport_iata else "DEPARTURE"), aid,
            "Movement direction must match airport flight leg")
        check("flight.not_completed", "PLATFORM", f.state not in {"DEPARTED", "COMPLETED"}, aid,
              "Completed movements cannot be rescheduled")
        has_started = any(r.flight_id == f.flight_id and r.starts_at_utc < snapshot.sim_time_utc
                          and r.ends_at_utc > snapshot.sim_time_utc for r in snapshot.resource_reservations)
        check("flight.not_in_progress", "PLATFORM", not has_started or f.plan == a, aid,
              "An in-progress resource commitment cannot be replaced")
        valid_taxi = a.taxi_end_utc > a.taxi_start_utc and bool(a.taxi_path_ids)
        check("taxi.interval", "GROUND", valid_taxi, aid, "Nonempty positive taxi route required")
        check("flight.future", "ATC", min(a.runway_time_utc, a.taxi_start_utc) >= snapshot.sim_time_utc,
              aid, "New movements must not precede the frozen clock")
        valid_gate = bool(a.gate_id and a.gate_start_utc and a.gate_end_utc
                          and a.gate_end_utc > a.gate_start_utc)
        check("gate.interval", "GROUND", valid_gate, aid, "Gate occupancy requires a bounded interval")
        if valid_gate:
            gate = resources.get(a.gate_id)
            check("gate.compatibility", "GROUND", (
                str(category.value) in gate.compatible_categories if category and gate else None),
                aid + [a.gate_id], "Gate must support the declared aircraft category")
            if a.operation == "ARRIVAL":
                check("gate.turnaround", "GROUND", a.gate_start_utc == a.taxi_end_utc and
                      (a.gate_end_utc - a.gate_start_utc).total_seconds() >=
                      snapshot.rules_config.minimum_turnaround_seconds, aid,
                      "Arrival gate remains reserved throughout minimum turnaround")
            else:
                check("gate.departure", "GROUND", a.gate_end_utc == a.taxi_start_utc
                      and a.taxi_start_utc >= f.scheduled_gate_departure_utc, aid,
                      "Departure gate remains occupied until scheduled-or-later pushback")
        runway_clear = a.runway_time_utc + timedelta(seconds=snapshot.rules_config.runway_occupancy_seconds)
        check("movement.order", "GROUND", a.taxi_start_utc >= runway_clear if a.operation == "ARRIVAL"
              else a.taxi_end_utc <= a.runway_time_utc, aid,
              "Arrival runway precedes taxi; departure taxi precedes runway")
        # Graph edges must join the runway node and the chosen gate node in traversal order.
        current = a.runway_id if a.operation == "ARRIVAL" else a.gate_id
        connected = bool(current)
        min_traversal = 0
        for segment_id in a.taxi_path_ids:
            edge = resources.get(segment_id)
            if edge is None or edge.kind != "TAXIWAY":
                connected = False
                break
            min_traversal += edge.minimum_traversal_seconds
            if edge.node_from == current:
                current = edge.node_to
            elif edge.bidirectional and edge.node_to == current:
                current = edge.node_from
            else:
                connected = False
                break
        check("taxi.connectivity", "GROUND", connected and current == (
            a.gate_id if a.operation == "ARRIVAL" else a.runway_id), aid,
            "Taxi path must connect assigned runway and gate")
        # Equal segment timing must satisfy each segment's traversal minimum.
        per_segment = (a.taxi_end_utc - a.taxi_start_utc).total_seconds() / max(1, len(a.taxi_path_ids))
        check("taxi.traversal", "GROUND", all(per_segment >= resources[s].minimum_traversal_seconds
              for s in a.taxi_path_ids if s in resources), aid,
              "Each segment must receive its configured traversal time", per_segment, None, "seconds")
        if a.operation == "ARRIVAL":
            fuel = f.assumptions.get("latest_safe_arrival_utc")
            try:
                latest = datetime.fromisoformat(str(fuel.value).replace("Z", "+00:00")) if fuel else None
                if latest is not None and latest.tzinfo is None:
                    latest = None
            except (ValueError, TypeError):
                latest = None
            check("fuel.latest_safe_arrival", "PILOT", a.runway_time_utc <= latest if latest else None,
                  aid, "Arrival must meet the declared UTC latest safe arrival; missing model blocks approval")
        check("crew.required", "PILOT", bool(a.crew_reservations), aid, "At least one crew reservation required")
        required_crew = f.assumptions.get("crew_id")
        check("crew.identity", "PILOT", any(i.resource_id == required_crew.value for i in a.crew_reservations)
              if required_crew else None, aid, "Assigned crew must match declared flight crew")
        for i in a.crew_reservations:
            check("crew.coverage", "PILOT", i.starts_at_utc <= min(a.taxi_start_utc, a.runway_time_utc)
                  and i.ends_at_utc >= max(a.taxi_end_utc, runway_clear), aid + [i.resource_id],
                  "Crew reservation covers the complete modelled movement")
        required_equipment = f.assumptions.get("equipment_id")
        check("equipment.required", "GROUND", any(i.resource_id == required_equipment.value
              for i in a.equipment_reservations) if required_equipment else None, aid,
              "Declared equipment is reserved for the complete ground service")
        for i in a.equipment_reservations:
            check("equipment.service", "GROUND", i.starts_at_utc <= a.taxi_start_utc
                  and i.ends_at_utc >= a.taxi_end_utc, aid + [i.resource_id],
                  "Equipment covers the modelled taxi/service interval")
        if valid_taxi and valid_gate:
            occupied.extend(reservations_for(snapshot, a))
    # Validate the ENTIRE schedule including nonparticipant flights and occupancy buffers.
    for r in occupied:
        resource = resources.get(r.resource_id)
        check("resource.exists", r.kind, resource is not None and resource.kind == r.kind,
              [r.flight_id, r.resource_id], "Reservation must reference a resource of the correct kind")
        if not resource:
            continue
        available = (not resource.available_from_utc or r.starts_at_utc >= resource.available_from_utc) and (
            not resource.available_until_utc or r.ends_at_utc <= resource.available_until_utc)
        check("resource.availability", r.kind, available, [r.flight_id, r.resource_id],
              "Reservation must fit the resource availability/duty window")
        if r.kind == "CREW":
            check("crew.duty_data", "PILOT", True if resource.available_from_utc
                  and resource.available_until_utc and resource.max_work_seconds else None,
                  [r.flight_id, r.resource_id], "Crew availability and work limit are required")
        for instruction in snapshot.active_instructions:
            if instruction.kind == "PROTECTED_SLOT":
                if r.kind == "RUNWAY" and r.flight_id == instruction.flight_id:
                    check("instruction.protected_flight", "ATC", r.resource_id == instruction.resource_id
                          and instruction.starts_at_utc <= r.starts_at_utc < instruction.ends_at_utc,
                          [r.flight_id, instruction.instruction_id], "Protected flight retains its runway slot")
                blocked = r.kind == "RUNWAY" and r.flight_id != instruction.flight_id
            else:
                blocked = True
            if blocked and r.resource_id == instruction.resource_id:
                check("instruction.restriction", r.kind, not overlap(
                    r.starts_at_utc, r.ends_at_utc, instruction.starts_at_utc,
                    instruction.ends_at_utc or datetime.max.replace(tzinfo=r.starts_at_utc.tzinfo)),
                    [r.flight_id, r.resource_id, instruction.instruction_id], "Confirmed restriction must hold")
    by_resource = defaultdict(list)
    for r in occupied:
        by_resource[r.resource_id].append(r)
    for rid, resource in resources.items():
        intervals = by_resource.get(rid, [])
        # End events sort before starts: touching intervals do not overlap.
        capacity_intervals = [(r.starts_at_utc, r.ends_at_utc, r.quantity) for r in intervals]
        if resource.kind == "GATE":
            # Arrival and next departure may describe the SAME aircraft's continuous gate stay.
            by_aircraft = {}
            for r in intervals:
                flight = flights.get(r.flight_id)
                key = flight.tail_number if flight and flight.tail_number else r.flight_id
                by_aircraft.setdefault(key, []).append(r)
            capacity_intervals = []
            for group in by_aircraft.values():
                merged = []
                for r in sorted(group, key=lambda x: x.starts_at_utc):
                    if merged and r.starts_at_utc <= merged[-1][1] and r.quantity == merged[-1][2]:
                        merged[-1][1] = max(merged[-1][1], r.ends_at_utc)
                    else:
                        merged.append([r.starts_at_utc, r.ends_at_utc, r.quantity])
                capacity_intervals.extend(merged)
        points = sorted([(start, quantity) for start, _, quantity in capacity_intervals] +
                        [(end, -quantity) for _, end, quantity in capacity_intervals])
        load = peak = 0
        for _, delta in points:
            load += delta
            peak = max(peak, load)
        check("resource.capacity", resource.kind, peak <= resource.capacity,
              [rid] + sorted({r.flight_id for r in intervals}), "Concurrent demand must fit configured capacity",
              peak, resource.capacity, "units")
        if resource.kind == "CREW" and intervals:
            # Union duration avoids double-counting overlapping intervals (capacity still rejects overlaps).
            merged = []
            for r in sorted(intervals, key=lambda x: x.starts_at_utc):
                if merged and r.starts_at_utc <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], r.ends_at_utc)
                else:
                    merged.append([r.starts_at_utc, r.ends_at_utc])
            work = sum((end - start).total_seconds() for start, end in merged)
            check("crew.work_limit", "PILOT", work <= resource.max_work_seconds
                  if resource.max_work_seconds else None, [rid], "Cumulative crew work must remain in limit",
                  work, resource.max_work_seconds, "seconds")
    runways = [r for r in occupied if r.kind == "RUNWAY"]
    for first, second in combinations(runways, 2):
        left, right = sorted((first, second), key=lambda x: x.starts_at_utc)
        lres, rres = resources.get(left.resource_id), resources.get(right.resource_id)
        dependent = left.resource_id == right.resource_id or (lres and rres and (
            right.resource_id in lres.dependent_runway_ids or left.resource_id in rres.dependent_runway_ids))
        pair_ids = [left.flight_id, right.flight_id]
        if not dependent:
            # Intersecting runways are not independent capacity: the intersection must be clear,
            # so their occupancy intervals may not overlap. Wake pairs apply only to the same or
            # dependent runways.
            if lres and rres and (right.resource_id in lres.intersecting_runway_ids
                                  or left.resource_id in rres.intersecting_runway_ids):
                check("runway.intersection", "ATC", not overlap(left.starts_at_utc, left.ends_at_utc,
                      right.starts_at_utc, right.ends_at_utc), pair_ids,
                      "Intersecting runways cannot be occupied simultaneously")
            continue
        check("runway.intersection", "ATC", not overlap(left.starts_at_utc, left.ends_at_utc,
              right.starts_at_utc, right.ends_at_utc), pair_ids,
              "Dependent runways cannot be occupied simultaneously")
        categories = f"{left.wake_category}:{right.wake_category}"
        separation = snapshot.rules_config.wake_separation_seconds.get(categories)
        actual = (right.starts_at_utc - left.starts_at_utc).total_seconds()
        check("runway.wake", "ATC", actual >= separation if separation is not None else None, pair_ids,
              "Configured category-pair wake separation on same or dependent runways", actual, separation, "seconds")
    # Aircraft rotation and crew coupling are checked across assigned and untouched flight plans.
    assignments = {f.flight_id: f.plan for f in snapshot.flights if f.plan}
    assignments.update({a.flight_id: a for a in plan.assignments})
    for instruction in snapshot.active_instructions:
        if instruction.kind == "PROTECTED_SLOT":
            check("instruction.protected_presence", "ATC", True if instruction.flight_id in assignments else None,
                  [instruction.flight_id], "A protected flight must have a published or proposed assignment")
    rotations = {}
    for f in snapshot.flights:
        if f.tail_number and f.flight_id in assignments:
            rotations.setdefault(f.tail_number, []).append(f)
    for legs in rotations.values():
        # Only adjacent legs form a rotation edge; an aircraft can make several trips in one window.
        legs.sort(key=lambda f: (f.scheduled_gate_arrival_utc if f.destination == snapshot.airport_iata
                                  else f.scheduled_gate_departure_utc, f.flight_id))
        for f, g in zip(legs, legs[1:]):
            a, b = assignments[f.flight_id], assignments[g.flight_id]
            if a.operation == "ARRIVAL" and b.operation == "DEPARTURE":
                check("rotation.turnaround", "GROUND", a.taxi_end_utc + timedelta(
                    seconds=snapshot.rules_config.minimum_turnaround_seconds) <= b.taxi_start_utc,
                    [f.flight_id, g.flight_id], "Same-tail departure must follow arrival turnaround")
                check("rotation.gate_continuity", "GROUND", a.gate_id == b.gate_id and
                      a.gate_start_utc <= b.gate_start_utc <= a.gate_end_utc and
                      max(a.gate_end_utc, b.gate_end_utc) >= b.taxi_start_utc,
                      [f.flight_id, g.flight_id], "Same aircraft retains continuous gate occupancy until departure")
            else:
                check("rotation.sequence", "GROUND", a.runway_time_utc < b.runway_time_utc,
                      [f.flight_id, g.flight_id], "Same-tail movement order must match source leg sequence")
    return results
