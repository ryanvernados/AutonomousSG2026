"""Real socket walkthrough, including streaming response and reconnect cursor."""
import json
import socket
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from backend.api.app import create_app
from backend.api.config import Settings


def test_real_http_walkthrough_and_authenticated_sse(store):
    app = create_app(Settings(auth_mode="demo", demo_token="socket-test", _env_file=None), store=store)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1",
                          headers={"Authorization": "Bearer socket-test"}, timeout=5) as client:
            def post(path, data):
                response = client.post(path, json=data)
                response.raise_for_status()
                return response.json()
            result = post("/sessions", {"command_id": "create", "base_state_version": 0,
                                        "scenario_id": "sfo-synthetic-v1"})
            sid = result["session_id"]
            instruction = json.loads(Path("fixtures/disruptions/runway.json").read_text())
            preview = post(f"/sessions/{sid}/instructions/preview", instruction)
            assert not preview["issues"]
            post(f"/sessions/{sid}/instructions", {"command_id": "restrict", "base_state_version": 0,
                                                  "instruction": preview["instruction"]})
            started = post(f"/sessions/{sid}/negotiations", {"command_id": "start", "base_state_version": 1})
            nid = started["negotiation_id"]
            for _ in range(200):
                response = client.get(f"/sessions/{sid}/negotiations/{nid}")
                response.raise_for_status()
                proposal = response.json()["proposal"]
                if proposal:
                    break
                time.sleep(0.01)
            assert proposal["plan"]["source"] == "FALLBACK"
            assert len(proposal["plan"]["assignments"]) == 24
            plan = proposal["plan"]
            post(f"/sessions/{sid}/proposals/{plan['proposal_id']}/decision", {"command_id": "approve",
                "base_state_version": 1, "plan_hash": plan["plan_hash"], "decision": "APPROVE"})
            metrics = client.get(f"/sessions/{sid}/metrics").json()
            assert metrics["impact"]["aggregate"]["completed_movements"] == 0
            assert metrics["impact"]["aggregate"]["planned_completed_movements"] == 24
            stepped = post(f"/sessions/{sid}/clock", {"command_id": "step", "base_state_version": 2,
                                                     "action": "STEP", "seconds": 1200})
            assert stepped["state_version"] == 3
            with client.stream("GET", f"/sessions/{sid}/events", headers={"Last-Event-ID": "2"}) as stream:
                assert stream.status_code == 200
                lines = stream.iter_lines()
                assert next(line for line in lines if line.startswith("id:")) == "id: 3"
            records = [json.loads(line) for line in client.get(f"/sessions/{sid}/events/export").text.splitlines()]
            assert [r["event_seq"] for r in records] == list(range(1, 7))
            assert records[-1]["actor_id"] == "REDACTED"
            assert client.get(f"/sessions/{sid}/snapshot", headers={"Authorization": "Bearer invalid"}).status_code == 401
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()
