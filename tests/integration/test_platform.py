import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from backend.api.app import DEMO_PROJECT, DEMO_USER, create_app
from backend.api.auth import authenticate
from backend.api.config import Settings
from backend.api.coordinator import CoordinationOutcome
from backend.api.errors import PlatformError
from backend.api.service import PlatformService
from backend.contracts import (
    AgentMessage,
    ClockCommand,
    ControlCommand,
    CreateSession,
    DecisionCommand,
    Instruction,
    InstructionCommand,
    NegotiationCommand,
    seal_plan,
    uid,
)
from backend.engine.planning import select_fallback
from backend.engine.scenarios import ScenarioRepository
from backend.persistence import SQLiteStore


def propose(service, session):
    result = service.start_negotiation(session, NegotiationCommand(command_id=uid(), base_state_version=
        service.get_snapshot(session).state_version), DEMO_USER)
    asyncio.run(service.negotiate(session, result["negotiation_id"], DEMO_USER))
    doc = service.store.get(session)
    record = doc.negotiations[result["negotiation_id"]]
    return doc.proposals[record.proposal_id].plan


def decision(plan, command_id=None, version=None, action="APPROVE"):
    return DecisionCommand(command_id=command_id or uid(), base_state_version=
        plan.base_state_version if version is None else version, plan_hash=plan.plan_hash, decision=action)


def test_authorization_boundary_and_idempotent_approval(service, session):
    plan = propose(service, session)
    assert service.get_snapshot(session).flights[0].plan is None
    assert not service.store.get(session).approvals
    cmd = decision(plan)
    result = service.decision(session, plan.proposal_id, cmd, DEMO_USER)
    assert result["state_version"] == 1
    before = len(service.store.events(session))
    assert service.decision(session, plan.proposal_id, cmd, DEMO_USER) == result
    assert len(service.store.events(session)) == before
    assert len(service.store.get(session).approvals) == 1
    assert len(service.get_snapshot(session).resource_reservations) > 100


def test_rejection_keeps_schedule_unchanged(service, session):
    plan = propose(service, session)
    before = service.get_snapshot(session)
    service.decision(session, plan.proposal_id, decision(plan, action="REJECT"), DEMO_USER)
    assert service.get_snapshot(session) == before


def test_stale_hash_and_expiration(service, session, monkeypatch):
    plan = propose(service, session)
    with pytest.raises(PlatformError, match="hash"):
        service.decision(session, plan.proposal_id, decision(plan).model_copy(update={"plan_hash": "wrong"}), DEMO_USER)
    monkeypatch.setattr("backend.api.service.utcnow", lambda: plan.expires_at_utc + timedelta(seconds=1))
    with pytest.raises(PlatformError, match="expired"):
        service.decision(session, plan.proposal_id, decision(plan), DEMO_USER)


def test_changed_state_stales_pending_plan(service, session):
    plan = propose(service, session)
    service.clock(session, ClockCommand(command_id="step", base_state_version=0, action="STEP"), DEMO_USER)
    with pytest.raises(PlatformError) as exc:
        service.decision(session, plan.proposal_id, decision(plan), DEMO_USER)
    assert exc.value.status == 409
    assert service.store.get(session).proposals[plan.proposal_id].status == "STALE"


def test_database_rollback_and_retry(service, session):
    plan = propose(service, session)
    before = service.store.get(session)
    events = service.store.events(session)
    cmd = decision(plan)

    def fail():
        raise sqlite3.IntegrityError("Injected transaction failure after all writes")
    service.store.before_commit = fail
    with pytest.raises(sqlite3.IntegrityError):
        service.decision(session, plan.proposal_id, cmd, DEMO_USER)
    assert service.store.get(session) == before
    assert service.store.events(session) == events
    service.store.before_commit = None
    assert service.decision(session, plan.proposal_id, cmd, DEMO_USER)["state_version"] == 1


def test_duplicate_concurrent_command_and_reused_id(service, session):
    cmd = ClockCommand(command_id="once", base_state_version=0, action="STEP")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.clock(session, cmd, DEMO_USER), range(2)))
    assert results[0] == results[1] and service.get_snapshot(session).state_version == 1
    with pytest.raises(PlatformError) as exc:
        service.clock(session, cmd.model_copy(update={"seconds": 120}), DEMO_USER)
    assert exc.value.body.code == "COMMAND_REUSED"


def test_checkpoint_recovery(tmp_path):
    path = str(tmp_path / "restart.sqlite3")
    store = SQLiteStore(path)
    store.add_member(DEMO_PROJECT, DEMO_USER)
    service = PlatformService(store, ScenarioRepository())
    session = service.create(CreateSession(command_id="new", base_state_version=0,
        scenario_id="sfo-synthetic-v1"), DEMO_USER)["session_id"]
    plan = propose(service, session)
    service.decision(session, plan.proposal_id, decision(plan), DEMO_USER)
    before = service.store.get(session)
    store.close()
    restored_store = SQLiteStore(path)
    restored = PlatformService(restored_store, ScenarioRepository())
    restored.recover()
    assert restored_store.get(session) == before
    assert restored_store.events(session)[-1].event_type == "PLAN_APPROVE"
    restored_store.close()


def test_manual_disables_replanning_and_preserves_restrictions(service, session):
    instruction = Instruction(scenario_id="sfo-synthetic-v1", base_state_version=0,
        kind="RUNWAY_UNAVAILABLE", resource_id="28R", starts_at_utc=service.get_snapshot(session).sim_time_utc,
        ends_at_utc=None)
    service.instruction(session, InstructionCommand(command_id="restrict", base_state_version=0,
                                                   instruction=instruction), DEMO_USER)
    plan = propose(service, session)
    service.control(session, ControlCommand(command_id="manual", base_state_version=1, mode="MANUAL"), DEMO_USER)
    snap = service.get_snapshot(session)
    assert snap.active_instructions and snap.mode == "MANUAL"
    assert service.store.get(session).proposals[plan.proposal_id].status == "REJECTED"
    with pytest.raises(PlatformError) as exc:
        service.start_negotiation(session, NegotiationCommand(command_id="disabled", base_state_version=2), DEMO_USER)
    assert exc.value.body.code == "MANUAL_MODE"


def test_preview_read_only_and_conflicting_instruction(service, session):
    before = service.store.get(session)
    instruction = Instruction(scenario_id="sfo-synthetic-v1", base_state_version=0,
        kind="TAXIWAY_BLOCKED", resource_id="NOT-A-SEGMENT", starts_at_utc=before.snapshot.start_utc,
        ends_at_utc=None)
    assert service.preview(session, instruction, DEMO_USER)["issues"]
    assert service.store.get(session) == before


class TimeoutCoordinator:
    async def coordinate(self, *args):
        await asyncio.sleep(1)


def test_timeout_labelled_fallback(service, session):
    doc = service.store.get(session)
    snapshot = doc.snapshot.model_copy(update={"rules_config": doc.snapshot.rules_config.model_copy(update={
        "round_budget_seconds": 0.01, "total_budget_seconds": 0.02})})
    service.store.commit(doc.model_copy(update={"snapshot": snapshot}), doc.revision, [], DEMO_USER,
                         "budget", "budget", {}, internal=True)
    service.coordinator = TimeoutCoordinator()
    plan = propose(service, session)
    record = next(iter(service.store.get(session).negotiations.values()))
    assert record.reason == "MODEL_TIMEOUT" and plan.source == "FALLBACK"
    assert record.round <= 2 and not service.store.get(session).approvals


class MockCoordinator:
    def __init__(self, veto=False, wrong_hash=False):
        self.veto, self.wrong_hash = veto, wrong_hash

    async def coordinate(self, snapshot, affected_ids, negotiation_id):
        plan = select_fallback(snapshot, "MOCK").plan
        plan = seal_plan(plan.model_copy(update={"source": "CONSENSUS"}))
        participants = [("ATC", "ATC"), ("GROUND", "GROUND")] + [("PILOT:" + f, "PILOT") for f in affected_ids]
        messages = [AgentMessage(negotiation_id=negotiation_id, round=round_no, sender_id=sender, role=role,
            type="OFFER" if round_no == 1 else "REJECT" if self.veto and role == "PILOT" else "ACCEPT",
            base_state_version=snapshot.state_version, instruction_version=snapshot.instruction_version,
            proposal_id=plan.proposal_id if round_no == 2 else None,
            plan_hash=("wrong" if self.wrong_hash else plan.plan_hash) if round_no == 2 else None,
            payload={}, reasons=[]) for round_no in (1, 2) for sender, role in participants]
        return CoordinationOutcome(plan=plan, messages=messages, model_mode="MOCK", model_calls=0, rounds=2)


@pytest.mark.parametrize("veto,wrong_hash,expected", [(False, False, "CONSENSUS"),
    (True, False, "FALLBACK"), (False, True, "FALLBACK")])
def test_coordinator_exact_ballots_and_veto(service, session, veto, wrong_hash, expected):
    service.coordinator = MockCoordinator(veto, wrong_hash)
    plan = propose(service, session)
    assert plan.source == expected
    assert not service.store.get(session).approvals
    assert next(iter(service.store.get(session).negotiations.values())).model_mode == "MOCK"


class VetoReasonCoordinator:
    async def coordinate(self, snapshot, affected_ids, negotiation_id):
        return CoordinationOutcome(plan=None, messages=[], model_mode="MOCK", model_calls=4, rounds=2,
                                   reason="AGENT_VETO")


def test_coordinator_reason_kept_in_fallback_label(service, session):
    service.coordinator = VetoReasonCoordinator()
    plan = propose(service, session)
    record = next(iter(service.store.get(session).negotiations.values()))
    assert plan.source == "FALLBACK"
    assert record.reason == "INVALID_OR_FAILED_COORDINATION:AGENT_VETO"


def test_api_async_flow_and_export(client):
    response = client.post("/api/v1/sessions", json={"scenario_id": "sfo-synthetic-v1", "command_id": "create",
                                                   "base_state_version": 0})
    assert response.status_code == 201
    sid = response.json()["session_id"]
    started = time.monotonic()
    response = client.post(f"/api/v1/sessions/{sid}/negotiations", json={"command_id": "negotiate",
        "base_state_version": 0, "affected_ids": []})
    assert response.status_code == 202 and time.monotonic() - started < 1
    nid = response.json()["negotiation_id"]
    for _ in range(200):
        data = client.get(f"/api/v1/sessions/{sid}/negotiations/{nid}").json()
        if data["negotiation"]["status"] != "RUNNING":
            break
        time.sleep(0.01)
    assert data["proposal"]["plan"]["source"] == "FALLBACK"
    p = data["proposal"]["plan"]
    approved = client.post(f"/api/v1/sessions/{sid}/proposals/{p['proposal_id']}/decision",
        json={"command_id": "approve", "base_state_version": 0, "plan_hash": p["plan_hash"], "decision": "APPROVE"})
    assert approved.status_code == 200 and approved.json()["state_version"] == 1
    events = client.get(f"/api/v1/sessions/{sid}/events/export").text
    assert '"event_seq": 4' in events and "REDACTED" in events
    assert client.get(f"/api/v1/sessions/{sid}/events", headers={"Last-Event-ID": "oops"}).status_code == 422


def test_unauthorized_reads_and_writes(client, store):
    sid = client.post("/api/v1/sessions", json={"command_id": "new", "base_state_version": 0,
                                             "scenario_id": "sfo-synthetic-v1"}).json()["session_id"]
    assert client.get("/api/v1/scenarios", headers={"Authorization": "Bearer wrong"}).status_code == 401
    outsider = "00000000-0000-0000-0000-000000000002"
    client.app.dependency_overrides[authenticate] = lambda: outsider
    assert client.get(f"/api/v1/sessions/{sid}/snapshot").status_code == 403
    assert client.post(f"/api/v1/sessions/{sid}/clock", json={"command_id": "steal", "base_state_version": 0,
                                                         "action": "STEP"}).status_code == 403
    store.add_member(DEMO_PROJECT, outsider, "viewer")
    assert client.get(f"/api/v1/sessions/{sid}/snapshot").status_code == 200
    assert client.post(f"/api/v1/sessions/{sid}/clock", json={"command_id": "viewer-write", "base_state_version": 0,
                                                         "action": "STEP"}).status_code == 403


def test_uniform_validation_errors(client):
    response = client.post("/api/v1/sessions", json={"scenario_id": "sfo-synthetic-v1"})
    assert response.status_code == 422
    assert set(response.json()) == {"code", "message", "details", "current_state_version"}


def test_restart_recovers_interrupted_negotiation(service, session):
    result = service.start_negotiation(session, NegotiationCommand(command_id="interrupted", base_state_version=0), DEMO_USER)
    restored = PlatformService(service.store, ScenarioRepository())
    restored.recover()
    doc = restored.store.get(session)
    record = doc.negotiations[result["negotiation_id"]]
    assert record.reason == "PROCESS_RESTART"
    assert doc.proposals[record.proposal_id].plan.source == "FALLBACK"


def test_no_default_insecure_auth():
    config = Settings(_env_file=None)
    assert config.auth_mode == "supabase" and not config.demo_token
    assert create_app(config).openapi()["info"]["version"] == "1.0.0"


def test_malformed_coordinator_result_uses_fallback(service, session):
    class Malformed:
        async def coordinate(self, *args):
            return {"made_up": "not a typed response"}
    service.coordinator = Malformed()
    plan = propose(service, session)
    assert plan.source == "FALLBACK"
    assert next(iter(service.store.get(session).negotiations.values())).model_calls is None


def test_proposals_immutable_in_offline_store(service, session):
    plan = propose(service, session)
    doc = service.store.get(session)
    changed = seal_plan(plan.model_copy(update={"source": "MANUAL"}))
    record = doc.proposals[plan.proposal_id].model_copy(update={"plan": changed})
    with pytest.raises(PlatformError, match="immutable"):
        service.store.commit(doc.model_copy(update={"proposals": {plan.proposal_id: record}}), doc.revision,
                             [], DEMO_USER, uid(), "changed", {}, internal=True)


def test_confirmed_conflict_remains_visible_in_manual_mode(service, session):
    plan = propose(service, session)
    service.decision(session, plan.proposal_id, decision(plan), DEMO_USER)
    instruction = Instruction(scenario_id="sfo-synthetic-v1", base_state_version=1,
        kind="RUNWAY_UNAVAILABLE", resource_id="28L", starts_at_utc=service.get_snapshot(session).start_utc,
        ends_at_utc=None)
    service.instruction(session, InstructionCommand(command_id="conflict", base_state_version=1,
                                                   instruction=instruction), DEMO_USER)
    assert service.get_snapshot(session).alerts
    service.control(session, ControlCommand(command_id="manual-alert", base_state_version=2, mode="MANUAL"), DEMO_USER)
    assert service.get_snapshot(session).alerts


def test_retry_does_not_start_duplicate_model_job(store):
    from fastapi.testclient import TestClient

    class BlockingCoordinator:
        calls = 0
        cancelled = False

        async def coordinate(self, *args):
            self.calls += 1
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    coordinator = BlockingCoordinator()
    app = create_app(Settings(auth_mode="demo", demo_token="test-token", _env_file=None),
                     store=store, coordinator=coordinator)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-token"
        sid = client.post("/api/v1/sessions", json={"command_id": "new", "base_state_version": 0,
                                                  "scenario_id": "sfo-synthetic-v1"}).json()["session_id"]
        payload = {"command_id": "start", "base_state_version": 0}
        first = client.post(f"/api/v1/sessions/{sid}/negotiations", json=payload)
        assert first.status_code == 202
        assert client.post(f"/api/v1/sessions/{sid}/negotiations", json=payload).json() == first.json()
        for _ in range(100):
            if coordinator.calls:
                break
            time.sleep(0.005)
        assert coordinator.calls == 1
        assert client.post(f"/api/v1/sessions/{sid}/control", json={"command_id": "manual",
            "base_state_version": 0, "mode": "MANUAL"}).status_code == 200
        for _ in range(100):
            if coordinator.cancelled:
                break
            time.sleep(0.005)
        assert coordinator.cancelled


def test_clock_speed_scales_running_ticks(service, session):
    version = service.get_snapshot(session).state_version
    service.clock(session, ClockCommand(command_id=uid(), base_state_version=version, action="RESUME", speed=10),
                  DEMO_USER)
    before = service.get_snapshot(session)
    service.tick()
    after = service.get_snapshot(session)
    assert after.clock_speed == 10 and (after.sim_time_utc - before.sim_time_utc).total_seconds() == 10
    service.clock(session, ClockCommand(command_id=uid(), base_state_version=after.state_version, action="SPEED",
                                        speed=60), DEMO_USER)
    service.tick()
    assert (service.get_snapshot(session).sim_time_utc - after.sim_time_utc).total_seconds() == 60
    assert service.get_snapshot(session).clock_status == "RUNNING"

