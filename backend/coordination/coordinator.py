"""Bounded, asynchronous, unanimous two-round negotiation.

The engine owns operational rules, scoring inputs and canonical hashing. This
module owns protocol checks and never receives a schedule commit capability.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from backend.agents.domain import ATCAgent, GroundAgent, PilotAgent

from .ports import EnginePort, PersistencePort
from .protocol import Json, ProtocolError, clone, hard_checks_pass, objective_score, offers_satisfied, utc
from .trace import Trace


@dataclass(frozen=True)
class Budgets:
    round_seconds: float = 30.0
    total_seconds: float = 60.0
    fallback_reserve_seconds: float = 2.0
    candidate_limit: int = 100

    def __post_init__(self):
        if not (all(math.isfinite(x) for x in (self.round_seconds, self.total_seconds, self.fallback_reserve_seconds)) and
                type(self.candidate_limit) is int and 0 < self.round_seconds <= self.total_seconds and
                0 < self.fallback_reserve_seconds < self.total_seconds and
                1 <= self.candidate_limit <= 100):
            raise ValueError("Invalid negotiation budgets")


class Coordinator:
    def __init__(self, engine: EnginePort, model, persistence: PersistencePort,
                 budgets: Budgets | None = None, now=None):
        self.engine, self.model, self.persistence = engine, model, persistence
        self.budgets = budgets or Budgets()
        self.now = now or (lambda: datetime.now(timezone.utc))

    async def run(self, session_id: str, affected_ids: list[str], *,
                  instruction_version: int, objective: Json, baseline_plan: Json,
                  negotiation_id: str | None = None) -> Json:
        # Per-run state is local: one Coordinator may serve concurrent sessions.
        trace = Trace(negotiation_id or str(uuid4()), self.persistence)
        start = time.perf_counter()
        stats = {"rounds": 0, "model_calls": 0, "message_count": 0, "fallback_count": 0,
                 "candidate_count": 0, "hard_failures": 0, "unknown_checks": 0}
        snapshot = None
        outcome = None
        objective, baseline_plan = clone(objective), clone(baseline_plan)
        try:
            async with asyncio.timeout(self.budgets.total_seconds):
                try:
                    async with asyncio.timeout(self.budgets.total_seconds - self.budgets.fallback_reserve_seconds):
                        snapshot = clone(await self.engine.get_snapshot(session_id))
                        if snapshot.get("schema_version") != 1:
                            raise ProtocolError("UNSUPPORTED_SCHEMA_VERSION")
                        await self._fresh(session_id, snapshot, instruction_version)
                        await trace.emit("NEGOTIATION_STARTED", {
                            "model_mode": self.model.mode, "model": self.model.model,
                            "snapshot": snapshot, "affected_ids": affected_ids,
                            "instruction_version": instruction_version, "objective": objective,
                            "round_seconds": self.budgets.round_seconds, "total_seconds": self.budgets.total_seconds})
                        outcome = await self._negotiate(session_id, snapshot, affected_ids,
                                                        instruction_version, clone(objective), clone(baseline_plan), trace, stats)
                except TimeoutError:
                    outcome = await self._fallback(session_id, snapshot, instruction_version, "TOTAL_NEGOTIATION_TIMEOUT", trace, stats)
                except ProtocolError as exc:
                    if exc.code == "PERSISTENCE_UNAVAILABLE":
                        raise
                    await trace.emit("NEGOTIATION_STOPPED", {"reason": exc.code, "detail": exc.detail})
                    if exc.code in ("STALE_SNAPSHOT", "MANUAL_MODE", "SIMULATION_NOT_PAUSED", "UNSUPPORTED_SCHEMA_VERSION"):
                        outcome = {"status": "STALE" if exc.code == "STALE_SNAPSHOT" else "CANCELLED", "reason": exc.code}
                    else:
                        outcome = await self._fallback(session_id, snapshot, instruction_version, exc.code, trace, stats)
                # Final persistence is part of the deadline; no returned consensus
                # if its audit record could not be durably saved.
                await trace.emit("NEGOTIATION_FINISHED", {**outcome, "metrics": self._metrics(stats, start),
                                                          "model_mode": self.model.mode})
        except TimeoutError:
            outcome = {"status": "FAILED", "reason": "TOTAL_TIMEOUT"}
        except ProtocolError as exc:
            outcome = {"status": "FAILED", "reason": exc.code}
        except asyncio.CancelledError:
            # Persist cancellation within the remaining budget, then propagate.
            remaining = self.budgets.total_seconds - (time.perf_counter() - start)
            if remaining > 0:
                try:
                    async with asyncio.timeout(min(.05, remaining)):
                        await trace.emit("NEGOTIATION_CANCELLED", {"reason": "TASK_CANCELLED", "metrics": self._metrics(stats, start)})
                except (TimeoutError, ProtocolError):
                    pass
            raise
        except Exception:
            # Do not leak engine/provider exception strings into exported traces.
            outcome = {"status": "FAILED", "reason": "DEPENDENCY_ERROR"}
        return {"negotiation_id": trace.negotiation_id, "model_mode": self.model.mode,
                **outcome, "metrics": self._metrics(stats, start), "trace": trace.records}

    @staticmethod
    def _metrics(stats, start):
        return {**stats, "duration_seconds": round(time.perf_counter() - start, 6)}

    async def _fresh(self, session_id, snapshot, instruction_version):
        current = clone(await self.engine.get_snapshot(session_id))
        if current.get("mode") == "MANUAL":
            raise ProtocolError("MANUAL_MODE")
        # Adapter must attest that the simulation clock is frozen.
        if current.get("data_quality", {}).get("clock_paused") is not True:
            raise ProtocolError("SIMULATION_NOT_PAUSED")
        versions = [i["instruction_version"] for i in current.get("active_instructions", [])]
        if max(versions, default=0) != instruction_version:
            raise ProtocolError("STALE_SNAPSHOT")
        for key in ("scenario_id", "state_version", "sim_time_utc", "active_instructions"):
            if current.get(key) != snapshot.get(key):
                raise ProtocolError("STALE_SNAPSHOT")

    async def _participants(self, snapshot, affected_ids):
        all_ids = {f["flight_id"] for f in snapshot["flights"]}
        selected = set(affected_ids)
        if not selected or not selected <= all_ids:
            raise ProtocolError("INVALID_AFFECTED_FLIGHTS")
        frontier = sorted(selected)
        while frontier:
            flight_id = frontier.pop(0)
            dependencies = set(await self.engine.dependencies(clone(snapshot), flight_id))
            if not dependencies <= all_ids:
                raise ProtocolError("INVALID_DEPENDENCY")
            frontier.extend(sorted(dependencies - selected))
            selected.update(dependencies)
        affected = sorted(selected)
        agents = []
        for role, flight_id in [("ATC", None), ("GROUND", None)] + [("PILOT", f) for f in affected]:
            view = await self.engine.domain_view(clone(snapshot), role, flight_id, affected)
            agents.append(PilotAgent(flight_id, view) if role == "PILOT" else
                          ATCAgent(view) if role == "ATC" else GroundAgent(view))
        return agents, affected

    async def _round(self, agents, context, round_number, plan, trace, stats):
        stats["rounds"] = round_number
        deadline = time.perf_counter() + self.budgets.round_seconds

        async def call(agent):
            request = agent.request(context, round_number, plan)
            # Persist input BEFORE dispatch. A DB failure prevents the call.
            await trace.emit("AGENT_REQUEST", {"agent_id": agent.agent_id, "role": agent.role,
                                               "round": round_number, "request": request,
                                               "model_mode": self.model.mode})
            call_start = time.perf_counter()
            stats["model_calls"] += 1
            response = None
            error = None
            try:
                response = await self.model.generate(clone(request), max(0.001, deadline - call_start))
                reply = agent.accept_response(response.payload, request)
                message = {"message_id": str(uuid4()), "negotiation_id": trace.negotiation_id,
                           "round": round_number, "sender_id": agent.agent_id, "role": agent.role,
                           "type": reply["type"], "base_state_version": reply["base_state_version"],
                           "instruction_version": reply["instruction_version"],
                           "proposal_id": reply["proposal_id"], "plan_hash": reply["plan_hash"],
                           "payload": {"constraints": reply["constraints"]}, "reasons": reply["reasons"]}
                await trace.emit("AGENT_MESSAGE", message)
                stats["message_count"] += 1
                return message
            except ProtocolError as exc:
                error = exc.code
                raise
            except asyncio.CancelledError:
                error = "CALL_CANCELLED"
                raise
            except Exception as exc:
                error = "MODEL_ERROR"
                raise ProtocolError(error) from exc
            finally:
                # Keep telemetry local during cancellation; flush it from _round
                # after sibling calls have stopped to avoid unbounded finalizers.
                telemetry.append({"agent_id": agent.agent_id, "round": round_number,
                                  "mode": self.model.mode,
                                  "model": response.model if response else self.model.model,
                                  "usage": response.usage if response else None,
                                  "latency_seconds": round(time.perf_counter() - call_start, 6), "error": error})

        telemetry = []
        tasks = [asyncio.create_task(call(a)) for a in agents]
        try:
            async with asyncio.timeout(self.budgets.round_seconds):
                messages = await asyncio.gather(*tasks)
        except TimeoutError as exc:
            raise ProtocolError("ROUND_TIMEOUT") from exc
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not asyncio.current_task().cancelling():
                for item in telemetry:
                    await trace.emit("MODEL_CALL", item)
        return messages

    async def _check_plan(self, snapshot, plan, instruction_version, affected=None):
        if plan.get("scenario_id") != snapshot["scenario_id"] or plan.get("base_state_version") != snapshot["state_version"] or plan.get("instruction_version") != instruction_version:
            raise ProtocolError("STALE_PLAN")
        if utc(plan["expires_at_utc"]) <= self.now():
            raise ProtocolError("EXPIRED_PLAN")
        if not plan.get("proposal_id") or type(plan.get("proposal_version")) is not int:
            raise ProtocolError("INVALID_PLAN")
        self._check_participants(snapshot, plan, affected)
        if plan.get("plan_hash") != self.engine.plan_hash(clone(plan)):
            raise ProtocolError("PLAN_HASH_MISMATCH")
        return await self.engine.validate_plan(clone(snapshot), clone(plan))

    @staticmethod
    def _check_participants(snapshot, plan, affected):
        ids = [a["flight_id"] for a in plan["assignments"]]
        known = {f["flight_id"] for f in snapshot["flights"]}
        if not ids or not set(ids) <= known or len(set(ids)) != len(ids):
            raise ProtocolError("INCOMPLETE_PLAN")
        if affected and set(ids) != set(affected):
            raise ProtocolError("PARTICIPANT_COVERAGE_MISMATCH")

    async def _negotiate(self, session_id, snapshot, affected_ids, version, objective, baseline, trace, stats):
        agents, affected = await self._participants(snapshot, affected_ids)
        await trace.emit("PARTICIPANTS_SELECTED", {"affected_ids": affected, "agent_ids": [a.agent_id for a in agents]})
        context = {"negotiation_id": trace.negotiation_id, "scenario_id": snapshot["scenario_id"],
                   "base_state_version": snapshot["state_version"], "instruction_version": version,
                   "sim_time_utc": snapshot["sim_time_utc"], "confirmed_instructions": clone(snapshot["active_instructions"])}
        offers = await self._round(agents, context, 1, None, trace, stats)
        await self._fresh(session_id, snapshot, version)
        constraints = [c for offer in offers for c in offer["payload"]["constraints"]]
        batch = await self.engine.enumerate_candidates(clone(snapshot), clone(constraints), self.budgets.candidate_limit)
        if not isinstance(batch.get("search_exhausted"), bool) or batch.get("limit") != self.budgets.candidate_limit or len(batch["plans"]) > self.budgets.candidate_limit:
            raise ProtocolError("INVALID_CANDIDATE_BATCH")
        await trace.emit("CANDIDATE_SEARCH", {"count": len(batch["plans"]), "limit": batch["limit"],
                                               "search_exhausted": batch["search_exhausted"]})
        candidates = []
        for candidate in batch["plans"]:
            stats["candidate_count"] += 1
            plan = clone(candidate)
            try:
                self._check_participants(snapshot, plan, affected)
                # Impact is calculated before hashing/ballots, then frozen with plan.
                plan["impact"] = clone(await self.engine.compute_impact(clone(snapshot), clone(plan), clone(baseline)))
                plan["plan_hash"] = self.engine.plan_hash(clone(plan))
                checks = await self._check_plan(snapshot, plan, version, affected)
                if not offers_satisfied(plan, constraints):
                    raise ProtocolError("OFFER_WINDOW_VIOLATION")
            except ProtocolError as exc:
                await trace.emit("CANDIDATE_REJECTED", {"proposal_id": plan.get("proposal_id"), "reason": exc.code})
                continue
            stats["hard_failures"] += sum(c.get("severity") == "HARD" and c.get("status") == "FAIL" for c in checks)
            stats["unknown_checks"] += sum(c.get("status") == "UNKNOWN" for c in checks)
            feasible = hard_checks_pass(checks)
            await trace.emit("CANDIDATE_CHECKS", {"proposal_id": plan["proposal_id"], "feasible": feasible, "checks": checks})
            if feasible:
                score = objective_score(plan["impact"], objective)
                tie = tuple((a["flight_id"], a["runway_time_utc"], a["runway_id"]) for a in sorted(plan["assignments"], key=lambda a: a["flight_id"]))
                candidates.append((score, tie, plan["plan_hash"], plan))
        if not candidates:
            raise ProtocolError("NO_FEASIBLE_PLAN_FOUND")
        candidates.sort(key=lambda c: c[:3])
        proposal = clone(candidates[0][3])
        await trace.emit("PROPOSAL_FROZEN", {"proposal": proposal, "objective": objective, "score": candidates[0][0]})
        await self._fresh(session_id, snapshot, version)
        ballots = await self._round(agents, context, 2, proposal, trace, stats)
        if any(b["type"] != "ACCEPT" for b in ballots):
            raise ProtocolError("AGENT_VETO")
        await self._fresh(session_id, snapshot, version)
        checks = await self._check_plan(snapshot, proposal, version, affected)
        if not hard_checks_pass(checks):
            raise ProtocolError("FINAL_VALIDATION_FAILED")
        await trace.emit("CONSENSUS_REACHED", {"plan_hash": proposal["plan_hash"], "participants": len(agents), "checks": checks})
        return {"status": "AWAITING_AUTHORIZATION", "decision": "CONSENSUS", "proposal": proposal,
                "checks": checks, "affected_ids": affected, "search_exhausted": batch["search_exhausted"],
                "search_limit": batch["limit"], "reason": None}

    async def _fallback(self, session_id, snapshot, version, reason, trace, stats):
        if snapshot is None:
            return {"status": "FAILED", "reason": reason}
        try:
            await self._fresh(session_id, snapshot, version)
        except ProtocolError as exc:
            return {"status": "STALE" if exc.code == "STALE_SNAPSHOT" else "CANCELLED", "reason": exc.code}
        stats["fallback_count"] += 1
        await trace.emit("FALLBACK_REQUESTED", {"reason": reason})
        fallback = await self.engine.select_fallback(clone(snapshot), reason)
        plan = clone(fallback.get("plan"))
        search = {"search_exhausted": fallback["search_exhausted"], "search_limit": fallback["limit"]}
        if not isinstance(search["search_exhausted"], bool) or type(search["search_limit"]) is not int or not 1 <= search["search_limit"] <= 100:
            raise ProtocolError("INVALID_FALLBACK_SEARCH")
        if plan:
            checks = await self._check_plan(snapshot, plan, version)
            stats["hard_failures"] += sum(c.get("severity") == "HARD" and c.get("status") == "FAIL" for c in checks)
            stats["unknown_checks"] += sum(c.get("status") == "UNKNOWN" for c in checks)
            await self._fresh(session_id, snapshot, version)
            await trace.emit("FALLBACK_CHECKS", {"checks": checks})
            if hard_checks_pass(checks):
                return {"status": "AWAITING_AUTHORIZATION", "decision": "FALLBACK", "reason": reason,
                        "proposal": plan, "checks": checks, **search}
        return {"status": "NO_FEASIBLE_PLAN_FOUND", "decision": "FALLBACK", "reason": reason, **search}
