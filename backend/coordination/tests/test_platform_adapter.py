"""Member 3 coordinator through the Member 1 platform boundary (needs pydantic and simpy)."""
import asyncio
import importlib.util
import unittest

HAVE_PLATFORM = all(importlib.util.find_spec(m) for m in ("pydantic", "simpy"))


@unittest.skipUnless(HAVE_PLATFORM, "Member 1 platform dependencies not installed")
class PlatformAdapterTest(unittest.TestCase):
    def run_case(self, behavior):
        from backend.agents.adapters import MockAdapter
        from backend.api.service import PlatformService
        from backend.contracts import NegotiationRecord
        from backend.coordination.platform_adapter import PlatformCoordinator
        from backend.engine.scenarios import ScenarioRepository, initial_snapshot

        snapshot = initial_snapshot(ScenarioRepository("fixtures").load_scenario("sfo-synthetic-v1"))
        affected = sorted(f.flight_id for f in snapshot.flights)
        record = NegotiationRecord(affected_ids=affected)
        outcome = asyncio.run(PlatformCoordinator(MockAdapter(behavior)).coordinate(
            snapshot, affected, record.negotiation_id))
        return snapshot, record, outcome, PlatformService._validate_outcome

    def test_consensus_is_accepted_by_member1_validation(self):
        snapshot, record, outcome, validate = self.run_case("consensus")
        self.assertIsNone(outcome.reason)
        self.assertEqual(outcome.rounds, 2)
        self.assertEqual(outcome.plan.source, "CONSENSUS")
        senders = {m.sender_id for m in outcome.messages}
        self.assertEqual(senders, {"ATC", "GROUND"} | {"PILOT:" + f for f in record.affected_ids})
        self.assertTrue(all(isinstance(r, str) for m in outcome.messages for r in m.reasons))
        validate(None, snapshot, record, outcome)  # raises if Member 1 rejects the outcome

    def test_failures_never_reach_consensus_or_a_third_round(self):
        for behavior, reason in (("pilot_veto", "AGENT_VETO"), ("ground_conflict", "AGENT_VETO"),
                                 ("invalid_message", "INVALID_MESSAGE"), ("missing_ballot", "MISSING_BALLOT"),
                                 ("wrong_hash", "BALLOT_IDENTITY_MISMATCH")):
            with self.subTest(behavior=behavior):
                snapshot, record, outcome, validate = self.run_case(behavior)
                self.assertIsNone(outcome.plan)
                self.assertEqual(outcome.reason, reason)
                self.assertLessEqual(outcome.rounds, 2)
                with self.assertRaises(ValueError):
                    validate(None, snapshot, record, outcome)


class RunInProcessTest(unittest.TestCase):
    def test_cancelled_work_does_not_outlive_its_negotiation(self):
        import multiprocessing
        import time

        from backend.coordination.platform_adapter import run_in_process

        self.assertEqual(asyncio.run(run_in_process(sum, [1, 2, 3])), 6)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            asyncio.run(asyncio.wait_for(run_in_process(time.sleep, 60), timeout=2))
        self.assertLess(time.monotonic() - started, 30)
        deadline = time.monotonic() + 10
        while multiprocessing.active_children() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(multiprocessing.active_children(), [])


if __name__ == "__main__":
    unittest.main()
