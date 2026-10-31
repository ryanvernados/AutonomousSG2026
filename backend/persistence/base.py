"""Adapters commit whole session documents with atomic audit + command records.

Operational versions change on simulation commands. Store revisions change on ALL writes.
"""
from typing import Protocol

from backend.api.errors import PlatformError
from backend.contracts import AuditEvent, SessionDocument, plan_hash, utcnow
from backend.engine.validation import feasible, validate_plan


def check_transition(current: SessionDocument, updated: SessionDocument):
    """Offline adapter repeats approval checks while its write transaction is held."""
    if current.project_id != updated.project_id or current.snapshot.scenario_id != updated.snapshot.scenario_id:
        raise PlatformError(409, "SESSION_CHANGED", "Session project and scenario are immutable")
    if not current.snapshot.state_version <= updated.snapshot.state_version <= current.snapshot.state_version + 1:
        raise PlatformError(409, "STATE_CONFLICT", "Operational state must advance monotonically")
    for pid, original in current.proposals.items():
        changed = updated.proposals.get(pid)
        if changed is None or changed.plan != original.plan:
            raise PlatformError(409, "PLAN_CHANGED", "Published proposal content is immutable")
    old_ids = {a["proposal_id"] for a in current.approvals}
    for approval in updated.approvals:
        if approval["proposal_id"] in old_ids or approval["decision"] != "APPROVE":
            continue
        record = current.proposals.get(approval["proposal_id"])
        if record is None or record.status != "AWAITING_AUTHORIZATION" or (
            record.plan.plan_hash != approval["plan_hash"] or plan_hash(record.plan) != approval["plan_hash"]):
            raise PlatformError(409, "PLAN_CHANGED", "Immutable pending proposal required")
        if record.plan.expires_at_utc <= utcnow():
            raise PlatformError(409, "EXPIRED_PLAN", "Proposal expired before durable commit")
        if not feasible(validate_plan(current.snapshot, record.plan)):
            raise PlatformError(409, "PLAN_INVALID", "Approval revalidation failed inside transaction")


class Store(Protocol):
    def role(self, project_id: str, actor_id: str) -> str | None: ...
    def get(self, session_id: str) -> SessionDocument: ...
    def all_documents(self) -> list[SessionDocument]: ...
    def command(self, session_id: str, command_id: str, fingerprint: str) -> dict | None: ...
    def creation(self, actor_id: str, command_id: str, fingerprint: str) -> dict | None: ...
    def commit(self, document: SessionDocument, expected_revision: int | None, events: list[AuditEvent],
               actor_id: str, command_id: str, fingerprint: str, response: dict,
               *, creation=False, internal=False) -> dict: ...
    def events(self, session_id: str, after_seq: int = 0) -> list[AuditEvent]: ...
    def close(self): ...
