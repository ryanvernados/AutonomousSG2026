"""Bridge Member 3's two-round coordinator to Member 1's platform.

Member 1 calls ``coordinate(snapshot, affected_ids, negotiation_id)`` and expects a
``CoordinationOutcome``. Member 3's ``Coordinator.run`` talks to an ``EnginePort`` over JSON
dicts. This module implements that port with Member 1's public engine functions and maps the
result back:

* snapshot: canonical schema_version "1" becomes the integer 1 Member 3 checks, and
  ``clock_status == "PAUSED"`` becomes the ``data_quality.clock_paused`` attestation
* agent IDs: ``atc``/``ground``/``pilot:<id>`` become ``ATC``/``GROUND``/``PILOT:<id>``
* reasons: structured ``{code, detail, affected_ids}`` objects become strings
* objective: ``{"weights": {"score": 1}}`` ranks by ``Impact.score``, which already applies
  Member 1's fixed objective weights
* plans: candidates are marked ``source="CONSENSUS"`` before the coordinator hashes them,
  because changing the source after hashing would change the plan identity

Authorized windows: agents may only narrow windows the engine authorizes. The engine has
no window concept, so the adapter derives them from the engine's own deterministic
candidate for the frozen snapshot: ATC gets that runway per flight, pilots the horizon and
their declared latest safe arrival, ground the gate over the horizon. Engine search is
synchronous, so it runs in a worker thread to keep the API event loop responsive.
"""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from backend.agents.adapters import MockAdapter, ModelAdapter
from backend.api.coordinator import CoordinationOutcome
from backend.contracts import AgentMessage, Plan, Snapshot, plan_hash
from backend.engine.planning import baseline_plan, compute_impact, search_candidates, select_fallback
from backend.engine.validation import validate_plan

from .coordinator import Budgets, Coordinator
from .protocol import Json, ProtocolError
from .trace import MemoryPersistence

OBJECTIVE = {"weights": {"score": 1.0}, "source_type": "SYNTHETIC",
             "explanation": "Impact.score already applies Member 1's fixed objective weights"}
GROUND_WINDOW_MARGIN = timedelta(hours=6)


def iso(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def sender_id(agent_id: str) -> str:
    return "PILOT:" + agent_id.split(":", 1)[1] if agent_id.startswith("pilot:") else agent_id.upper()


def reason_text(reason) -> str:
    if isinstance(reason, dict):
        detail = reason.get("detail") or ""
        return f"{reason.get('code', 'REASON')}: {detail}" if detail else str(reason.get("code", "REASON"))
    return str(reason)


class PlatformEngine:
    """EnginePort over one frozen canonical Snapshot. No commit capability."""

    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot
        self._preview: Plan | None = None
        self._preview_done = False

    def _json_snapshot(self) -> Json:
        data = self.snapshot.model_dump(mode="json")
        data["schema_version"] = 1
        data["data_quality"] = {**data.get("data_quality", {}),
                                "clock_paused": self.snapshot.clock_status == "PAUSED"}
        return data

    async def get_snapshot(self, session_id: str) -> Json:
        return self._json_snapshot()

    async def dependencies(self, snapshot: Json, flight_id: str) -> list[str]:
        # Member 1 already passes the whole unfinished slice as the affected set.
        return []

    async def _preview_plan(self) -> Plan | None:
        if not self._preview_done:
            plans, _ = await asyncio.to_thread(search_candidates, self.snapshot, None, 100)
            self._preview = plans[0] if plans else None
            self._preview_done = True
        return self._preview

    async def domain_view(self, snapshot: Json, role: str, flight_id: str | None,
                          affected_ids: list[str]) -> Json:
        s = self.snapshot
        preview = await self._preview_plan()
        by_flight = {a.flight_id: a for a in preview.assignments} if preview else {}
        flights = {f.flight_id: f for f in s.flights}
        horizon = (iso(s.sim_time_utc), iso(s.end_utc))

        def window(kind, subject, resource, start, end):
            return {"kind": kind, "subject_id": subject, "resource_id": resource, "starts_at_utc": start,
                    "ends_at_utc": end, "unit": "UTC", "source_type": "SYNTHETIC"}

        if role == "ATC":
            runways = sorted(r.resource_id for r in s.infrastructure.resources if r.kind == "RUNWAY")
            return {"authorized_windows": [
                window("RUNWAY_WINDOW", f, by_flight[f].runway_id if f in by_flight else runways[0], *horizon)
                for f in affected_ids],
                "runway_ids": runways, "rules": s.rules_config.model_dump(mode="json")}
        if role == "GROUND":
            windows = []
            for f in affected_ids:
                gate = flights[f].assumptions.get("gate_id")
                if gate:
                    windows.append(window("GROUND_WINDOW", f, str(gate.value), horizon[0],
                                          iso(s.end_utc + GROUND_WINDOW_MARGIN)))
            return {"authorized_windows": windows}
        flight = flights[flight_id]
        arrival = flight.destination == s.airport_iata
        windows = [window("TIMING_WINDOW", flight_id, None, *horizon)]
        fuel = flight.assumptions.get("latest_safe_arrival_utc")
        if arrival and fuel:
            windows.append(window("LATEST_SAFE_ARRIVAL", flight_id, None, None,
                                  str(fuel.value).replace("+00:00", "Z")))
        return {"flight_id": flight_id, "operation": "ARRIVAL" if arrival else "DEPARTURE",
                "authorized_windows": windows,
                "assumptions": {k: v.model_dump(mode="json") for k, v in flight.assumptions.items()}}

    async def enumerate_candidates(self, snapshot: Json, constraints: list[Json], limit: int = 100) -> Json:
        plans, metadata = await asyncio.to_thread(search_candidates, self.snapshot, None, limit)
        out = []
        for p in plans[:limit]:
            p = p.model_copy(update={"source": "CONSENSUS"})
            out.append(p.model_copy(update={"plan_hash": plan_hash(p)}).model_dump(mode="json"))
        return {"plans": out, "limit": limit, "search_exhausted": bool(metadata.get("exhaustive", False))}

    async def validate_plan(self, snapshot: Json, plan: Json) -> list[Json]:
        checks = await asyncio.to_thread(validate_plan, self.snapshot, Plan.model_validate(plan))
        return [c.model_dump(mode="json") for c in checks]

    async def compute_impact(self, snapshot: Json, plan: Json, baseline: Json | None) -> Json:
        base = Plan.model_validate(baseline) if baseline else None
        impact = await asyncio.to_thread(compute_impact, self.snapshot, Plan.model_validate(plan), base)
        return impact.model_dump(mode="json")

    async def select_fallback(self, snapshot: Json, reason: str) -> Json:
        result = await asyncio.to_thread(select_fallback, self.snapshot, reason)
        meta = result.search_metadata
        return {"plan": result.plan.model_dump(mode="json") if result.plan else None,
                "search_exhausted": bool(meta.get("exhaustive", False)), "limit": int(meta.get("limit", 100))}

    def plan_hash(self, plan: Json) -> str:
        return plan_hash(Plan.model_validate(plan))


class PlatformCoordinator:
    """Member 1 ``Coordinator`` protocol backed by Member 3's two-round coordinator."""

    def __init__(self, model: ModelAdapter, budgets: Budgets | None = None):
        self.model = model
        # Stay inside Member 1's 60 s outer deadline so the platform sees our outcome.
        self.budgets = budgets or Budgets(round_seconds=25, total_seconds=55, fallback_reserve_seconds=2)
        self.last_trace: list[Json] = []

    @classmethod
    def from_env(cls) -> PlatformCoordinator:
        mode = os.environ.get("AIRPORT_MODEL_MODE", "mock").lower()
        if mode == "mock":
            return cls(MockAdapter(os.environ.get("AIRPORT_MOCK_BEHAVIOR", "consensus")))
        if mode == "live":
            import httpx

            from backend.agents.adapters import ResponsesAdapter
            return cls(ResponsesAdapter.from_env(httpx.AsyncClient(follow_redirects=False)))
        raise ValueError("AIRPORT_MODEL_MODE must be mock or live")

    async def coordinate(self, snapshot: Snapshot, affected_ids: list[str],
                         negotiation_id: str) -> CoordinationOutcome:
        engine = PlatformEngine(snapshot)
        baseline = await asyncio.to_thread(baseline_plan, snapshot)
        result = await Coordinator(engine, self.model, MemoryPersistence(), self.budgets).run(
            snapshot.scenario_id, list(affected_ids), instruction_version=snapshot.instruction_version,
            objective=OBJECTIVE, baseline_plan=baseline.plan.model_dump(mode="json") if baseline.plan else None,
            negotiation_id=negotiation_id)
        self.last_trace = result.get("trace", [])
        messages = []
        for record in self.last_trace:
            if record["event_type"] != "AGENT_MESSAGE":
                continue
            m = record["payload"]
            messages.append(AgentMessage(
                message_id=m["message_id"], negotiation_id=m["negotiation_id"], round=m["round"],
                sender_id=sender_id(m["sender_id"]), role=m["role"], type=m["type"],
                base_state_version=m["base_state_version"], instruction_version=m["instruction_version"],
                proposal_id=m["proposal_id"], plan_hash=m["plan_hash"], payload=m["payload"],
                reasons=[reason_text(r) for r in m["reasons"]]))
        consensus = result.get("decision") == "CONSENSUS" and result.get("status") == "AWAITING_AUTHORIZATION"
        metrics = result.get("metrics", {})
        reason = None if consensus else (result.get("reason") or result.get("status") or "NO_CONSENSUS")
        try:
            plan = Plan.model_validate(result["proposal"]) if consensus else None
        except Exception as exc:
            raise ProtocolError("INVALID_CONSENSUS_PLAN") from exc
        return CoordinationOutcome(plan=plan, messages=messages, model_mode=self.model.mode,
                                   model_calls=int(metrics.get("model_calls", 0)),
                                   rounds=int(metrics.get("rounds", 0)), reason=reason)
