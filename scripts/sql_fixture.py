"""Emit real service commit envelopes for the embedded PostgreSQL verification suite."""
import asyncio
import json

from backend.api.app import DEMO_PROJECT, DEMO_USER
from backend.api.service import PlatformService
from backend.contracts import CreateSession, DecisionCommand, NegotiationCommand
from backend.engine.scenarios import ScenarioRepository
from backend.persistence import SQLiteStore


def main():
    store = SQLiteStore(":memory:")
    store.add_member(DEMO_PROJECT, DEMO_USER)
    original = store.commit
    commits = []

    def capture(document, expected_revision, events, actor_id, command_id, fingerprint, response, **kwargs):
        result = original(document, expected_revision, events, actor_id, command_id, fingerprint, response, **kwargs)
        commits.append({"document": document.model_dump(mode="json"), "expected_revision": expected_revision,
            "events": [e.model_dump(mode="json") for e in events], "actor_id": actor_id,
            "command_id": command_id, "fingerprint": fingerprint, "response": response,
            "creation": kwargs.get("creation", False), "internal": kwargs.get("internal", False)})
        return result
    store.commit = capture
    service = PlatformService(store, ScenarioRepository())
    sid = service.create(CreateSession(command_id="create", base_state_version=0,
        scenario_id="sfo-synthetic-v1"), DEMO_USER)["session_id"]
    result = service.start_negotiation(sid, NegotiationCommand(command_id="start", base_state_version=0), DEMO_USER)
    asyncio.run(service.negotiate(sid, result["negotiation_id"], DEMO_USER))
    doc = store.get(sid)
    plan = next(iter(doc.proposals.values())).plan
    service.decision(sid, plan.proposal_id, DecisionCommand(command_id="approve", base_state_version=0,
        decision="APPROVE", plan_hash=plan.plan_hash), DEMO_USER)
    print(json.dumps(commits))
    store.close()


if __name__ == "__main__":
    main()
