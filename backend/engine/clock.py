from datetime import timedelta

import simpy

from backend.contracts import Snapshot


def advance_clock(snapshot: Snapshot, seconds: int) -> Snapshot:
    """Reconstruct deterministic SimPy time from the persisted checkpoint (unit: seconds)."""
    if snapshot.start_utc is None or snapshot.end_utc is None:
        raise ValueError("Authoritative UTC horizon required for clock advancement")
    remaining = max(0, int((snapshot.end_utc - snapshot.sim_time_utc).total_seconds()))
    delta = min(seconds, remaining)
    env = simpy.Environment(initial_time=(snapshot.sim_time_utc - snapshot.start_utc).total_seconds())
    if delta:
        env.run(until=env.now + delta)
    now = snapshot.start_utc + timedelta(seconds=env.now)
    flights = []
    for f in snapshot.flights:
        a = f.plan
        if not a:
            state = "HOLDING" if f.destination == snapshot.airport_iata and now >= (
                f.scheduled_gate_arrival_utc) else "SCHEDULED"
            flights.append(f.model_copy(update={"state": state}))
            continue
        updates = {}
        if a.operation == "ARRIVAL":
            ready = f.scheduled_gate_arrival_utc - (a.taxi_end_utc - a.runway_time_utc)
            waiting_state = "HOLDING" if now >= ready else "SCHEDULED"
            state = waiting_state if now < a.runway_time_utc else "TAXIING" if now < a.taxi_end_utc else (
                "TURNAROUND" if now < a.gate_end_utc else "COMPLETED")
            if now >= a.runway_time_utc:
                updates["wheels_on_utc"] = a.runway_time_utc
            if now >= a.taxi_end_utc:
                updates["actual_gate_arrival_utc"] = a.taxi_end_utc
        else:
            state = "SCHEDULED" if now < a.gate_start_utc else "AT_GATE" if now < a.taxi_start_utc else (
                "TAXIING" if now < a.runway_time_utc else "DEPARTED")
            if now >= a.taxi_start_utc:
                updates["actual_gate_departure_utc"] = a.taxi_start_utc
            if now >= a.runway_time_utc:
                updates["wheels_off_utc"] = a.runway_time_utc
        flights.append(f.model_copy(update={"state": state, **updates}))
    return snapshot.model_copy(update={"sim_time_utc": now, "flights": flights,
                                      "state_version": snapshot.state_version + 1,
                                      "clock_status": "PAUSED" if now >= snapshot.end_utc
                                      else snapshot.clock_status})
