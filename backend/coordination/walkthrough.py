"""End-to-end HTTP walkthrough of the integrated API (Members 1, 2 and 3).

    python -m backend.coordination.walkthrough --scenario sfo-synthetic-v1 [--url http://127.0.0.1:8000]

Needs a running ``backend.coordination.server`` in AUTH_MODE=demo; the token comes from
DEMO_TOKEN. Steps: create a session, preview and confirm a runway restriction, start a
negotiation, wait for the result, check consensus evidence, approve the exact plan hash,
then advance the clock. Prints one JSON summary; exits non-zero on a failed step.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta

import httpx


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario", default="sfo-synthetic-v1")
    parser.add_argument("--runway", default=None, help="Runway to restrict (default: first runway)")
    parser.add_argument("--approve", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--wait", type=float, default=90,
                        help="Seconds to wait for the negotiation (the 218-flight scenario takes minutes)")
    args = parser.parse_args(argv)
    token = os.environ.get("DEMO_TOKEN")
    if not token:
        raise SystemExit("Set DEMO_TOKEN to the backend's demo token.")
    summary = {"scenario_id": args.scenario}
    with httpx.Client(base_url=args.url + "/api/v1", headers={"Authorization": "Bearer " + token},
                      timeout=max(30.0, args.wait)) as client:
        def post(path, body, expect=(200, 201, 202)):
            r = client.post(path, json={"command_id": str(uuid.uuid4()), **body})
            if r.status_code not in expect:
                raise SystemExit(f"POST {path} -> {r.status_code}: {r.text[:300]}")
            return r.json()

        created = post("/sessions", {"base_state_version": 0, "scenario_id": args.scenario})
        sid, snap = created["session_id"], created["snapshot"]
        summary.update(session_id=sid, flights=len(snap["flights"]), initial_state_version=snap["state_version"])
        runways = sorted(r["resource_id"] for r in snap["infrastructure"]["resources"] if r["kind"] == "RUNWAY")
        runway = args.runway or runways[0]
        start = datetime.fromisoformat(snap["sim_time_utc"])
        draft = {"scenario_id": args.scenario, "base_state_version": snap["state_version"],
                 "kind": "RUNWAY_UNAVAILABLE", "resource_id": runway,
                 "starts_at_utc": start.isoformat(), "ends_at_utc": (start + timedelta(minutes=30)).isoformat(),
                 "flight_id": None, "parameters": {}}
        preview = client.post(f"/sessions/{sid}/instructions/preview", json=draft).json()
        summary["preview_issues"] = preview["issues"]
        if preview["issues"]:
            raise SystemExit(f"Preview issues: {preview['issues']}")
        confirmed = post(f"/sessions/{sid}/instructions",
                         {"base_state_version": snap["state_version"], "instruction": preview["instruction"]})
        summary["restriction"] = {"runway": runway, "state_version": confirmed["state_version"]}

        started = post(f"/sessions/{sid}/negotiations",
                       {"base_state_version": confirmed["state_version"], "affected_ids": []})
        nid = started["negotiation_id"]
        deadline = time.monotonic() + args.wait
        while True:
            n = client.get(f"/sessions/{sid}/negotiations/{nid}").json()
            if n["negotiation"]["status"] != "RUNNING" or time.monotonic() > deadline:
                break
            time.sleep(0.5)
        neg, proposal = n["negotiation"], n["proposal"]
        rounds = {r: sorted({(m["type"]) for m in neg["messages"] if m["round"] == r}) for r in (1, 2)}
        summary["negotiation"] = {
            "status": neg["status"], "reason": neg["reason"], "model_mode": neg["model_mode"],
            "rounds": neg["round"], "model_calls": neg["model_calls"], "messages": len(neg["messages"]),
            "message_types_by_round": rounds, "participants": len({m["sender_id"] for m in neg["messages"]}),
            "duration_seconds": round(neg["duration_seconds"], 3),
        }
        if not proposal:
            summary["result"] = "NO_PLAN"
            print(json.dumps(summary, indent=2))
            return 0 if neg["status"] == "NO_FEASIBLE_PLAN_FOUND" else 1
        plan = proposal["plan"]
        hard = [c for c in proposal["checks"] if c["severity"] == "HARD"]
        summary["proposal"] = {"source": plan["source"], "assignments": len(plan["assignments"]),
                               "plan_hash": plan["plan_hash"][:16],
                               "hard_checks": f"{sum(c['status'] == 'PASS' for c in hard)}/{len(hard)} PASS",
                               "score": plan["impact"]["score"]}
        # The published schedule must not change until approval.
        before = client.get(f"/sessions/{sid}/snapshot").json()["snapshot"]
        summary["published_assignments_before_approval"] = sum(f["plan"] is not None for f in before["flights"])
        if args.approve:
            approved = post(f"/sessions/{sid}/proposals/{plan['proposal_id']}/decision",
                            {"base_state_version": before["state_version"], "plan_hash": plan["plan_hash"],
                             "decision": "APPROVE"})
            after = approved["snapshot"]
            summary["approved"] = {"state_version": after["state_version"],
                                   "published_assignments": sum(f["plan"] is not None for f in after["flights"]),
                                   "reservations": len(after["resource_reservations"])}
            stepped = post(f"/sessions/{sid}/clock", {"base_state_version": after["state_version"],
                                                       "action": "STEP", "seconds": 600})
            summary["clock_after_step"] = stepped["snapshot"]["sim_time_utc"]
        summary["result"] = proposal["status"] if not args.approve else "AUTHORIZED"
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
