"""Member 1 integration seams. Implement async wrappers around its shared models.

No commit_plan method is deliberately exposed to the coordinator or agents.
"""
from typing import Protocol

from .protocol import Json


class EnginePort(Protocol):
    async def get_snapshot(self, session_id: str) -> Json: ...
    async def dependencies(self, snapshot: Json, flight_id: str) -> list[str]: ...
    async def domain_view(self, snapshot: Json, role: str, flight_id: str | None,
                          affected_ids: list[str]) -> Json: ...
    async def enumerate_candidates(self, snapshot: Json, constraints: list[Json],
                                   limit: int = 100) -> Json:
        """Return {plans: list[Plan], search_exhausted: bool, limit: int}."""
        ...
    async def validate_plan(self, snapshot: Json, plan: Json) -> list[Json]: ...
    async def compute_impact(self, snapshot: Json, plan: Json, baseline_plan: Json) -> Json: ...
    async def select_fallback(self, snapshot: Json, reason: str) -> Json:
        """Return {plan: Plan | None, search_exhausted: bool, limit: int}."""
        ...
    def plan_hash(self, plan: Json) -> str:
        """Use Member 1's canonical hash utility, excluding only plan_hash."""
        ...


class PersistencePort(Protocol):
    async def append(self, record: Json) -> None:
        """Persist a server-generated negotiation record through Member 1's adapter.

        Raise on failure. record_id is the idempotency key. Never execute raw LLM
        payloads as database commands. Retention/access policies belong to Member 1.
        """
        ...

