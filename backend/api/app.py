import asyncio
import json
import logging
import sqlite3
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException

from backend.api.auth import authenticate, bearer
from backend.api.config import Settings
from backend.api.errors import PlatformError
from backend.api.service import PlatformService
from backend.contracts import (
    ClockCommand,
    CommandResult,
    ControlCommand,
    CreateSession,
    DecisionCommand,
    ErrorBody,
    FallbackResult,
    Instruction,
    InstructionCommand,
    NegotiationCommand,
    NegotiationResponse,
    PreviewResponse,
    Scenario,
    SnapshotResponse,
    utcnow,
)
from backend.engine.planning import baseline_plan, compute_impact
from backend.engine.scenarios import ScenarioRepository
from backend.persistence import SQLiteStore
from backend.persistence.postgres import PostgresStore
from backend.persistence.supabase_http import SupabaseHTTPStore

log = logging.getLogger(__name__)
DEMO_USER = "00000000-0000-0000-0000-000000000001"
DEMO_PROJECT = "00000000-0000-0000-0000-000000000010"


def redact(value):
    if isinstance(value, dict):
        return {k: ("REDACTED" if any(s in k.lower() for s in (
            "token", "secret", "password", "authorization", "prompt", "email", "entered_by", "actor_id"))
            else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def create_app(settings=None, store=None, coordinator=None):
    config = settings or Settings()
    tasks = {}

    @asynccontextmanager
    async def lifespan(app):
        owned_store = store is None
        if store is not None:
            persistence = store
        elif config.auth_mode == "demo":
            if not config.demo_token:
                raise RuntimeError("Explicit DEMO_TOKEN required in demo mode")
            persistence = SQLiteStore(config.sqlite_path)
            persistence.add_member(DEMO_PROJECT, DEMO_USER)
        else:
            if not all((config.supabase_url, config.supabase_publishable_key)):
                raise RuntimeError("Configure SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY")
            if config.persistence_mode == "supabase_http":
                if not config.supabase_secret_key:
                    raise RuntimeError("Configure backend-only SUPABASE_SECRET_KEY for HTTPS persistence")
                persistence = SupabaseHTTPStore(config.supabase_url, config.supabase_secret_key)
            else:
                if not config.database_url:
                    raise RuntimeError("Configure DATABASE_URL for direct Postgres persistence")
                persistence = PostgresStore(config.database_url)
        app.state.service = PlatformService(persistence, ScenarioRepository(config.scenario_dir), coordinator,
                                            default_clock_speed=config.sim_speed)
        await asyncio.to_thread(app.state.service.recover)

        async def run_clock():
            while True:
                await asyncio.sleep(1)
                try:
                    await asyncio.to_thread(app.state.service.tick)
                except Exception:
                    log.exception("Clock persistence failed; no in-memory update applied")
        clock_task = asyncio.create_task(run_clock())
        try:
            yield
        finally:
            clock_task.cancel()
            pending = list(tasks.values())
            for task in pending:
                task.cancel()
            await asyncio.gather(clock_task, *pending, return_exceptions=True)
            if owned_store:
                persistence.close()

    app = FastAPI(title="Airport disruption simulator", version="1.0.0", lifespan=lifespan,
                  description="Member 1 v1 platform. All numerical defaults are synthetic simulation rules.",
                  responses={s: {"model": ErrorBody} for s in (401, 403, 404, 409, 422, 503)})
    app.state.settings = config
    app.add_middleware(CORSMiddleware, allow_origins=config.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "Last-Event-ID"])

    @app.exception_handler(PlatformError)
    async def platform_error(request, exc):
        return JSONResponse(exc.body.model_dump(mode="json"), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Do not reflect input values or token-bearing request bodies.
        details = [{"loc": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(ErrorBody(code="INVALID_INPUT", message="Request validation failed",
                                      details={"issues": details}).model_dump(mode="json"), status_code=422)

    @app.exception_handler(sqlite3.Error)
    async def persistence_error(request, exc):
        return JSONResponse(ErrorBody(code="PERSISTENCE_UNAVAILABLE", message="Durable transaction failed").model_dump(),
                            status_code=503)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(ErrorBody(code="HTTP_ERROR", message=str(exc.detail)).model_dump(), status_code=exc.status_code)

    @app.get("/health")
    def health():
        return {"status": "ok", "contract_version": "1", "auth_mode": config.auth_mode,
                "coordinator": "INJECTED" if coordinator else "UNAVAILABLE", "single_owner_required": True}

    prefix = "/api/v1"

    def cancel_jobs(session_id):
        import anyio

        current = app.state.service.store.get(session_id)

        def cancel():
            for (sid, nid), task in list(tasks.items()):
                if sid == session_id and current.negotiations[nid].status != "RUNNING":
                    task.cancel()
        anyio.from_thread.run_sync(cancel)

    @app.get(prefix + "/scenarios", response_model=list[Scenario])
    def scenarios(request: Request, actor=Depends(authenticate)):
        service = request.app.state.service
        return [s for s in service.scenarios.list_scenarios()
                if service.store.role(s.project_id, actor) in {"viewer", "controller", "admin"}]

    @app.post(prefix + "/sessions", response_model=CommandResult, status_code=201)
    def create_session(body: CreateSession, request: Request, actor=Depends(authenticate)):
        return request.app.state.service.create(body, actor)

    @app.get(prefix + "/sessions/{session_id}/snapshot", response_model=SnapshotResponse)
    def snapshot(session_id: str, request: Request, actor=Depends(authenticate)):
        doc = request.app.state.service.read(session_id, actor)
        proposals = [r.model_copy(update={"status": "EXPIRED"}) if r.status == "AWAITING_AUTHORIZATION"
                     and r.plan.expires_at_utc <= utcnow() else r for r in doc.proposals.values()]
        return SnapshotResponse(snapshot=doc.snapshot, proposals=proposals, negotiations=list(doc.negotiations.values()))

    @app.post(prefix + "/sessions/{session_id}/clock", response_model=CommandResult)
    def clock_command(session_id: str, body: ClockCommand, request: Request, actor=Depends(authenticate)):
        result = request.app.state.service.clock(session_id, body, actor)
        cancel_jobs(session_id)
        return result

    @app.post(prefix + "/sessions/{session_id}/instructions/preview", response_model=PreviewResponse)
    def preview(session_id: str, body: Instruction, request: Request, actor=Depends(authenticate)):
        return request.app.state.service.preview(session_id, body, actor)

    @app.post(prefix + "/sessions/{session_id}/instructions", response_model=CommandResult)
    def instruction(session_id: str, body: InstructionCommand, request: Request, actor=Depends(authenticate)):
        result = request.app.state.service.instruction(session_id, body, actor)
        cancel_jobs(session_id)
        return result

    @app.post(prefix + "/sessions/{session_id}/control", response_model=CommandResult)
    def control(session_id: str, body: ControlCommand, request: Request, actor=Depends(authenticate)):
        result = request.app.state.service.control(session_id, body, actor)
        cancel_jobs(session_id)
        return result

    @app.post(prefix + "/sessions/{session_id}/negotiations", response_model=CommandResult, status_code=202)
    def negotiate(session_id: str, body: NegotiationCommand, request: Request, actor=Depends(authenticate)):
        service = request.app.state.service
        result = service.start_negotiation(session_id, body, actor)
        # This route is synchronous; schedule on the application's loop via AnyIO's worker bridge.
        import anyio

        def schedule():
            key = (session_id, result["negotiation_id"])
            if key in tasks:
                return
            task = asyncio.create_task(service.negotiate(session_id, result["negotiation_id"], actor))
            tasks[key] = task

            def finished(completed):
                if tasks.get(key) is completed:
                    tasks.pop(key, None)
                if not completed.cancelled() and completed.exception():
                    log.error("Negotiation job failed; durable state retained", exc_info=completed.exception())
            task.add_done_callback(finished)
        anyio.from_thread.run_sync(schedule)
        return result

    @app.get(prefix + "/sessions/{session_id}/negotiations/{nid}", response_model=NegotiationResponse)
    def negotiation(session_id: str, nid: str, request: Request, actor=Depends(authenticate)):
        doc = request.app.state.service.read(session_id, actor)
        record = doc.negotiations.get(nid)
        if not record:
            raise PlatformError(404, "NEGOTIATION_NOT_FOUND", "Negotiation does not exist")
        return NegotiationResponse(negotiation=record, proposal=doc.proposals.get(record.proposal_id))

    @app.post(prefix + "/sessions/{session_id}/proposals/{pid}/decision", response_model=CommandResult)
    def decision(session_id: str, pid: str, body: DecisionCommand, request: Request, actor=Depends(authenticate)):
        return request.app.state.service.decision(session_id, pid, body, actor)

    @app.get(prefix + "/sessions/{session_id}/baseline", response_model=FallbackResult)
    def baseline(session_id: str, request: Request, actor=Depends(authenticate)):
        doc = request.app.state.service.read(session_id, actor)
        return baseline_plan(doc.snapshot)

    @app.get(prefix + "/sessions/{session_id}/metrics")
    def metrics(session_id: str, request: Request, actor=Depends(authenticate)):
        from datetime import timedelta

        from backend.contracts import Plan, seal_plan
        service = request.app.state.service
        doc = service.read(session_id, actor)
        s = doc.snapshot
        current = seal_plan(Plan(scenario_id=s.scenario_id, base_state_version=s.state_version,
            instruction_version=s.instruction_version, assignments=[f.plan for f in s.flights if f.plan],
            expires_at_utc=utcnow() + timedelta(minutes=5)))
        impact = compute_impact(s, current)
        records = list(doc.negotiations.values())
        authorized = [p for p in doc.proposals.values() if p.status == "AUTHORIZED"]
        checks = [c for p in authorized for c in p.checks]
        return {"schema_version": "1", "scenario_id": s.scenario_id, "seed": s.seed,
                "state_version": s.state_version, "horizon_start_utc": s.start_utc,
                "horizon_end_utc": s.end_utc, "rules_config": s.rules_config,
                "impact": impact, "authorization_checks": {
                    "hard_violations": sum(c.status == "FAIL" and c.severity == "HARD" for c in checks),
                    "unknown": sum(c.status == "UNKNOWN" for c in checks)},
                "active_alerts": s.alerts, "negotiation_rounds": sum(n.round for n in records),
                "model_calls": sum(n.model_calls or 0 for n in records),
                "model_call_counts_incomplete": any(n.model_calls is None for n in records),
                "negotiation_duration_seconds": sum(n.duration_seconds for n in records),
                "fallback_count": sum(p.plan.source == "FALLBACK" for p in doc.proposals.values()),
                "staff_actions": sum(e.actor_id != "SYSTEM" and e.event_type != "NEGOTIATION_FINISHED"
                                     for e in service.store.events(session_id)),
                "holding_model": "Gate delay proxy with fixed taxi duration; no fuel model"}

    @app.get(prefix + "/sessions/{session_id}/events/export")
    def export(session_id: str, request: Request, actor=Depends(authenticate)):
        service = request.app.state.service
        service.read(session_id, actor)
        records = service.store.events(session_id)
        return StreamingResponse((json.dumps(redact(e.model_dump(mode="json"))) + "\n" for e in records),
                                 media_type="application/x-ndjson",
                                 headers={"Content-Disposition": "attachment; filename=audit.jsonl"})

    @app.get(prefix + "/sessions/{session_id}/events")
    async def events(session_id: str, request: Request, after_seq: int = Query(default=0, ge=0),
                     actor=Depends(authenticate)):
        service = request.app.state.service
        await asyncio.to_thread(service.read, session_id, actor)
        try:
            cursor = max(after_seq, int(request.headers.get("Last-Event-ID", "0")))
        except ValueError as exc:
            raise PlatformError(422, "INVALID_CURSOR", "Last-Event-ID must be an integer") from exc

        async def stream():
            nonlocal cursor
            heartbeat = 0
            while not await request.is_disconnected():
                try:
                    await asyncio.to_thread(service.read, session_id, actor)
                    if heartbeat >= 60:
                        await authenticate(request, await bearer(request))
                        heartbeat = 0
                    records = await asyncio.to_thread(service.store.events, session_id, cursor)
                except PlatformError as exc:
                    yield "event: error\ndata: " + exc.body.model_dump_json() + "\n\n"
                    return
                for e in records:
                    cursor = e.event_seq
                    yield f"id: {cursor}\nevent: {e.event_type}\ndata: {json.dumps(redact(e.model_dump(mode='json')))}\n\n"
                if heartbeat % 30 == 0:
                    yield ": heartbeat\n\n"
                heartbeat += 1
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


app = create_app()
