"""HTTPS transport to Supabase Postgres. Writes use the same atomic transaction RPC.

This is a backend-only adapter. A publishable key is never sufficient for this store.
"""
import httpx

from backend.api.errors import PlatformError
from backend.contracts import AuditEvent, SessionDocument


class SupabaseHTTPStore:
    def __init__(self, project_url: str, secret_key: str, *, transport=None):
        if not secret_key:
            raise ValueError("A backend-only Supabase secret key is required")
        self.client = httpx.Client(base_url=project_url.rstrip("/") + "/rest/v1/",
                                   headers={"apikey": secret_key}, timeout=15, transport=transport)

    def _request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PlatformError(503, "PERSISTENCE_UNAVAILABLE", "Supabase HTTPS connection unavailable") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise PlatformError(503, "PERSISTENCE_UNAVAILABLE", "Invalid Supabase response") from exc
        if not response.is_success:
            code = body.get("code") if isinstance(body, dict) else None
            if code == "40001":
                raise PlatformError(409, "REVISION_CONFLICT", "Concurrent update or changed command identity")
            if code == "42501":
                raise PlatformError(403, "FORBIDDEN", "Project membership or backend permission is missing")
            raise PlatformError(503, "PERSISTENCE_UNAVAILABLE", "Supabase database request failed",
                                dependency_code=code)
        return body

    def _rows(self, table, **filters):
        rows = []
        offset = 0
        while True:
            page = self._request("GET", table, params={**filters, "offset": offset, "limit": 500})
            if not isinstance(page, list):
                raise PlatformError(503, "PERSISTENCE_UNAVAILABLE", "Invalid table response")
            rows.extend(page)
            if len(page) < 500:
                return rows
            offset += len(page)

    def role(self, project_id, actor_id):
        rows = self._rows("project_members", select="role", project_id="eq." + project_id,
                          user_id="eq." + actor_id)
        return rows[0]["role"] if rows else None

    def get(self, session_id):
        rows = self._rows("simulation_sessions", select="document", session_id="eq." + session_id)
        if not rows:
            raise PlatformError(404, "SESSION_NOT_FOUND", "Session does not exist")
        return SessionDocument.model_validate(rows[0]["document"])

    def all_documents(self):
        return [SessionDocument.model_validate(r["document"]) for r in self._rows(
            "simulation_sessions", select="document", order="session_id.asc")]

    def _cached(self, table, key, value, command_id, fingerprint):
        rows = self._rows(table, select="fingerprint,response", **{key: "eq." + value,
                                                                   "command_id": "eq." + command_id})
        if rows and rows[0]["fingerprint"] != fingerprint:
            raise PlatformError(409, "COMMAND_REUSED", "Command ID used with different content")
        return rows[0]["response"] if rows else None

    def command(self, session_id, command_id, fingerprint):
        return self._cached("session_commands", "session_id", session_id, command_id, fingerprint)

    def creation(self, actor_id, command_id, fingerprint):
        return self._cached("session_creations", "actor_id", actor_id, command_id, fingerprint)

    def commit(self, document, expected_revision, events, actor_id, command_id, fingerprint, response,
               *, creation=False, internal=False):
        # No independent reservation/approval writes: the RPC is one Postgres transaction.
        return self._request("POST", "rpc/commit_simulation_session", json={
            "p_document": document.model_dump(mode="json"), "p_expected_revision": expected_revision,
            "p_events": [e.model_dump(mode="json") for e in events], "p_actor": actor_id,
            "p_command": command_id, "p_fingerprint": fingerprint, "p_response": response,
            "p_creation": creation, "p_internal": internal})

    def events(self, session_id, after_seq=0):
        return [AuditEvent.model_validate(r["document"]) for r in self._rows(
            "audit_events", select="document", session_id="eq." + session_id,
            event_seq="gt." + str(after_seq), order="event_seq.asc")]

    def close(self):
        self.client.close()
