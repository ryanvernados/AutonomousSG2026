import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.agents.adapters import MockAdapter, ResponsesAdapter
from backend.coordination.coordinator import Budgets, Coordinator
from backend.coordination.mock_engine import MockEngine, stamp
from backend.coordination.protocol import ProtocolError, clone, hard_checks_pass
from backend.coordination.trace import MemoryPersistence, Trace, redact


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, behavior="consensus", engine=None, model=None, persistence=None, budgets=None):
        engine = engine or MockEngine()
        model = model or MockAdapter(behavior)
        before = clone(engine.snapshot)
        result = await Coordinator(engine, model, persistence or MemoryPersistence(), budgets).run(
            "test-session", ["DEPART-1"], instruction_version=1,
            objective={"weights": {"total_runway_wait_minutes": 1, "max_runway_wait_minutes": 1}},
            baseline_plan=engine.plan("baseline", arrival=14, departure=10))
        self.assertLessEqual(result["metrics"]["rounds"], 2)
        self.assertLessEqual(result["metrics"]["model_calls"], 8)
        self.assertEqual(engine.snapshot, before)
        self.assertEqual([r["sequence"] for r in result["trace"]], list(range(1, len(result["trace"]) + 1)))
        return result, engine, model

    async def test_unanimous_consensus_with_dependency_expansion(self):
        result, engine, model = await self.run_case()
        self.assertEqual(result["status"], "AWAITING_AUTHORIZATION")
        self.assertEqual(result["decision"], "CONSENSUS")
        self.assertEqual(result["metrics"]["rounds"], 2)
        self.assertEqual(result["metrics"]["model_calls"], 8)
        self.assertEqual(result["metrics"]["message_count"], 8)
        self.assertEqual(result["affected_ids"], ["ARRIVE-1", "DEPART-1"])
        self.assertEqual(set(engine.validation_schedule_sizes), {3})
        self.assertEqual(engine.fallback_calls, 0)
        ballots = [c for c in model.calls if c["round"] == 2]
        self.assertEqual({c["plan_hash"] for c in ballots}, {result["proposal"]["plan_hash"]})
        self.assertEqual(len({c["agent_id"] for c in ballots}), 4)
        self.assertEqual(len({c["instructions"] for c in ballots}), 3)
        for c in ballots:
            self.assertEqual(c["history"][1]["reply"]["type"], "OFFER")
            if c["role"] == "PILOT":
                self.assertEqual({w["subject_id"] for w in c["view"]["authorized_windows"]}, {c["flight_id"]})
        delta = result["proposal"]["impact"]["runway_delta_minutes_by_flight"]
        self.assertEqual(delta, {"ARRIVE-1": -4, "DEPART-1": 4})

    async def test_pilot_veto(self):
        result, engine, _ = await self.run_case("pilot_veto")
        self.assertEqual(result["decision"], "FALLBACK")
        self.assertEqual(result["reason"], "AGENT_VETO")
        self.assertEqual(engine.fallback_calls, 1)

    async def test_ground_conflict(self):
        result, _, _ = await self.run_case("ground_conflict")
        self.assertEqual(result["decision"], "FALLBACK")
        self.assertEqual(result["reason"], "AGENT_VETO")

    async def test_malformed_offer_stops_before_ballots(self):
        result, _, model = await self.run_case("invalid_message")
        self.assertEqual(result["reason"], "INVALID_MESSAGE")
        self.assertEqual(result["metrics"]["rounds"], 1)
        self.assertEqual({c["round"] for c in model.calls}, {1})

    async def test_missing_ballot(self):
        result, _, _ = await self.run_case("missing_ballot")
        self.assertEqual(result["reason"], "MISSING_BALLOT")
        self.assertEqual(result["decision"], "FALLBACK")

    async def test_hash_mismatch(self):
        result, _, _ = await self.run_case("wrong_hash")
        self.assertEqual(result["reason"], "BALLOT_IDENTITY_MISMATCH")

    async def test_round_timeout_stays_inside_budget(self):
        start = time.monotonic()
        result, _, _ = await self.run_case("timeout", budgets=Budgets(.03, .3, .1))
        self.assertEqual(result["reason"], "ROUND_TIMEOUT")
        self.assertEqual(result["decision"], "FALLBACK")
        self.assertLess(time.monotonic() - start, .3)

    async def test_stale_snapshot_after_model_calls(self):
        class StaleEngine(MockEngine):
            reads = 0
            async def get_snapshot(self, session_id):
                self.reads += 1
                snapshot = await super().get_snapshot(session_id)
                if self.reads >= 3:
                    snapshot["state_version"] += 1
                return snapshot
        result, engine, _ = await self.run_case(engine=StaleEngine())
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(engine.fallback_calls, 0)
        self.assertNotIn("proposal", result)

    async def test_stale_snapshot_after_ballots(self):
        class StaleEngine(MockEngine):
            reads = 0
            async def get_snapshot(self, session_id):
                self.reads += 1
                snapshot = await super().get_snapshot(session_id)
                if self.reads >= 5:
                    snapshot["state_version"] += 1
                return snapshot
        result, _, _ = await self.run_case(engine=StaleEngine())
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["metrics"]["rounds"], 2)

    async def test_manual_mode_prevents_model_calls(self):
        engine = MockEngine()
        engine.snapshot["mode"] = "MANUAL"
        result, _, _ = await self.run_case(engine=engine)
        self.assertEqual(result["reason"], "MANUAL_MODE")
        self.assertEqual(result["metrics"]["model_calls"], 0)

    async def test_clock_must_be_paused(self):
        engine = MockEngine()
        engine.snapshot["data_quality"]["clock_paused"] = False
        result, _, _ = await self.run_case(engine=engine)
        self.assertEqual(result["reason"], "SIMULATION_NOT_PAUSED")

    async def test_unknown_fuel_prevents_consensus_and_fallback_proposal(self):
        result, _, _ = await self.run_case(engine=MockEngine(failure="unknown_fuel"))
        self.assertEqual(result["status"], "NO_FEASIBLE_PLAN_FOUND")
        self.assertEqual(result["reason"], "UNKNOWN_FUEL_LIMIT")
        self.assertNotIn("proposal", result)

    async def test_ground_unavailable_prevents_any_proposal(self):
        result, _, _ = await self.run_case(engine=MockEngine(failure="ground_unavailable"))
        self.assertEqual(result["status"], "NO_FEASIBLE_PLAN_FOUND")

    async def test_empty_search_reports_limits_without_claiming_impossibility(self):
        result, _, _ = await self.run_case(engine=MockEngine(failure="no_candidates"))
        self.assertEqual(result["status"], "NO_FEASIBLE_PLAN_FOUND")
        self.assertEqual(result["search_limit"], 100)

    async def test_empty_validation_is_not_success(self):
        class EmptyValidator(MockEngine):
            async def validate_plan(self, snapshot, plan):
                return []
        result, _, _ = await self.run_case(engine=EmptyValidator())
        self.assertEqual(result["status"], "NO_FEASIBLE_PLAN_FOUND")

    async def test_candidate_limit_enforced(self):
        class TooMany(MockEngine):
            async def enumerate_candidates(self, snapshot, constraints, limit=100):
                return {"plans": [self.plan("x")] * 101, "limit": limit, "search_exhausted": False}
        result, _, _ = await self.run_case(engine=TooMany())
        self.assertEqual(result["reason"], "INVALID_CANDIDATE_BATCH")

    async def test_candidate_cannot_change_a_flight_without_its_pilot(self):
        class ExtraFlight(MockEngine):
            async def enumerate_candidates(self, snapshot, constraints, limit=100):
                plan = self.plan("extra-flight")
                extra = clone(plan["assignments"][1])
                extra["flight_id"] = "UNAFFECTED-1"
                extra["runway_time_utc"] = stamp(20)
                plan["assignments"].append(extra)
                return {"plans": [plan], "limit": limit, "search_exhausted": True}
        result, _, _ = await self.run_case(engine=ExtraFlight())
        self.assertEqual(result["decision"], "FALLBACK")
        self.assertEqual(result["metrics"]["rounds"], 1)
        self.assertTrue(any(r["event_type"] == "CANDIDATE_REJECTED" and r["payload"]["reason"] == "PARTICIPANT_COVERAGE_MISMATCH" for r in result["trace"]))

    async def test_unknown_hard_check_rejected_but_soft_failure_allowed(self):
        self.assertFalse(hard_checks_pass([{"severity": "HARD", "status": "UNKNOWN"}]))
        self.assertFalse(hard_checks_pass([{"severity": "HARD", "status": "PASS"}, {}]))
        self.assertTrue(hard_checks_pass([{"severity": "HARD", "status": "PASS"}, {"severity": "SOFT", "status": "FAIL"}]))

    async def test_expired_plan_cannot_reach_ballots(self):
        def past():
            return datetime.now(timezone.utc) - timedelta(days=1)

        result, _, _ = await self.run_case(engine=MockEngine(now=past))
        self.assertNotIn("proposal", result)
        self.assertEqual(result["metrics"]["rounds"], 1)

    async def test_pilot_cannot_widen_or_omit_fuel_limit(self):
        for omit in (False, True):
            class FuelTamper(MockAdapter):
                async def generate(self, request, timeout_seconds):
                    response = await super().generate(request, timeout_seconds)
                    if request["round"] == 1 and request["flight_id"] == "ARRIVE-1":
                        if omit:
                            response.payload["constraints"] = [c for c in response.payload["constraints"] if c["kind"] != "LATEST_SAFE_ARRIVAL"]
                        else:
                            response.payload["constraints"][-1]["ends_at_utc"] = stamp(59)
                    return response
            result, _, _ = await self.run_case(model=FuelTamper())
            self.assertEqual(result["metrics"]["rounds"], 1)
            self.assertIn(result["reason"], ("UNAUTHORIZED_CONSTRAINT", "MISSING_FUEL_LIMIT"))

    async def test_agents_cannot_change_other_flights_constraints(self):
        class Impersonate(MockAdapter):
            async def generate(self, request, timeout_seconds):
                response = await super().generate(request, timeout_seconds)
                if request["role"] == "PILOT" and request["round"] == 1:
                    response.payload["constraints"][0]["subject_id"] = "UNAFFECTED-1"
                return response
        result, _, _ = await self.run_case(model=Impersonate())
        self.assertEqual(result["reason"], "UNAUTHORIZED_CONSTRAINT")

    async def test_persistence_failure_cannot_return_consensus(self):
        class BrokenStore:
            async def append(self, record):
                raise RuntimeError("secret must not be logged")
        result, _, _ = await self.run_case(persistence=BrokenStore())
        self.assertEqual(result["reason"], "PERSISTENCE_UNAVAILABLE")
        self.assertEqual(result["metrics"]["model_calls"], 0)
        self.assertNotIn("secret must not be logged", json.dumps(result))

    async def test_final_persistence_failure_removes_proposal(self):
        class FailFinal(MemoryPersistence):
            async def append(self, record):
                if record["event_type"] == "NEGOTIATION_FINISHED":
                    raise RuntimeError("unavailable")
                await super().append(record)
        result, _, _ = await self.run_case(persistence=FailFinal())
        self.assertEqual(result["status"], "FAILED")
        self.assertNotIn("proposal", result)

    async def test_global_timeout_covers_engine_and_fallback(self):
        class SlowEngine(MockEngine):
            async def enumerate_candidates(self, *args):
                await asyncio.sleep(2)
            async def select_fallback(self, *args):
                await asyncio.sleep(2)
        start = time.monotonic()
        result, _, _ = await self.run_case(engine=SlowEngine(), budgets=Budgets(.03, .12, .04))
        self.assertEqual(result["reason"], "TOTAL_TIMEOUT")
        self.assertLess(time.monotonic() - start, .4)

    async def test_cancelled_task_has_no_fallback_or_later_round(self):
        model, engine = MockAdapter("timeout"), MockEngine()
        persistence = MemoryPersistence()
        task = asyncio.create_task(self.run_case(model=model, engine=engine, persistence=persistence))
        await asyncio.sleep(.03)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(engine.fallback_calls, 0)
        self.assertEqual({r["round"] for r in model.calls}, {1})
        self.assertEqual(persistence.records[-1]["event_type"], "NEGOTIATION_CANCELLED")

    async def test_candidate_generator_cannot_ignore_a_narrowed_offer(self):
        class NarrowPilot(MockAdapter):
            async def generate(self, request, timeout_seconds):
                response = await super().generate(request, timeout_seconds)
                if request["round"] == 1 and request["flight_id"] == "DEPART-1":
                    response.payload["constraints"][0]["ends_at_utc"] = stamp(11)
                return response
        class IgnoreOffers(MockEngine):
            async def enumerate_candidates(self, snapshot, constraints, limit=100):
                return {"plans": [self.plan("ignores-offer")], "limit": limit, "search_exhausted": True}
        result, _, _ = await self.run_case(model=NarrowPilot(), engine=IgnoreOffers())
        self.assertEqual(result["decision"], "FALLBACK")
        self.assertEqual(result["metrics"]["rounds"], 1)
        self.assertTrue(any(r["event_type"] == "CANDIDATE_REJECTED" and r["payload"]["reason"] == "OFFER_WINDOW_VIOLATION" for r in result["trace"]))

    async def test_ranking_uses_frozen_objective_over_feasible_plans(self):
        class MultipleCandidates(MockEngine):
            async def enumerate_candidates(self, snapshot, constraints, limit=100):
                return {"plans": [self.plan("slower", departure=18), self.plan("faster", departure=14)],
                        "limit": limit, "search_exhausted": True}
        result, _, _ = await self.run_case(engine=MultipleCandidates())
        self.assertEqual(result["proposal"]["proposal_id"], "faster")

    async def test_empty_fallback_cannot_be_authorized(self):
        class EmptyFallback(MockEngine):
            async def select_fallback(self, snapshot, reason):
                result = await super().select_fallback(snapshot, reason)
                result["plan"]["assignments"] = []
                result["plan"]["plan_hash"] = self.plan_hash(result["plan"])
                return result
        result, _, _ = await self.run_case("pilot_veto", engine=EmptyFallback())
        self.assertEqual(result["reason"], "INCOMPLETE_PLAN")
        self.assertNotIn("proposal", result)

    async def test_total_budget_includes_slow_persistence(self):
        class SlowStore(MemoryPersistence):
            async def append(self, record):
                await asyncio.sleep(2)
        start = time.monotonic()
        result, _, _ = await self.run_case(persistence=SlowStore(), budgets=Budgets(.03, .12, .04))
        self.assertEqual(result["reason"], "TOTAL_TIMEOUT")
        self.assertLess(time.monotonic() - start, .4)

    async def test_trace_sequences_survive_concurrent_async_persistence(self):
        class YieldingStore(MemoryPersistence):
            async def append(self, record):
                await asyncio.sleep(0)
                await super().append(record)
        result, _, _ = await self.run_case(persistence=YieldingStore())
        self.assertEqual(result["decision"], "CONSENSUS")

    async def test_changes_to_instruction_content_invalidate_plan(self):
        class ChangedInstruction(MockEngine):
            reads = 0
            async def get_snapshot(self, session_id):
                self.reads += 1
                snapshot = await super().get_snapshot(session_id)
                if self.reads >= 3:
                    snapshot["active_instructions"][0]["ends_at_utc"] = stamp(20)
                return snapshot
        result, _, _ = await self.run_case(engine=ChangedInstruction())
        self.assertEqual(result["status"], "STALE")

    async def test_full_schedule_validator_sees_nonparticipant_conflicts(self):
        engine = MockEngine()
        checks = await engine.validate_plan(engine.snapshot, engine.plan("collision", departure=30))
        self.assertEqual(next(c for c in checks if c["constraint_id"] == "mock:nonparticipant-occupancy")["status"], "FAIL")

    async def test_jsonl_export_is_redacted_and_valid(self):
        result, _, _ = await self.run_case()
        trace = Trace(result["negotiation_id"], MemoryPersistence())
        trace.records = result["trace"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            trace.export_jsonl(path)
            records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(records), len(trace.records))
        self.assertEqual(redact({"api_key": "test", "detail": "sk-secret123 Bearer token"}),
                         {"api_key": "[REDACTED]", "detail": "[REDACTED] Bearer [REDACTED]"})


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_refusal_incomplete_and_invalid_json_fail_closed(self):
        cases = [({"status": "incomplete"}, "MODEL_INCOMPLETE"),
                 ({"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}, "MODEL_REFUSAL"),
                 ({"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not JSON"}]}]}, "INVALID_MESSAGE")]
        for data, expected in cases:
            class Response:
                def raise_for_status(self):
                    pass
                def json(self):
                    return data
            class Client:
                async def post(self, *args, **kwargs):
                    return Response()
            with self.assertRaises(ProtocolError) as caught:
                await ResponsesAdapter(Client(), model="test", api_key="test").generate({"instructions": "test"}, .1)
            self.assertEqual(caught.exception.code, expected)

    async def test_live_request_uses_structured_output_and_no_retry(self):
        class Response:
            def raise_for_status(self):
                pass
            def json(self):
                return {"status": "completed", "model": "test-provider-model", "usage": {"input_tokens": 7, "output_tokens": 9, "total_tokens": 16},
                        "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"example": true}'}]}]}
        class Client:
            calls = []
            async def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()
        client = Client()
        adapter = ResponsesAdapter(client, model="test-model", api_key="test-only")
        response = await adapter.generate({"instructions": "domain instructions", "round": 1}, .5)
        self.assertEqual(len(client.calls), 1)
        body = client.calls[0][1]["json"]
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual(response.usage["total_tokens"], 16)

    async def test_live_transport_errors_are_sanitized(self):
        class BrokenClient:
            count = 0
            async def post(self, *args, **kwargs):
                self.count += 1
                raise RuntimeError("Bearer super-secret")
        client = BrokenClient()
        with self.assertRaises(ProtocolError) as caught:
            await ResponsesAdapter(client, model="test", api_key="test").generate({"instructions": "test"}, .1)
        self.assertEqual(str(caught.exception), "MODEL_TRANSPORT_ERROR")
        self.assertEqual(client.count, 1)


if __name__ == "__main__":
    unittest.main()
