"""Local wire validation, not a replacement for Member 1's canonical models.

All Scenario/Snapshot/Plan/CheckResult objects cross this boundary as JSON dicts.
The integration adapter must validate them with backend.contracts when available.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

Json = dict[str, Any]


class ProtocolError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def clone(value: Any) -> Any:
    """Copy JSON only; reject NaN and non-serializable engine data."""
    return json.loads(json.dumps(value, allow_nan=False))


def utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError("UTC required")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProtocolError("INVALID_UTC") from exc


def object_schema(properties: Json) -> Json:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


REASON_SCHEMA = object_schema({
    "code": {"type": "string"}, "detail": {"type": "string"},
    "affected_ids": {"type": "array", "items": {"type": "string"}},
})
CONSTRAINT_SCHEMA = object_schema({
    "kind": {"type": "string", "enum": ["RUNWAY_WINDOW", "TIMING_WINDOW", "LATEST_SAFE_ARRIVAL", "GROUND_WINDOW"]},
    "subject_id": {"type": "string"},
    "resource_id": {"type": ["string", "null"]},
    "starts_at_utc": {"type": ["string", "null"]},
    "ends_at_utc": {"type": "string"},
    "unit": {"type": "string", "enum": ["UTC"]},
    "source_type": {"type": "string", "enum": ["HISTORICAL", "SOURCED", "SYNTHETIC"]},
})
REPLY_SCHEMA = object_schema({
    "type": {"type": "string", "enum": ["OFFER", "ACCEPT", "REJECT"]},
    "base_state_version": {"type": "integer"},
    "instruction_version": {"type": "integer"},
    "proposal_id": {"type": ["string", "null"]},
    "plan_hash": {"type": ["string", "null"]},
    "constraints": {"type": "array", "items": CONSTRAINT_SCHEMA},
    "reasons": {"type": "array", "items": REASON_SCHEMA},
})


def validate_schema(value: Any, schema: Json) -> None:
    """Validate the intentionally small JSON Schema subset used above."""
    kinds = schema["type"]
    kinds = kinds if isinstance(kinds, list) else [kinds]
    types = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
    if not any(type(value) is types[k] for k in kinds):
        raise ProtocolError("INVALID_MESSAGE", "Incorrect field type")
    if "enum" in schema and value not in schema["enum"]:
        raise ProtocolError("INVALID_MESSAGE", "Invalid enum")
    if isinstance(value, dict):
        if set(value) != set(schema["properties"]):
            raise ProtocolError("INVALID_MESSAGE", "Missing or unauthorized fields")
        for key, item in value.items():
            validate_schema(item, schema["properties"][key])
    if isinstance(value, list):
        if len(value) > 1000:
            raise ProtocolError("INVALID_MESSAGE", "Oversized list")
        for item in value:
            validate_schema(item, schema["items"])
    if isinstance(value, str) and len(value) > 2000:
        raise ProtocolError("INVALID_MESSAGE", "Oversized string")


def validate_reply(reply: Json, request: Json, role: str, flight_id: str | None) -> Json:
    validate_schema(reply, REPLY_SCHEMA)
    for field in ("base_state_version", "instruction_version", "proposal_id", "plan_hash"):
        if reply[field] != request[field]:
            raise ProtocolError("BALLOT_IDENTITY_MISMATCH" if request["round"] == 2 else "STALE_MESSAGE")
    if request["round"] == 1:
        if reply["type"] != "OFFER" or not reply["constraints"]:
            raise ProtocolError("INVALID_OFFER")
    elif reply["type"] not in ("ACCEPT", "REJECT") or reply["constraints"]:
        raise ProtocolError("INVALID_BALLOT", "Ballots cannot amend a plan")
    if reply["type"] == "REJECT" and not reply["reasons"]:
        raise ProtocolError("INVALID_BALLOT", "A veto requires structured reasons")
    allowed = {"ATC": {"RUNWAY_WINDOW"}, "PILOT": {"TIMING_WINDOW", "LATEST_SAFE_ARRIVAL"},
               "GROUND": {"GROUND_WINDOW"}}[role]
    # Authorized windows come from the engine. Agents may narrow but never widen them.
    windows = request["view"]["authorized_windows"]
    for c in reply["constraints"]:
        if c["kind"] not in allowed or (flight_id and c["subject_id"] != flight_id):
            raise ProtocolError("UNAUTHORIZED_CONSTRAINT")
        matching = [w for w in windows if all(w[k] == c[k] for k in
                    ("kind", "subject_id", "resource_id", "unit", "source_type"))]
        end = utc(c["ends_at_utc"])
        start = utc(c["starts_at_utc"]) if c["starts_at_utc"] else None
        if start and start > end:
            raise ProtocolError("INVALID_WINDOW")
        if not any(end <= utc(w["ends_at_utc"]) and
                   ((w["starts_at_utc"] is None and start is None) or
                    (w["starts_at_utc"] is not None and start is not None and start >= utc(w["starts_at_utc"])))
                   for w in matching):
            raise ProtocolError("UNAUTHORIZED_CONSTRAINT", "Offer exceeds engine limits")
    if request["round"] == 1 and role == "PILOT":
        # No pilot may omit a known mandatory fuel/latest-arrival bound.
        required = [w for w in windows if w["kind"] == "LATEST_SAFE_ARRIVAL"]
        for bound in required:
            if not any(c["kind"] == bound["kind"] and c["subject_id"] == bound["subject_id"]
                       for c in reply["constraints"]):
                raise ProtocolError("MISSING_FUEL_LIMIT")
    return clone(reply)


def hard_checks_pass(checks: list[Json]) -> bool:
    """Empty/malformed/UNKNOWN hard reports are never interpreted as success."""
    if not isinstance(checks, list) or not checks:
        return False
    hard = []
    for check in checks:
        if not isinstance(check, dict) or check.get("severity") not in ("HARD", "SOFT") or check.get("status") not in ("PASS", "FAIL", "UNKNOWN"):
            return False
        if check["severity"] == "HARD":
            hard.append(check)
    return bool(hard) and all(c["status"] == "PASS" for c in hard)


def offers_satisfied(plan: Json, constraints: list[Json]) -> bool:
    """Enforce accepted windows independently of engine candidate generation.

    Runway/pilot windows constrain runway_time_utc. Ground windows contain the
    entire named reservation. Per-segment occupancy remains an engine check.
    """
    assignments = {a["flight_id"]: a for a in plan["assignments"]}
    for constraint in constraints:
        assignment = assignments.get(constraint["subject_id"])
        if assignment is None:
            return False
        kind, resource = constraint["kind"], constraint["resource_id"]
        if kind != "GROUND_WINDOW":
            if kind == "RUNWAY_WINDOW" and assignment["runway_id"] != resource:
                return False
            intervals = [(assignment["runway_time_utc"], assignment["runway_time_utc"])]
        elif resource == assignment.get("gate_id"):
            intervals = [(assignment["gate_start_utc"], assignment["gate_end_utc"])]
        elif resource in assignment["taxi_path_ids"]:
            intervals = [(assignment["taxi_start_utc"], assignment["taxi_end_utc"])]
        else:
            reservations = assignment["crew_reservations"] + assignment["equipment_reservations"]
            intervals = [(r["starts_at_utc"], r["ends_at_utc"]) for r in reservations if r["resource_id"] == resource]
        if not intervals:
            return False
        for start, end in intervals:
            if start is None or end is None:
                return False
            if utc(start) > utc(end) or utc(end) > utc(constraint["ends_at_utc"]):
                return False
            if constraint["starts_at_utc"] and utc(start) < utc(constraint["starts_at_utc"]):
                return False
    return True


def objective_score(impact: Json, objective: Json) -> float:
    weights = objective.get("weights", {})
    if not weights:
        raise ProtocolError("MISSING_OBJECTIVE")
    score = 0.0
    for name, weight in weights.items():
        value = impact.get(name)
        if type(value) not in (int, float) or type(weight) not in (int, float) or not math.isfinite(value) or not math.isfinite(weight) or weight < 0:
            raise ProtocolError("INVALID_OBJECTIVE")
        score += weight * value
    if not math.isfinite(score):
        raise ProtocolError("INVALID_OBJECTIVE")
    return score

