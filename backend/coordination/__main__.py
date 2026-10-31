"""Run a Member 3 protocol demo, always with an explicitly MOCK engine."""
import argparse
import asyncio
import json
from pathlib import Path

from backend.agents.adapters import MockAdapter, ResponsesAdapter

from .coordinator import Budgets, Coordinator
from .mock_engine import MockEngine
from .trace import MemoryPersistence, Trace


async def demo(args):
    engine = MockEngine(failure=args.engine_failure)
    persistence = MemoryPersistence()
    budgets = Budgets(round_seconds=args.round_seconds, total_seconds=args.total_seconds,
                      fallback_reserve_seconds=min(2, args.total_seconds / 4))

    async def run(model):
        return await Coordinator(engine, model, persistence, budgets).run(
            "demo-session", ["DEPART-1"], instruction_version=1,
            objective={"weights": {"total_runway_wait_minutes": 1, "max_runway_wait_minutes": 1},
                       "source_type": "SYNTHETIC"},
            baseline_plan=engine.plan("comparison-fcfs", arrival=14, departure=10))

    if args.mode == "live":
        try:
            import httpx
        except ImportError as exc:
            raise SystemExit("LIVE needs: python -m pip install -r backend/coordination/requirements-live.txt") from exc
        async with httpx.AsyncClient(follow_redirects=False) as client:
            result = await run(ResponsesAdapter.from_env(client))
    else:
        result = await run(MockAdapter(args.behavior))
    trace = Trace(result["negotiation_id"], persistence)
    trace.records = result.pop("trace")
    trace.export_jsonl(args.trace)
    result.update(engine_mode="MOCK", persistence_mode="MOCK", trace_file=str(args.trace),
                  historical_traffic=False, authorized=False)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "AWAITING_AUTHORIZATION" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument("--behavior", choices=["consensus", "pilot_veto", "ground_conflict", "invalid_message", "missing_ballot", "timeout", "wrong_hash"], default="consensus")
    parser.add_argument("--engine-failure", choices=["unknown_fuel", "no_candidates", "ground_unavailable", "unaffected_conflict"])
    parser.add_argument("--round-seconds", type=float, default=30)
    parser.add_argument("--total-seconds", type=float, default=60)
    parser.add_argument("--trace", type=Path, default=Path("backend/coordination/output/trace.jsonl"))
    args = parser.parse_args()
    if args.mode == "live" and args.behavior != "consensus":
        parser.error("--behavior is a mock-only fault injection option")
    try:
        raise SystemExit(asyncio.run(demo(args)))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

