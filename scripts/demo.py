"""HTTP walkthrough against a running backend; never bypasses staff command checks."""
import argparse
import json
import time
from pathlib import Path

import httpx

from backend.api.config import Settings
from backend.contracts import uid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/v1")
    args = parser.parse_args()
    settings = Settings()
    if settings.auth_mode != "demo":
        raise SystemExit("This walkthrough requires the explicit local demo configuration.")
    with httpx.Client(base_url=args.url, headers={"Authorization": "Bearer " + settings.demo_token}, timeout=10) as client:
        def post(path, data):
            response = client.post(path, json=data)
            response.raise_for_status()
            return response.json()

        created = post("/sessions", {"command_id": uid(), "base_state_version": 0,
                                      "scenario_id": "sfo-synthetic-v1"})
        sid = created["session_id"]
        instruction = json.loads(Path("fixtures/disruptions/runway.json").read_text())
        preview = post(f"/sessions/{sid}/instructions/preview", instruction)
        assert not preview["issues"]
        confirmed = post(f"/sessions/{sid}/instructions", {"command_id": uid(), "base_state_version": 0,
                                                           "instruction": preview["instruction"]})
        started = post(f"/sessions/{sid}/negotiations", {"command_id": uid(),
            "base_state_version": confirmed["state_version"], "affected_ids": []})
        nid = started["negotiation_id"]
        for _ in range(300):
            response = client.get(f"/sessions/{sid}/negotiations/{nid}")
            response.raise_for_status()
            record = response.json()
            if record["negotiation"]["status"] != "RUNNING":
                break
            time.sleep(0.1)
        proposal = record["proposal"]
        assert proposal and proposal["plan"]["source"] == "FALLBACK"
        assert not any(c["severity"] == "HARD" and c["status"] != "PASS" for c in proposal["checks"])
        p = proposal["plan"]
        approved = post(f"/sessions/{sid}/proposals/{p['proposal_id']}/decision", {"command_id": uid(),
            "base_state_version": p["base_state_version"], "plan_hash": p["plan_hash"], "decision": "APPROVE"})
        stepped = post(f"/sessions/{sid}/clock", {"command_id": uid(),
            "base_state_version": approved["state_version"], "action": "STEP", "seconds": 1200})
        # Fetch SSE using Authorization headers and verify reconnection cursor skips earlier events.
        with client.stream("GET", f"/sessions/{sid}/events", headers={"Last-Event-ID": "2"}) as stream:
            stream.raise_for_status()
            first_id = next(line for line in stream.iter_lines() if line.startswith("id:"))
            assert int(first_id.split(":", 1)[1]) == 3
        print(json.dumps({"session_id": sid, "proposal_source": p["source"], "assignments": len(p["assignments"]),
            "state_version": stepped["state_version"], "sim_time_utc": stepped["snapshot"]["sim_time_utc"],
            "sse_reconnected_from_sequence": 2, "hard_checks": "PASS"}, indent=2))


if __name__ == "__main__":
    main()
