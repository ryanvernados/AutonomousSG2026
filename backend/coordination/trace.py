"""Structured persistence bridge and redacted JSONL exports."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .protocol import Json, ProtocolError, clone


def redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(term in k.lower() for term in
                    ("api_key", "authorization", "password", "secret", "credential", "tail_number", "entered_by"))
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"\b(?:sk|sb_secret)-[A-Za-z0-9_-]+", "[REDACTED]", value)
        value = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", value)
        return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED EMAIL]", value)
    return value


class MemoryPersistence:
    """Explicit local mock. Production supplies Member 1's durable adapter."""
    def __init__(self):
        self.records: list[Json] = []

    async def append(self, record: Json) -> None:
        self.records.append(clone(record))


class Trace:
    def __init__(self, negotiation_id: str, persistence):
        self.negotiation_id, self.persistence = negotiation_id, persistence
        self.records: list[Json] = []
        self._lock = asyncio.Lock()

    async def emit(self, event_type: str, payload: Json) -> None:
        async with self._lock:
            record = {"record_id": str(uuid4()), "negotiation_id": self.negotiation_id,
                      "sequence": len(self.records) + 1, "event_type": event_type,
                      "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "payload": clone(payload)}
            try:
                await self.persistence.append(clone(record))
            except Exception as exc:
                raise ProtocolError("PERSISTENCE_UNAVAILABLE") from exc
            self.records.append(record)

    def export_jsonl(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(json.dumps(redact(r), sort_keys=True, allow_nan=False) + "\n"
                                  for r in self.records), encoding="utf-8")

