"""One adapter call per agent per round. No retries or hidden repair turns."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Protocol

from backend.coordination.protocol import Json, REPLY_SCHEMA, ProtocolError, clone


@dataclass(frozen=True)
class ModelResponse:
    payload: Json
    model: str
    usage: Json | None = None


class ModelAdapter(Protocol):
    mode: str
    model: str

    async def generate(self, request: Json, timeout_seconds: float) -> ModelResponse: ...


class MockAdapter:
    mode = "MOCK"
    model = "deterministic-domain-v1"

    def __init__(self, behavior: str = "consensus"):
        self.behavior = behavior
        self.calls: list[Json] = []

    async def generate(self, request: Json, timeout_seconds: float) -> ModelResponse:
        self.calls.append(clone(request))
        round_number, role = request["round"], request["role"]
        if self.behavior == "timeout" and role == "GROUND":
            await asyncio.sleep(timeout_seconds + 1)
        if self.behavior == "missing_ballot" and round_number == 2 and role == "GROUND":
            raise ProtocolError("MISSING_BALLOT")
        reply = {key: request[key] for key in ("base_state_version", "instruction_version", "proposal_id", "plan_hash")}
        reply.update(type="OFFER" if round_number == 1 else "ACCEPT",
                     constraints=clone(request["view"]["authorized_windows"]) if round_number == 1 else [], reasons=[])
        if round_number == 2 and ((self.behavior == "pilot_veto" and role == "PILOT") or
                                  (self.behavior == "ground_conflict" and role == "GROUND")):
            reply.update(type="REJECT", reasons=[{"code": "DOMAIN_CONFLICT", "detail": "Mock domain veto",
                                                   "affected_ids": [request["flight_id"]] if request["flight_id"] else []}])
        if self.behavior == "invalid_message" and round_number == 1 and role == "ATC":
            reply["rewrite_fuel"] = 999
        if self.behavior == "wrong_hash" and round_number == 2 and role == "GROUND":
            reply["plan_hash"] = "wrong"
        return ModelResponse(reply, self.model)


class ResponsesAdapter:
    """OpenAI Responses HTTP adapter; provider-neutral coordinator uses ModelAdapter.

    A caller-owned httpx.AsyncClient enables pooled, cancellable requests and
    transport testing. No client or credentials are put into the trace.
    """
    mode = "LIVE"

    def __init__(self, client, *, model: str, api_key: str):
        if not model or not api_key:
            raise ValueError("Set AIRPORT_MODEL and OPENAI_API_KEY for LIVE mode")
        self.client, self.model, self._api_key = client, model, api_key

    @classmethod
    def from_env(cls, client):
        return cls(client, model=os.environ.get("AIRPORT_MODEL", ""), api_key=os.environ.get("OPENAI_API_KEY", ""))

    async def generate(self, request: Json, timeout_seconds: float) -> ModelResponse:
        body = {
            "model": self.model, "store": False,
            "instructions": request["instructions"],
            "input": json.dumps({k: v for k, v in request.items() if k != "instructions"}, allow_nan=False),
            "text": {"format": {"type": "json_schema", "name": "airport_agent_reply",
                                  "strict": True, "schema": REPLY_SCHEMA}},
        }
        try:
            response = await self.client.post("https://api.openai.com/v1/responses", json=body,
                                              headers={"Authorization": f"Bearer {self._api_key}"},
                                              timeout=timeout_seconds)
            response.raise_for_status()
            data = response.json()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Provider exceptions can contain headers/URLs/body; never persist str(exc).
            raise ProtocolError("MODEL_TRANSPORT_ERROR") from exc
        if data.get("status") != "completed":
            raise ProtocolError("MODEL_INCOMPLETE")
        parts = [p for item in data.get("output", []) if item.get("type") == "message"
                 for p in item.get("content", [])]
        if any(p.get("type") == "refusal" for p in parts):
            raise ProtocolError("MODEL_REFUSAL")
        texts = [p["text"] for p in parts if p.get("type") == "output_text"]
        if len(texts) != 1 or len(texts[0]) > 100000:
            raise ProtocolError("INVALID_MESSAGE")
        try:
            payload = json.loads(texts[0])
        except (ValueError, TypeError) as exc:
            raise ProtocolError("INVALID_MESSAGE") from exc
        usage = data.get("usage") or {}
        safe_usage = {key: usage[key] for key in ("input_tokens", "output_tokens", "total_tokens")
                      if type(usage.get(key)) is int}
        return ModelResponse(payload, data.get("model", self.model), safe_usage)
