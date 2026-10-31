"""Saved trace evidence is replayed structurally, never authorized or committed."""
import json
import unittest
from pathlib import Path

from backend.coordination.mock_engine import mock_plan_hash


class TraceFixtureTests(unittest.TestCase):
    def test_saved_mock_traces_have_consistent_rounds_identities_and_counts(self):
        directory = Path(__file__).resolve().parents[1] / "fixtures"
        for name in ("mock_consensus", "mock_pilot_veto", "mock_timeout"):
            with self.subTest(trace=name):
                records = [json.loads(line) for line in (directory / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual([r["sequence"] for r in records], list(range(1, len(records) + 1)))
                self.assertEqual(len({r["negotiation_id"] for r in records}), 1)
                requests = [r["payload"] for r in records if r["event_type"] == "AGENT_REQUEST"]
                messages = [r["payload"] for r in records if r["event_type"] == "AGENT_MESSAGE"]
                calls = [r["payload"] for r in records if r["event_type"] == "MODEL_CALL"]
                finished = records[-1]["payload"]
                self.assertEqual(records[-1]["event_type"], "NEGOTIATION_FINISHED")
                self.assertEqual(finished["metrics"]["model_calls"], len(calls))
                self.assertEqual(finished["metrics"]["message_count"], len(messages))
                self.assertTrue(all(r["round"] <= 2 and r["model_mode"] == "MOCK" for r in requests))
                self.assertEqual(len({(r["round"], r["agent_id"]) for r in requests}), len(requests))
                self.assertEqual(finished["status"], "AWAITING_AUTHORIZATION")
                if name == "mock_consensus":
                    proposal = finished["proposal"]
                    self.assertEqual(proposal["plan_hash"], mock_plan_hash(proposal))
                    ballots = [m for m in messages if m["round"] == 2]
                    self.assertEqual(len(ballots), 4)
                    self.assertTrue(all(m["type"] == "ACCEPT" and m["plan_hash"] == proposal["plan_hash"] for m in ballots))
                else:
                    self.assertEqual(finished["decision"], "FALLBACK")
