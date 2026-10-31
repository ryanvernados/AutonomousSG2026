import asyncio
import hashlib
import json
import threading
import time

from backend.api.coordinator import CoordinationOutcome
from backend.api.errors import PlatformError
from backend.contracts import (
    AuditEvent,
    CommandResult,
    NegotiationRecord,
    Plan,
    ProposalRecord,
    SessionDocument,
    Snapshot,
    plan_hash,
    uid,
    utcnow,
)
from backend.engine import advance_clock, feasible, reservations_for, select_fallback, validate_plan
from backend.engine.scenarios import initial_snapshot
from backend.engine.validation import instruction_issues

ACTIVE_PROPOSALS = {"PROPOSED", "CONSENSUS_REACHED", "AWAITING_AUTHORIZATION"}


def fingerprint(action, command, actor_id):
    value = {"action": action, "actor": actor_id, "command": command.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PlatformService:
    def __init__(self, store, scenarios, coordinator=None):
        self.store, self.scenarios, self.coordinator = store, scenarios, coordinator
        self.lock = threading.RLock()

    def require_role(self, project_id, actor_id, write=False):
        role = self.store.role(project_id, actor_id)
        if role not in ({"controller", "admin"} if write else {"viewer", "controller", "admin"}):
            raise PlatformError(403, "FORBIDDEN", "Project membership and required role are missing")

    def read(self, session_id, actor_id):
        doc = self.store.get(session_id)
        self.require_role(doc.project_id, actor_id)
        return doc

    def get_snapshot(self, session_id):
        """Trusted in-process engine interface. HTTP access always checks membership first."""
        return self.store.get(session_id).snapshot

    def event(self, doc, actor, event_type, correlation_id, payload=None):
        s = doc.snapshot
        return AuditEvent(event_seq=0, scenario_id=s.scenario_id, state_version=s.state_version,
                          sim_time_utc=s.sim_time_utc, actor_id=actor, event_type=event_type,
                          correlation_id=correlation_id, payload=payload or {})

    def create(self, command, actor_id):
        with self.lock:
            fp = fingerprint("create", command, actor_id)
            try:
                scenario = self.scenarios.load_scenario(command.scenario_id)
            except KeyError as exc:
                raise PlatformError(404, "SCENARIO_NOT_FOUND", "Scenario not found") from exc
            self.require_role(scenario.project_id, actor_id, write=True)
            cached = self.store.creation(actor_id, command.command_id, fp)
            if cached is not None:
                return cached
            if command.base_state_version != 0:
                raise PlatformError(409, "STALE_STATE", "New sessions start at version 0", 0)
            snap = initial_snapshot(scenario)
            doc = SessionDocument(project_id=scenario.project_id, snapshot=snap, initial_snapshot=snap)
            event = self.event(doc, actor_id, "SESSION_CREATED", command.command_id,
                               {"seed": scenario.seed, "source_manifest": scenario.source_manifest})
            response = CommandResult(session_id=doc.session_id, state_version=0,
                                     event_ids=[event.event_id], snapshot=snap).model_dump(mode="json")
            return self.store.commit(doc, None, [event], actor_id, command.command_id, fp, response, creation=True)

    def _execute(self, session_id, command, actor_id, action, mutate):
        with self.lock:
            doc = self.read(session_id, actor_id)
            self.require_role(doc.project_id, actor_id, write=True)
            fp = fingerprint(action, command, actor_id)
            cached = self.store.command(session_id, command.command_id, fp)
            if cached is not None:
                return cached
            if command.base_state_version != doc.snapshot.state_version:
                raise PlatformError(409, "STALE_STATE", "Reload the authoritative snapshot",
                                    doc.snapshot.state_version)
            updated, event_type, payload, extra = mutate(doc)
            event = self.event(updated, actor_id, event_type, command.command_id, payload)
            response = CommandResult(session_id=session_id, state_version=updated.snapshot.state_version,
                                     event_ids=[event.event_id], snapshot=updated.snapshot,
                                     **extra).model_dump(mode="json")
            return self.store.commit(updated, doc.revision, [event], actor_id, command.command_id, fp, response)

    def invalidate(self, doc, status="STALE"):
        return doc.model_copy(update={
            "proposals": {pid: record.model_copy(update={"status": status})
                          if record.status in ACTIVE_PROPOSALS else record
                          for pid, record in doc.proposals.items()},
            "negotiations": {nid: record.model_copy(update={"status": "CANCELLED", "reason": status})
                             if record.status == "RUNNING" else record
                             for nid, record in doc.negotiations.items()}})

    def clock(self, session_id, command, actor_id):
        def mutate(doc):
            s = doc.snapshot
            if command.action == "STEP":
                if s.clock_status != "PAUSED":
                    raise PlatformError(409, "CLOCK_RUNNING", "Pause before stepping", s.state_version)
                snap = advance_clock(s, command.seconds)
            elif command.action == "RESET":
                # Reset is explicit and audited; keeps restrictions and advances monotonically.
                snap = doc.initial_snapshot.model_copy(update={"state_version": s.state_version + 1,
                    "active_instructions": s.active_instructions, "instruction_version": s.instruction_version,
                    "mode": s.mode})
            else:
                snap = s.model_copy(update={"clock_status": "RUNNING" if command.action == "RESUME" else "PAUSED",
                                            "state_version": s.state_version + 1})
            updated = self.invalidate(doc).model_copy(update={"snapshot": snap})
            return updated, "CLOCK_" + command.action, {"seconds": command.seconds}, {}
        return self._execute(session_id, command, actor_id, "clock", mutate)

    def preview(self, session_id, instruction, actor_id):
        doc = self.read(session_id, actor_id)
        self.require_role(doc.project_id, actor_id, write=True)
        normalized = instruction.model_copy(update={"entered_by": actor_id,
                                                    "instruction_version": doc.snapshot.instruction_version + 1})
        return {"instruction": normalized.model_dump(mode="json"),
                "issues": instruction_issues(doc.snapshot, normalized),
                "state_version": doc.snapshot.state_version}

    def instruction(self, session_id, command, actor_id):
        def mutate(doc):
            instruction = command.instruction
            issues = instruction_issues(doc.snapshot, instruction)
            if issues:
                raise PlatformError(422, "INVALID_INSTRUCTION", "Instruction failed preview checks",
                                    doc.snapshot.state_version, issues=issues)
            if instruction.instruction_id in {i.instruction_id for i in doc.snapshot.active_instructions}:
                raise PlatformError(409, "INSTRUCTION_EXISTS", "Instruction ID is already confirmed")
            instruction = instruction.model_copy(update={"entered_by": actor_id,
                "instruction_version": doc.snapshot.instruction_version + 1})
            snap = doc.snapshot.model_copy(update={"active_instructions": doc.snapshot.active_instructions + [instruction],
                "state_version": doc.snapshot.state_version + 1, "instruction_version": instruction.instruction_version})
            return self.invalidate(doc).model_copy(update={"snapshot": snap}), "INSTRUCTION_CONFIRMED", {
                "instruction": instruction.model_dump(mode="json")}, {}
        return self._execute(session_id, command, actor_id, "instruction", mutate)

    def control(self, session_id, command, actor_id):
        def mutate(doc):
            snap = doc.snapshot.model_copy(update={"mode": command.mode,
                                                    "state_version": doc.snapshot.state_version + 1})
            return self.invalidate(doc, "REJECTED" if command.mode == "MANUAL" else "STALE").model_copy(
                update={"snapshot": snap}), "CONTROL_CHANGED", {"mode": command.mode}, {}
        return self._execute(session_id, command, actor_id, "control", mutate)

    def start_negotiation(self, session_id, command, actor_id):
        def mutate(doc):
            if doc.snapshot.mode == "MANUAL":
                raise PlatformError(409, "MANUAL_MODE", "Automatic replanning is disabled", doc.snapshot.state_version)
            if any(n.status == "RUNNING" for n in doc.negotiations.values()):
                raise PlatformError(409, "NEGOTIATION_RUNNING", "A negotiation is already running")
            known = {f.flight_id for f in doc.snapshot.flights}
            if set(command.affected_ids) - known:
                raise PlatformError(422, "UNKNOWN_FLIGHT", "Affected IDs must reference scenario flights")
            # Conservative transitive closure: whole traffic slice, including resource and rotation dependencies.
            affected = sorted(f.flight_id for f in doc.snapshot.flights
                              if f.state not in {"DEPARTED", "COMPLETED"})
            record = NegotiationRecord(affected_ids=affected)
            snap = doc.snapshot
            if snap.clock_status != "PAUSED":
                snap = snap.model_copy(update={"clock_status": "PAUSED", "state_version": snap.state_version + 1})
            updated = self.invalidate(doc).model_copy(update={"snapshot": snap,
                "negotiations": {**doc.negotiations, record.negotiation_id: record}})
            return updated, "NEGOTIATION_STARTED", {"negotiation_id": record.negotiation_id,
                                                    "affected_ids": affected}, {"negotiation_id": record.negotiation_id}
        return self._execute(session_id, command, actor_id, "negotiate", mutate)

    def _validate_outcome(self, snapshot, record, outcome):
        if not isinstance(outcome, CoordinationOutcome) or not outcome.plan or outcome.reason:
            raise ValueError("No agreed plan or invalid coordinator output")
        p = outcome.plan
        if p.source != "CONSENSUS" or p.expires_at_utc <= utcnow():
            raise ValueError("Consensus identity or validity missing")
        if outcome.rounds != 2 or not feasible(validate_plan(snapshot, p)):
            raise ValueError("Two rounds and passing deterministic checks required")
        required = {"ATC", "GROUND"} | {"PILOT:" + f for f in record.affected_ids}
        # Exactly one offer and one ballot per participant; no hidden correction turns.
        if len(outcome.messages) != len(required) * 2:
            raise ValueError("Missing or extra messages")
        for round_no, kind in ((1, "OFFER"), (2, "ACCEPT")):
            messages = [m for m in outcome.messages if m.round == round_no]
            if len(messages) != len(required) or {m.sender_id for m in messages} != required:
                raise ValueError("Missing participant or duplicate response")
            for m in messages:
                role = "PILOT" if m.sender_id.startswith("PILOT:") else m.sender_id
                if m.type != kind or m.role != role or m.negotiation_id != record.negotiation_id or (
                    m.base_state_version != snapshot.state_version or
                    m.instruction_version != snapshot.instruction_version):
                    raise ValueError("Invalid message identity, version, role or ballot")
                if round_no == 2 and (m.plan_hash != p.plan_hash or m.proposal_id != p.proposal_id):
                    raise ValueError("Ballot references a different immutable plan")

    async def negotiate(self, session_id, negotiation_id, actor_id):
        doc = await asyncio.to_thread(self.store.get, session_id)
        record = doc.negotiations.get(negotiation_id)
        if not record or record.status != "RUNNING":
            return
        frozen = doc.snapshot.model_copy(deep=True)
        started = time.monotonic()
        outcome = None
        reason = "COORDINATOR_UNAVAILABLE"
        if self.coordinator:
            try:
                budget = min(frozen.rules_config.total_budget_seconds, frozen.rules_config.round_budget_seconds * 2)
                outcome = await asyncio.wait_for(self.coordinator.coordinate(frozen.model_copy(deep=True),
                    list(record.affected_ids), negotiation_id), timeout=budget)
                self._validate_outcome(frozen, record, outcome)
                reason = "CONSENSUS"
            except asyncio.TimeoutError:
                reason = "MODEL_TIMEOUT"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = "INVALID_OR_FAILED_COORDINATION:" + type(exc).__name__
        if reason == "CONSENSUS":
            plan, checks, metadata = outcome.plan, validate_plan(frozen, outcome.plan), {}
        else:
            fallback = await asyncio.to_thread(select_fallback, frozen, reason)
            plan, checks, metadata = fallback.plan, fallback.checks, fallback.search_metadata
        duration = time.monotonic() - started
        await asyncio.to_thread(self.finish_negotiation, session_id, negotiation_id, actor_id, frozen,
                                outcome, plan, checks, metadata, reason, duration)

    def finish_negotiation(self, session_id, nid, actor_id, frozen, outcome, plan, checks, metadata, reason, duration):
        with self.lock:
            doc = self.store.get(session_id)
            record = doc.negotiations[nid]
            if record.status != "RUNNING":
                return
            stale = (doc.snapshot.state_version != frozen.state_version or doc.snapshot.mode == "MANUAL"
                     or doc.snapshot.instruction_version != frozen.instruction_version)
            status = "STALE" if stale else "AWAITING_AUTHORIZATION" if plan else "NO_FEASIBLE_PLAN_FOUND"
            proposals = dict(doc.proposals)
            if plan and not stale:
                proposals[plan.proposal_id] = ProposalRecord(plan=plan, checks=checks)
            record = record.model_copy(update={"status": status, "reason": reason,
                "round": outcome.rounds if outcome else 0, "messages": outcome.messages if outcome else [],
                "model_calls": outcome.model_calls if outcome else 0,
                "model_mode": outcome.model_mode if outcome else "UNAVAILABLE",
                "duration_seconds": duration, "proposal_id": plan.proposal_id if plan and not stale else None,
                "search_metadata": metadata})
            updated = doc.model_copy(update={"proposals": proposals, "negotiations": {**doc.negotiations, nid: record}})
            event = self.event(updated, actor_id, "NEGOTIATION_FINISHED", nid, {
                "negotiation": record.model_dump(mode="json"), "plan": plan.model_dump(mode="json") if plan else None,
                "checks": [c.model_dump(mode="json") for c in checks]})
            self.store.commit(updated, doc.revision, [event], actor_id, "finish:" + nid, nid, {}, internal=True)

    def _apply_plan(self, doc, plan, actor_id):
        s = doc.snapshot
        if plan.expires_at_utc <= utcnow():
            raise PlatformError(409, "EXPIRED_PLAN", "Proposal validity period expired", s.state_version)
        checks = validate_plan(s, plan)
        if not feasible(checks):
            raise PlatformError(409, "PLAN_INVALID", "Hard constraint failed or required input is UNKNOWN",
                                s.state_version, checks=[c.model_dump(mode="json") for c in checks if c.status != "PASS"])
        assignments = {a.flight_id: a for a in plan.assignments}
        # Partial commits are forbidden; this replaces every dependent reservation as one document.
        reservations = [r for r in s.resource_reservations if r.flight_id not in assignments
                        or r.starts_at_utc < s.sim_time_utc]
        for a in plan.assignments:
            reservations.extend(reservations_for(s, a))
        flights = [f.model_copy(update={"plan": assignments[f.flight_id]}) if f.flight_id in assignments else f
                   for f in s.flights]
        snap = s.model_copy(update={"resource_reservations": reservations, "flights": flights,
                                   "state_version": s.state_version + 1})
        updated = self.invalidate(doc).model_copy(update={"snapshot": snap})
        proposals = dict(updated.proposals)
        proposals[plan.proposal_id] = doc.proposals[plan.proposal_id].model_copy(update={"status": "AUTHORIZED"})
        return updated.model_copy(update={"proposals": proposals, "approvals": doc.approvals + [{
            "proposal_id": plan.proposal_id, "plan_hash": plan.plan_hash, "actor_id": actor_id,
            "decision": "APPROVE", "recorded_at_utc": utcnow().isoformat()}]})

    def decision(self, session_id, proposal_id, command, actor_id):
        def mutate(doc):
            record = doc.proposals.get(proposal_id)
            if record is None:
                raise PlatformError(404, "PROPOSAL_NOT_FOUND", "Proposal not found")
            if record.plan.plan_hash != command.plan_hash or plan_hash(record.plan) != command.plan_hash:
                raise PlatformError(409, "PLAN_CHANGED", "Proposal hash must match exact immutable plan")
            if record.status != "AWAITING_AUTHORIZATION":
                raise PlatformError(409, "PLAN_NOT_PENDING", "Proposal is " + record.status, doc.snapshot.state_version)
            if command.decision == "APPROVE":
                updated = self._apply_plan(doc, record.plan, actor_id)
            else:
                updated = doc.model_copy(update={"proposals": {**doc.proposals,
                    proposal_id: record.model_copy(update={"status": "REJECTED"})},
                    "approvals": doc.approvals + [{"proposal_id": proposal_id, "plan_hash": command.plan_hash,
                                                   "actor_id": actor_id, "decision": "REJECT",
                                                   "recorded_at_utc": utcnow().isoformat()}]})
            return updated, "PLAN_" + command.decision, {"proposal_id": proposal_id,
                "plan_hash": command.plan_hash, "decision": command.decision}, {}
        return self._execute(session_id, command, actor_id, "decision:" + proposal_id, mutate)

    def commit_plan(self, session_id: str, plan: Plan, actor_id: str) -> Snapshot:
        """Trusted integration helper still enforces authorization, identity and atomic commit."""
        from backend.contracts import DecisionCommand
        result = self.decision(session_id, plan.proposal_id,
            DecisionCommand(command_id="commit:" + plan.proposal_id, base_state_version=plan.base_state_version,
                            plan_hash=plan.plan_hash, decision="APPROVE"), actor_id)
        return Snapshot.model_validate(result["snapshot"])

    def tick(self, seconds=1):
        with self.lock:
            for doc in self.store.all_documents():
                if doc.snapshot.clock_status != "RUNNING":
                    continue
                snap = advance_clock(doc.snapshot, seconds)
                updated = self.invalidate(doc).model_copy(update={"snapshot": snap})
                event = self.event(updated, "SYSTEM", "CLOCK_TICK", uid())
                self.store.commit(updated, doc.revision, [event], "SYSTEM", uid(), "tick", {}, internal=True)

    def recover(self):
        """Pause recovered running clocks. Interrupted jobs become explicit labelled fallbacks."""
        for doc in self.store.all_documents():
            if doc.snapshot.clock_status == "RUNNING":
                snap = doc.snapshot.model_copy(update={"clock_status": "PAUSED",
                                                       "state_version": doc.snapshot.state_version + 1})
                updated = self.invalidate(doc).model_copy(update={"snapshot": snap})
                event = self.event(updated, "SYSTEM", "RECOVERY_PAUSED", uid())
                self.store.commit(updated, doc.revision, [event], "SYSTEM", uid(), "recover", {}, internal=True)
                doc = self.store.get(doc.session_id)
            for nid, record in doc.negotiations.items():
                if record.status == "RUNNING":
                    result = select_fallback(doc.snapshot, "PROCESS_RESTART")
                    self.finish_negotiation(doc.session_id, nid, "SYSTEM", doc.snapshot, None, result.plan,
                        result.checks, result.search_metadata, "PROCESS_RESTART", 0)
