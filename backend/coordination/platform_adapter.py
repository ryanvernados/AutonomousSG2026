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
their declared latest safe arrival, ground the gate over the horizon. The engine search is
CPU-bound, so it runs in a child process: it neither holds the API's GIL nor outlives a
cancelled or timed-out negotiation (the process is terminated before the platform's fallback).
"""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from functools import partial

from backend.agents.adapters import MockAdapter, ModelAdapter
from backend.api.coordinator import CoordinationOutcome
from backend.contracts import AgentMessage, Plan, Snapshot, plan_hash
from backend.engine.isolation import run_in_process
from backend.engine.planning import FlightRequest, compute_impact, search_candidates, select_fallback
from backend.engine.validation import validate_plan

from .coordinator import Budgets, Coordinator
from .protocol import Json, ProtocolError
from .trace import MemoryPersistence

OBJECTIVE = {"weights": {"score": 1.0}, "source_type": "SYNTHETIC",
             "explanation": "Impact.score already applies Member 1's fixed objective weights"}
GROUND_WINDOW_MARGIN = timedelta(hours=6)
ALTERNATIVE_GATES = 3  # compatible gates offered to the ground agent besides the scheduled one


def requests_from_offers(snapshot: Snapshot, constraints: list[Json]) -> dict[str, FlightRequest]:
    """Turn the agents' round-1 constraints into engine requests (runway, time bounds, gate).

    ATC runway windows and pilot timing windows bound the runway time; ATC's resource picks the
    runway; the pilot's latest safe arrival caps it; ground's gate window picks the gate. Two
    different runways or gates for one flight cancel that choice (the offer check then rejects
    any plan, so the platform falls back rather than guessing).
    """
    from backend.coordination.protocol import utc

    gates = {r.resource_id for r in snapshot.infrastructure.resources if r.kind == "GATE"}
    fields: dict[str, dict] = {}
    for c in constraints:
        f = fields.setdefault(c["subject_id"], {"runways": set(), "gates": set(), "earliest": None, "latest": None})
        if c["kind"] == "GROUND_WINDOW":
            if c["resource_id"] in gates:
                f["gates"].add(c["resource_id"])
            continue
        start = utc(c["starts_at_utc"]) if c.get("starts_at_utc") else None
        end = utc(c["ends_at_utc"]) if c.get("ends_at_utc") else None
        if c["kind"] == "RUNWAY_WINDOW" and c["resource_id"]:
            f["runways"].add(c["resource_id"])
        if start and (f["earliest"] is None or start > f["earliest"]):
            f["earliest"] = start
        if end and (f["latest"] is None or end < f["latest"]):
            f["latest"] = end
    return {fid: FlightRequest(runway_ids=tuple(f["runways"]) if len(f["runways"]) == 1 else (),
                               earliest_utc=f["earliest"], latest_utc=f["latest"],
                               gate_id=next(iter(f["gates"])) if len(f["gates"]) == 1 else None)
            for fid, f in fields.items()}


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

    def __init__(self, snapshot: Snapshot, affected_ids: list[str] | None = None):
        self.snapshot = snapshot
        # Searches replan only the platform's affected set; other published flights stay fixed.
        self.affected_ids = list(affected_ids) if affected_ids is not None else None
        self._search: tuple[list[Plan], dict] | None = None
        self.request_search: dict = {}
        self._search_lock = asyncio.Lock()

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

    async def _search_once(self) -> tuple[list[Plan], dict]:
        # One deterministic search per frozen snapshot, shared by the agents' authorized windows
        # and the candidate batch, so the negotiation pays for the engine search only once.
        async with self._search_lock:
            if self._search is None:
                self._search = await run_in_process(
                    partial(search_candidates, flight_ids=self.affected_ids), self.snapshot, None, 100)
        return self._search

    async def _preview_plan(self) -> Plan | None:
        # The engine-only plan: what the agents start from and what their plan is compared with.
        plans, _ = await self._search_once()
        return min(plans, key=lambda p: (p.impact.score, p.plan_hash)) if plans else None

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

        def slot(f):
            a = by_flight.get(f)
            return {"flight_id": f, "operation": a.operation, "runway_id": a.runway_id,
                    "runway_time_utc": iso(a.runway_time_utc), "gate_id": a.gate_id,
                    "gate_start_utc": iso(a.gate_start_utc), "gate_end_utc": iso(a.gate_end_utc)} if a else None

        engine_plan = [x for x in (slot(f) for f in affected_ids) if x]
        if role == "ATC":
            # Any runway for any flight: ATC chooses each flight's runway and slot window (the sequence).
            runways = sorted(r.resource_id for r in s.infrastructure.resources if r.kind == "RUNWAY")
            return {"authorized_windows": [window("RUNWAY_WINDOW", f, r, *horizon)
                                           for f in affected_ids for r in runways],
                    "runway_ids": runways, "engine_plan": engine_plan,
                    "affected_ids": list(affected_ids), "required_constraints": len(affected_ids),
                    "rules": s.rules_config.model_dump(mode="json")}
        if role == "GROUND":
            gate_resources = sorted((r for r in s.infrastructure.resources if r.kind == "GATE"),
                                    key=lambda r: r.resource_id)
            used = {a.gate_id for a in by_flight.values()}
            windows = []
            for f in affected_ids:
                gate = flights[f].assumptions.get("gate_id")
                category = flights[f].assumptions.get("wake_category")
                if not gate:
                    continue
                choices = [str(gate.value)] + [
                    r.resource_id for r in gate_resources
                    if r.resource_id != str(gate.value) and r.resource_id not in used
                    and (not category or str(category.value) in r.compatible_categories)][:ALTERNATIVE_GATES]
                windows += [window("GROUND_WINDOW", f, g, horizon[0], iso(s.end_utc + GROUND_WINDOW_MARGIN))
                            for g in choices]
            return {"authorized_windows": windows, "engine_plan": engine_plan,
                    "affected_ids": list(affected_ids), "required_constraints": len(affected_ids)}
        flight = flights[flight_id]
        arrival = flight.destination == s.airport_iata
        windows = [window("TIMING_WINDOW", flight_id, None, *horizon)]
        fuel = flight.assumptions.get("latest_safe_arrival_utc")
        if arrival and fuel:
            windows.append(window("LATEST_SAFE_ARRIVAL", flight_id, None, None,
                                  str(fuel.value).replace("+00:00", "Z")))
        mine = by_flight.get(flight_id)
        return {"flight_id": flight_id, "operation": "ARRIVAL" if arrival else "DEPARTURE",
                "authorized_windows": windows,
                # The engine's own slot for this flight, so the pilot judges the actual proposal
                # (e.g. whether a runway closure touches it) instead of guessing.
                "engine_proposed_slot": {"runway_id": mine.runway_id, "runway_time_utc": iso(mine.runway_time_utc),
                                         "gate_id": mine.gate_id} if mine else None,
                "assumptions": {k: v.model_dump(mode="json") for k, v in flight.assumptions.items()}}

    async def enumerate_candidates(self, snapshot: Json, constraints: list[Json], limit: int = 100) -> Json:
        # Plans built around the agents' requests first, then the engine-only plans; the coordinator
        # keeps those satisfying every offer and ranks them by Impact.score.
        engine_plans, metadata = await self._search_once()
        requests = requests_from_offers(self.snapshot, constraints) if constraints else {}
        requested = []
        if requests:
            requested, self.request_search = await run_in_process(
                partial(search_candidates, requests=requests, flight_ids=self.affected_ids), self.snapshot, None, 100)
        plans = requested + engine_plans
        out = []
        for p in plans[:limit]:
            p = p.model_copy(update={"source": "CONSENSUS"})
            out.append(p.model_copy(update={"plan_hash": plan_hash(p)}).model_dump(mode="json"))
        return {"plans": out, "limit": limit, "search_exhausted": bool(metadata.get("exhaustive", False))}

    async def validate_plan(self, snapshot: Json, plan: Json) -> list[Json]:
        checks = await asyncio.to_thread(validate_plan, self.snapshot, Plan.model_validate(plan))
        return [c.model_dump(mode="json") for c in checks]

    async def compute_impact(self, snapshot: Json, plan: Json, baseline: Json | None) -> Json:
        # Without a caller baseline, compare with the engine-only plan, so versus_baseline shows
        # what the agents' negotiation changed.
        base = Plan.model_validate(baseline) if baseline else await self._preview_plan()
        impact = await asyncio.to_thread(compute_impact, self.snapshot, Plan.model_validate(plan), base)
        return impact.model_dump(mode="json")

    async def select_fallback(self, snapshot: Json, reason: str) -> Json:
        result = await run_in_process(select_fallback, self.snapshot, reason, self.affected_ids)
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
            # Claude when ANTHROPIC_API_KEY is set (or AIRPORT_MODEL_PROVIDER=anthropic), else OpenAI.
            provider = os.environ.get("AIRPORT_MODEL_PROVIDER") or (
                "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai")
            if provider == "anthropic":
                from backend.agents.adapters import ClaudeAdapter
                return cls(ClaudeAdapter.from_env())
            import httpx

            from backend.agents.adapters import ResponsesAdapter
            return cls(ResponsesAdapter.from_env(httpx.AsyncClient(follow_redirects=False)))
        raise ValueError("AIRPORT_MODEL_MODE must be mock or live")

    async def coordinate(self, snapshot: Snapshot, affected_ids: list[str],
                         negotiation_id: str) -> CoordinationOutcome:
        engine = PlatformEngine(snapshot, affected_ids)
        # No separate baseline search inside the negotiation deadline: Member 1 exposes the FCFS
        # baseline on GET /sessions/{id}/baseline, and impact is ranked on Impact.score.
        result = await Coordinator(engine, self.model, MemoryPersistence(), self.budgets).run(
            snapshot.scenario_id, list(affected_ids), instruction_version=snapshot.instruction_version,
            objective=OBJECTIVE, baseline_plan=None, negotiation_id=negotiation_id)
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
