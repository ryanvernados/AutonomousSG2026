"""Member 3 injection boundary; no model provider or role impersonation in Member 1."""
from typing import Literal, Protocol

from backend.contracts import AgentMessage, Model, Plan, Snapshot


class CoordinationOutcome(Model):
    plan: Plan | None
    messages: list[AgentMessage]
    model_mode: Literal["MOCK", "LIVE"]
    model_calls: int
    rounds: int
    reason: str | None = None


class Coordinator(Protocol):
    async def coordinate(self, snapshot: Snapshot, affected_ids: list[str],
                         negotiation_id: str) -> CoordinationOutcome:
        """Separate role contexts; <=30s per round, <=60s total, no corrective third round.

        Sender IDs: ATC, GROUND, PILOT:<flight_id>. Both offers and ballots required.
        Call engine public functions; never commit. Include all structured messages.
        """
        ...
