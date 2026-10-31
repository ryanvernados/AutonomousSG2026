"""Explicit synthetic engine double for protocol development ONLY.

This implements a few fixture checks, not Member 1's complete airport validator.
Every operational value below is SYNTHETIC. No historical outcome is inferred.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from .protocol import clone, offers_satisfied, utc


def mock_plan_hash(plan):
    return hashlib.sha256(json.dumps({k: v for k, v in plan.items() if k != "plan_hash"},
                                    sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def stamp(minutes):
    return (datetime(2015, 1, 1, 16, tzinfo=timezone.utc) + timedelta(minutes=minutes)).isoformat()


def window(kind, subject, resource, start, end):
    return {"kind": kind, "subject_id": subject, "resource_id": resource,
            "starts_at_utc": stamp(start) if start is not None else None,
            "ends_at_utc": stamp(end), "unit": "UTC", "source_type": "SYNTHETIC"}


class MockEngine:
    mode = "MOCK"

    def __init__(self, *, failure=None, now=None):
        self.failure = failure
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.fallback_calls = 0
        self.validation_schedule_sizes = []
        self.candidate_constraints = []
        self.snapshot = {
            "schema_version": 1, "scenario_id": "synthetic-sfo-member3-v1", "state_version": 1,
            "sim_time_utc": stamp(0), "mode": "ASSISTED",
            "flights": [self._flight("DEPART-1", "DEPARTURE"), self._flight("ARRIVE-1", "ARRIVAL"),
                        self._flight("UNAFFECTED-1", "DEPARTURE")],
            "resource_reservations": [{"flight_id": "UNAFFECTED-1", "resource_id": "R-SIM", "starts_at_utc": stamp(30), "ends_at_utc": stamp(34)}],
            "active_instructions": [{"instruction_id": "closure-1", "instruction_version": 1,
                                     "scenario_id": "synthetic-sfo-member3-v1", "base_state_version": 0,
                                     "kind": "RUNWAY_UNAVAILABLE", "resource_id": "R-SIM",
                                     "starts_at_utc": stamp(0), "ends_at_utc": stamp(10),
                                     "flight_id": None, "parameters": {"source_type": "SYNTHETIC"}, "entered_by": "demo-staff"}],
            "data_quality": {"clock_paused": True, "engine_mode": "MOCK", "historical_traffic": False,
                             "limitations": ["Three synthetic flights; simplified fixture rules; no sourced SFO layout."]},
        }

    @staticmethod
    def _flight(flight_id, operation):
        return {"flight_id": flight_id, "source_row_id": f"synthetic:{flight_id}", "airline": "SIM",
                "flight_number": flight_id, "tail_number": None,
                "origin": "SFO" if operation == "DEPARTURE" else "SIM",
                "destination": "SFO" if operation == "ARRIVAL" else "SIM",
                "scheduled_gate_departure_utc": stamp(-10), "scheduled_gate_arrival_utc": stamp(15),
                "state": "QUEUED", "plan": None,
                "assumptions": {"operation": operation, "source_type": "SYNTHETIC"}}

    async def get_snapshot(self, session_id):
        return clone(self.snapshot)

    async def dependencies(self, snapshot, flight_id):
        # Shared runway dependency. Member 1 must include taxi/gate/crew/rotation too.
        return ["ARRIVE-1", "DEPART-1"] if flight_id in ("ARRIVE-1", "DEPART-1") else []

    async def domain_view(self, snapshot, role, flight_id, affected_ids):
        if role == "ATC":
            return {"authorized_windows": [window("RUNWAY_WINDOW", f, "R-SIM", 10, 25) for f in affected_ids],
                    "separation": {"value": 4, "unit": "minutes", "source_type": "SYNTHETIC"}}
        if role == "GROUND":
            return {"authorized_windows": [window("GROUND_WINDOW", f, "G-SIM-1", 0, 45) for f in affected_ids],
                    "resource_windows": {"taxi_path": ["T-SIM-IN", "T-SIM-OUT"], "crew": "C-SIM", "equipment": "E-SIM"}}
        operation = next(f["assumptions"]["operation"] for f in snapshot["flights"] if f["flight_id"] == flight_id)
        windows = [window("TIMING_WINDOW", flight_id, None, 0, 25)]
        if operation == "ARRIVAL" and self.failure != "unknown_fuel":
            windows.append(window("LATEST_SAFE_ARRIVAL", flight_id, None, None, 12))
        return {"flight_id": flight_id, "operation": operation, "authorized_windows": windows,
                "fuel_model": {"source_type": "SYNTHETIC", "unit": "UTC", "latest_safe_arrival_utc": stamp(12) if self.failure != "unknown_fuel" else None}}

    def plan(self, name, arrival=10, departure=14):
        def assignment(flight_id, operation, minute):
            return {"flight_id": flight_id, "operation": operation, "runway_id": "R-SIM",
                    "runway_time_utc": stamp(minute), "taxi_path_ids": ["T-SIM-IN" if operation == "ARRIVAL" else "T-SIM-OUT"],
                    "taxi_start_utc": stamp(minute + 1 if operation == "ARRIVAL" else minute - 4),
                    "taxi_end_utc": stamp(minute + 4 if operation == "ARRIVAL" else minute - 1),
                    "gate_id": "G-SIM-1", "gate_start_utc": stamp(minute + 4 if operation == "ARRIVAL" else 0),
                    "gate_end_utc": stamp(45 if operation == "ARRIVAL" else minute - 4),
                    "crew_reservations": [], "equipment_reservations": []}
        plan = {"proposal_id": name, "proposal_version": 1, "scenario_id": self.snapshot["scenario_id"],
                "base_state_version": self.snapshot["state_version"], "instruction_version": 1, "plan_hash": "",
                "assignments": [assignment("ARRIVE-1", "ARRIVAL", arrival), assignment("DEPART-1", "DEPARTURE", departure)],
                "impact": {}, "expires_at_utc": (self.now() + timedelta(minutes=5)).isoformat()}
        plan["plan_hash"] = self.plan_hash(plan)
        return plan

    @staticmethod
    def plan_hash(plan):
        return mock_plan_hash(plan)

    async def enumerate_candidates(self, snapshot, constraints, limit=100):
        self.candidate_constraints = clone(constraints)
        plans = [self.plan("fcfs-candidate", arrival=14, departure=10), self.plan("fuel-priority-candidate")]
        if self.failure == "no_candidates":
            plans = []
        if self.failure == "unaffected_conflict":
            plans = [self.plan("conflict-candidate", departure=30)]
        # Candidate search must obey all offers, not just operational hard checks.
        accepted = []
        for plan in plans:
            if offers_satisfied(plan, constraints):
                accepted.append(plan)
        return {"plans": accepted[:limit], "limit": limit, "search_exhausted": len(accepted) <= limit}

    async def validate_plan(self, snapshot, plan):
        self.validation_schedule_sizes.append(len(snapshot["flights"]))
        checks = []

        def check(name, ok, domain="ATC", unknown=False):
            checks.append({"constraint_id": name, "domain": domain, "severity": "HARD",
                           "status": "UNKNOWN" if unknown else "PASS" if ok else "FAIL",
                           "affected_ids": [a["flight_id"] for a in plan["assignments"]],
                           "measured_value": None, "limit_value": None, "unit": None,
                           "explanation": "Synthetic mock fixture check; not a complete operational validator."})

        assignments = plan["assignments"]
        slots = sorted(utc(a["runway_time_utc"]) for a in assignments)
        check("mock:runway-closure", all(t >= utc(stamp(10)) for t in slots))
        check("mock:separation", all((b - a).total_seconds() >= 240 for a, b in zip(slots, slots[1:])))
        check("mock:nonparticipant-occupancy", all(abs((t - utc(r["starts_at_utc"])).total_seconds()) >= 240
              for t in slots for r in snapshot["resource_reservations"]))
        check("mock:latest-safe-arrival", all(utc(a["runway_time_utc"]) <= utc(stamp(12))
              for a in assignments if a["operation"] == "ARRIVAL"), "PILOT", self.failure == "unknown_fuel")
        taxi_ok = True
        for segment in {s for a in assignments for s in a["taxi_path_ids"]}:
            intervals = sorted((utc(a["taxi_start_utc"]), utc(a["taxi_end_utc"])) for a in assignments if segment in a["taxi_path_ids"])
            taxi_ok &= all(end <= next_start for (_, end), (next_start, _) in zip(intervals, intervals[1:]))
        check("mock:taxi-segment", taxi_ok, "GROUND")
        gates = sorted((utc(a["gate_start_utc"]), utc(a["gate_end_utc"])) for a in assignments)
        check("mock:gate-occupancy", all(end <= next_start for (_, end), (next_start, _) in zip(gates, gates[1:])), "GROUND")
        check("mock:ground-availability", self.failure != "ground_unavailable", "GROUND")
        return checks

    async def compute_impact(self, snapshot, plan, baseline_plan):
        current = {a["flight_id"]: utc(a["runway_time_utc"]) for a in plan["assignments"]}
        baseline = {a["flight_id"]: utc(a["runway_time_utc"]) for a in baseline_plan["assignments"]}
        deltas = {f: (t - baseline[f]).total_seconds() / 60 for f, t in current.items()}
        return {"total_runway_wait_minutes": sum((t - utc(stamp(0))).total_seconds() / 60 for t in current.values()),
                "max_runway_wait_minutes": max((t - utc(stamp(0))).total_seconds() / 60 for t in current.values()),
                "runway_delta_minutes_by_flight": deltas, "source_type": "SYNTHETIC",
                "explanation": "DEPART-1 yields four minutes; ARRIVE-1 lands four minutes earlier within its synthetic latest-arrival limit. Runway deltas are not gate-delay or fuel savings metrics."}

    async def select_fallback(self, snapshot, reason):
        self.fallback_calls += 1
        plan = None if self.failure == "no_candidates" else self.plan("fallback-fuel-priority")
        if plan:
            plan["impact"] = await self.compute_impact(snapshot, plan, self.plan("baseline", arrival=14, departure=10))
            plan["plan_hash"] = self.plan_hash(plan)
        return {"plan": plan, "search_exhausted": True, "limit": 100}

