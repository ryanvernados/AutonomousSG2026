"""Canonical v1 contract. UTC internally; intervals are half-open [start, end)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


def uid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="after")
    @classmethod
    def normalize_time(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        return value


class Assumption(Model):
    value: Any
    source_type: Literal["HISTORICAL", "SOURCED", "SYNTHETIC"]
    unit: str
    explanation: str = ""


class Interval(Model):
    resource_id: str
    starts_at_utc: AwareDatetime
    ends_at_utc: AwareDatetime
    quantity: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.ends_at_utc <= self.starts_at_utc:
            raise ValueError("Interval end must be after start")
        return self


class Reservation(Interval):
    reservation_id: str = Field(default_factory=uid)
    flight_id: str
    kind: Literal["RUNWAY", "TAXIWAY", "GATE", "CREW", "EQUIPMENT"]
    # Carried by initial occupancy when its flight is outside the traffic slice.
    wake_category: str | None = None


class Resource(Model):
    resource_id: str
    kind: Literal["RUNWAY", "TAXIWAY", "GATE", "CREW", "EQUIPMENT"]
    capacity: int = Field(default=1, ge=1)
    node_from: str | None = None
    node_to: str | None = None
    bidirectional: bool = True
    dependent_runway_ids: list[str] = Field(default_factory=list)
    compatible_categories: list[str] = Field(default_factory=list)
    available_from_utc: AwareDatetime | None = None
    available_until_utc: AwareDatetime | None = None
    max_work_seconds: int | None = Field(default=None, gt=0)
    minimum_traversal_seconds: int = Field(default=30, gt=0)


class Infrastructure(Model):
    resources: list[Resource]
    source_type: Literal["SOURCED", "SYNTHETIC"]
    effective_date: str
    coverage_limits: list[str]

    @model_validator(mode="after")
    def unique_resources(self):
        if len({r.resource_id for r in self.resources}) != len(self.resources):
            raise ValueError("Resource IDs must be unique")
        return self


class RulesConfig(Model):
    source_type: Literal["SYNTHETIC", "SOURCED"] = "SYNTHETIC"
    runway_occupancy_seconds: int = Field(default=60, gt=0)
    wake_separation_seconds: dict[str, int] = Field(
        default_factory=lambda: {"MEDIUM:MEDIUM": 120, "HEAVY:MEDIUM": 180,
                                 "MEDIUM:HEAVY": 120, "HEAVY:HEAVY": 150})
    minimum_turnaround_seconds: int = Field(default=1200, ge=0)
    search_step_seconds: int = Field(default=120, gt=0)
    search_limit: int = Field(default=100, ge=1, le=100)
    proposal_validity_seconds: int = Field(default=300, gt=0)
    round_budget_seconds: float = Field(default=30, gt=0, le=30)
    total_budget_seconds: float = Field(default=60, gt=0, le=60)
    objective_weights: dict[str, float] = Field(
        default_factory=lambda: {"delay_minutes": 1, "holding_minutes": 2,
                                 "taxi_minutes": 0.2, "max_wait_minutes": 0.5})

    @field_validator("wake_separation_seconds", "objective_weights")
    @classmethod
    def nonnegative_values(cls, value):
        if any(v < 0 for v in value.values()):
            raise ValueError("Rule values must be nonnegative")
        return value


class Assignment(Model):
    flight_id: str
    operation: Literal["ARRIVAL", "DEPARTURE"]
    runway_id: str
    runway_time_utc: AwareDatetime
    taxi_path_ids: list[str]
    taxi_start_utc: AwareDatetime
    taxi_end_utc: AwareDatetime
    gate_id: str | None = None
    gate_start_utc: AwareDatetime | None = None
    gate_end_utc: AwareDatetime | None = None
    crew_reservations: list[Interval]
    equipment_reservations: list[Interval]


class Flight(Model):
    flight_id: str
    source_row_id: str
    airline: str
    flight_number: str
    tail_number: str | None
    origin: str
    destination: str
    scheduled_gate_departure_utc: AwareDatetime
    scheduled_gate_arrival_utc: AwareDatetime
    state: Literal["SCHEDULED", "HOLDING", "TAXIING", "AT_GATE", "TURNAROUND", "DEPARTED", "COMPLETED"]
    plan: Assignment | None
    assumptions: dict[str, Assumption]
    actual_gate_departure_utc: AwareDatetime | None = None
    actual_gate_arrival_utc: AwareDatetime | None = None
    wheels_on_utc: AwareDatetime | None = None
    wheels_off_utc: AwareDatetime | None = None


class Scenario(Model):
    schema_version: Literal["1"] = "1"
    scenario_id: str
    project_id: str
    airport_iata: str
    timezone: str
    seed: int
    start_utc: AwareDatetime
    end_utc: AwareDatetime
    source_manifest: dict[str, Any]
    rules_config: RulesConfig
    flights: list[Flight]
    infrastructure: Infrastructure
    initial_occupancy: list[Reservation]

    @model_validator(mode="after")
    def valid_window(self):
        if self.end_utc <= self.start_utc:
            raise ValueError("Scenario end must follow start")
        if len({f.flight_id for f in self.flights}) != len(self.flights):
            raise ValueError("Flight IDs must be unique")
        return self


class Instruction(Model):
    instruction_id: str = Field(default_factory=uid)
    instruction_version: int = Field(default=0, ge=0)
    scenario_id: str
    base_state_version: int = Field(ge=0)
    kind: Literal["RUNWAY_UNAVAILABLE", "TAXIWAY_BLOCKED", "EQUIPMENT_FAILURE", "PROTECTED_SLOT"]
    resource_id: str | None
    starts_at_utc: AwareDatetime
    ends_at_utc: AwareDatetime | None
    flight_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    entered_by: str = ""

    @model_validator(mode="after")
    def interval_valid(self):
        if self.ends_at_utc and self.ends_at_utc <= self.starts_at_utc:
            raise ValueError("Restriction end must follow start")
        return self


class CheckResult(Model):
    constraint_id: str
    domain: str
    severity: Literal["HARD", "SOFT"] = "HARD"
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    affected_ids: list[str]
    measured_value: float | str | None = None
    limit_value: float | str | None = None
    unit: str | None = None
    explanation: str


class Impact(Model):
    per_flight: dict[str, dict[str, float | bool]] = Field(default_factory=dict)
    aggregate: dict[str, float] = Field(default_factory=dict)
    versus_baseline: dict[str, float] = Field(default_factory=dict)
    objective_weights: dict[str, float] = Field(default_factory=dict)
    score: float = 0
    unresolved_flight_ids: list[str] = Field(default_factory=list)
    unfinished_flight_ids: list[str] = Field(default_factory=list)


class Plan(Model):
    proposal_id: str = Field(default_factory=uid)
    proposal_version: int = Field(default=1, ge=1)
    scenario_id: str
    base_state_version: int = Field(ge=0)
    instruction_version: int = Field(ge=0)
    plan_hash: str = ""
    assignments: list[Assignment]
    impact: Impact = Field(default_factory=Impact)
    expires_at_utc: AwareDatetime
    source: Literal["CONSENSUS", "FALLBACK", "BASELINE", "MANUAL"] = "FALLBACK"


def plan_hash(plan: Plan) -> str:
    content = plan.model_dump(mode="json", exclude={"plan_hash"})
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def seal_plan(plan: Plan) -> Plan:
    return plan.model_copy(update={"plan_hash": plan_hash(plan)})


class Snapshot(Model):
    schema_version: Literal["1"] = "1"
    scenario_id: str
    state_version: int = Field(ge=0)
    sim_time_utc: AwareDatetime
    mode: Literal["MANUAL", "ASSISTED"] = "ASSISTED"
    flights: list[Flight]
    resource_reservations: list[Reservation]
    active_instructions: list[Instruction]
    data_quality: dict[str, Any]
    instruction_version: int = 0
    clock_status: Literal["PAUSED", "RUNNING"] = "PAUSED"
    infrastructure: Infrastructure
    rules_config: RulesConfig
    start_utc: AwareDatetime
    end_utc: AwareDatetime
    airport_iata: str
    seed: int


PlanningStatus = Literal["PROPOSED", "CONSENSUS_REACHED", "AWAITING_AUTHORIZATION", "AUTHORIZED",
                         "REJECTED", "STALE", "EXPIRED", "NO_FEASIBLE_PLAN_FOUND", "RUNNING", "CANCELLED"]


class AgentMessage(Model):
    message_id: str = Field(default_factory=uid)
    negotiation_id: str
    round: int = Field(ge=1, le=2)
    sender_id: str
    role: Literal["ATC", "PILOT", "GROUND"]
    type: Literal["OFFER", "ACCEPT", "REJECT"]
    base_state_version: int
    instruction_version: int
    proposal_id: str | None
    plan_hash: str | None
    payload: dict[str, Any]
    reasons: list[str]


class AuditEvent(Model):
    event_id: str = Field(default_factory=uid)
    event_seq: int
    scenario_id: str
    state_version: int
    sim_time_utc: AwareDatetime
    recorded_at_utc: AwareDatetime = Field(default_factory=utcnow)
    actor_id: str
    event_type: str
    correlation_id: str
    payload: dict[str, Any]


class ProposalRecord(Model):
    plan: Plan
    status: PlanningStatus = "AWAITING_AUTHORIZATION"
    checks: list[CheckResult]


class NegotiationRecord(Model):
    negotiation_id: str = Field(default_factory=uid)
    status: PlanningStatus = "RUNNING"
    model_mode: Literal["MOCK", "LIVE", "UNAVAILABLE"] = "UNAVAILABLE"
    affected_ids: list[str]
    round: int = 0
    model_calls: int = 0
    duration_seconds: float = 0
    messages: list[AgentMessage] = Field(default_factory=list)
    proposal_id: str | None = None
    reason: str | None = None
    search_metadata: dict[str, Any] = Field(default_factory=dict)


class SessionDocument(Model):
    session_id: str = Field(default_factory=uid)
    project_id: str
    revision: int = 0  # durable compare-and-swap, separate from operational state_version
    snapshot: Snapshot
    initial_snapshot: Snapshot
    proposals: dict[str, ProposalRecord] = Field(default_factory=dict)
    negotiations: dict[str, NegotiationRecord] = Field(default_factory=dict)
    approvals: list[dict[str, Any]] = Field(default_factory=list)


class Command(Model):
    command_id: str = Field(min_length=1, max_length=128)
    base_state_version: int = Field(ge=0)


class CreateSession(Command):
    scenario_id: str


class ClockCommand(Command):
    action: Literal["PAUSE", "RESUME", "STEP", "RESET"]
    seconds: int = Field(default=60, gt=0, le=7200)


class InstructionCommand(Command):
    instruction: Instruction


class NegotiationCommand(Command):
    affected_ids: list[str] = Field(default_factory=list)


class DecisionCommand(Command):
    plan_hash: str
    decision: Literal["APPROVE", "REJECT"]


class ControlCommand(Command):
    mode: Literal["MANUAL", "ASSISTED"]


class CommandResult(Model):
    session_id: str
    state_version: int
    event_ids: list[str]
    snapshot: Snapshot | None = None
    negotiation_id: str | None = None


class ErrorBody(Model):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    current_state_version: int | None = None


class FallbackResult(Model):
    status: Literal["AWAITING_AUTHORIZATION", "NO_FEASIBLE_PLAN_FOUND"]
    reason: str
    plan: Plan | None
    checks: list[CheckResult]
    search_metadata: dict[str, Any]


class SnapshotResponse(Model):
    snapshot: Snapshot
    proposals: list[ProposalRecord]
    negotiations: list[NegotiationRecord]


class NegotiationResponse(Model):
    negotiation: NegotiationRecord
    proposal: ProposalRecord | None


class PreviewResponse(Model):
    instruction: Instruction
    issues: list[str]
    state_version: int
