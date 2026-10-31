"""Supabase Postgres adapter using a server-only connection and transaction function."""
import psycopg
from psycopg.types.json import Jsonb

from backend.api.errors import PlatformError
from backend.contracts import AuditEvent, SessionDocument


class PostgresStore:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def _query(self, sql, params=()):
        try:
            with psycopg.connect(self.database_url, connect_timeout=5) as conn:
                return conn.execute(sql, params).fetchall()
        except psycopg.Error as exc:
            if exc.sqlstate == "42501":
                raise PlatformError(403, "FORBIDDEN", "Controller membership required") from exc
            if exc.sqlstate == "40001":
                raise PlatformError(409, "REVISION_CONFLICT", "Concurrent update or changed command identity") from exc
            raise PlatformError(503, "PERSISTENCE_UNAVAILABLE", "Postgres transaction failed") from exc

    def role(self, project_id, actor_id):
        rows = self._query("SELECT role FROM public.project_members WHERE project_id=%s::uuid AND user_id=%s::uuid",
                           (project_id, actor_id))
        return rows[0][0] if rows else None

    def get(self, session_id):
        rows = self._query("SELECT document FROM public.simulation_sessions WHERE session_id=%s", (session_id,))
        if not rows:
            raise PlatformError(404, "SESSION_NOT_FOUND", "Session does not exist")
        return SessionDocument.model_validate(rows[0][0])

    def all_documents(self):
        return [SessionDocument.model_validate(r[0]) for r in self._query(
            "SELECT document FROM public.simulation_sessions")]

    def _cached(self, table, key, value, command_id, fingerprint):
        rows = self._query(f"SELECT fingerprint,response FROM public.{table} WHERE {key}=%s AND command_id=%s",
                           (value, command_id))
        if rows and rows[0][0] != fingerprint:
            raise PlatformError(409, "COMMAND_REUSED", "Command ID used with different content")
        return rows[0][1] if rows else None

    def command(self, session_id, command_id, fingerprint):
        return self._cached("session_commands", "session_id", session_id, command_id, fingerprint)

    def creation(self, actor_id, command_id, fingerprint):
        return self._cached("session_creations", "actor_id", actor_id, command_id, fingerprint)

    def commit(self, document, expected_revision, events, actor_id, command_id, fingerprint, response,
               *, creation=False, internal=False):
        # Function execution is revoked from browser roles. It also checks actor membership.
        return self._query("SELECT public.commit_simulation_session(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                           (Jsonb(document.model_dump(mode="json")), expected_revision,
                            Jsonb([e.model_dump(mode="json") for e in events]), actor_id, command_id,
                            fingerprint, Jsonb(response), creation, internal))[0][0]

    def events(self, session_id, after_seq=0):
        return [AuditEvent.model_validate(r[0]) for r in self._query(
            "SELECT document FROM public.audit_events WHERE session_id=%s AND event_seq>%s ORDER BY event_seq",
            (session_id, after_seq))]

    def close(self):
        pass
