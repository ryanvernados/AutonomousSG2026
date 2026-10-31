"""Explicit offline adapter. Same atomic boundary as Supabase; not an RLS substitute."""
import json
import sqlite3
import threading

from backend.api.errors import PlatformError
from backend.contracts import AuditEvent, SessionDocument
from backend.persistence.base import check_transition


class SQLiteStore:
    def __init__(self, path: str):
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        self.before_commit = None  # Failure injection for transaction verification.
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS members(project_id TEXT, actor_id TEXT, role TEXT,
            PRIMARY KEY(project_id,actor_id));
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
            document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checkpoints(session_id TEXT, revision INTEGER, document TEXT NOT NULL,
            PRIMARY KEY(session_id,revision), FOREIGN KEY(session_id) REFERENCES sessions(id));
        CREATE TABLE IF NOT EXISTS events(session_id TEXT, seq INTEGER, event TEXT NOT NULL,
            PRIMARY KEY(session_id,seq), FOREIGN KEY(session_id) REFERENCES sessions(id));
        CREATE TABLE IF NOT EXISTS commands(session_id TEXT, command_id TEXT, fingerprint TEXT NOT NULL,
            response TEXT NOT NULL, PRIMARY KEY(session_id,command_id));
        CREATE TABLE IF NOT EXISTS creations(actor_id TEXT, command_id TEXT, fingerprint TEXT NOT NULL,
            response TEXT NOT NULL, PRIMARY KEY(actor_id,command_id));
        """)

    def add_member(self, project_id, actor_id, role="controller"):
        with self.lock:
            self.connection.execute("INSERT OR REPLACE INTO members VALUES(?,?,?)", (project_id, actor_id, role))

    def role(self, project_id, actor_id):
        with self.lock:
            row = self.connection.execute("SELECT role FROM members WHERE project_id=? AND actor_id=?",
                                          (project_id, actor_id)).fetchone()
            return row[0] if row else None

    def get(self, session_id):
        with self.lock:
            row = self.connection.execute("SELECT document FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                raise PlatformError(404, "SESSION_NOT_FOUND", "Session does not exist")
            return SessionDocument.model_validate_json(row[0])

    def all_documents(self):
        with self.lock:
            return [SessionDocument.model_validate_json(r[0])
                    for r in self.connection.execute("SELECT document FROM sessions")]

    def _cached(self, table, first_key, first_value, command_id, fingerprint):
        with self.lock:
            row = self.connection.execute(
                f"SELECT fingerprint,response FROM {table} WHERE {first_key}=? AND command_id=?",
                (first_value, command_id)).fetchone()
            if row and row[0] != fingerprint:
                raise PlatformError(409, "COMMAND_REUSED", "Command ID already used with different content")
            return json.loads(row[1]) if row else None

    def command(self, session_id, command_id, fingerprint):
        return self._cached("commands", "session_id", session_id, command_id, fingerprint)

    def creation(self, actor_id, command_id, fingerprint):
        return self._cached("creations", "actor_id", actor_id, command_id, fingerprint)

    def commit(self, document, expected_revision, events, actor_id, command_id, fingerprint, response,
               *, creation=False, internal=False):
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                role = self.role(document.project_id, actor_id)
                if not internal and role not in {"controller", "admin"}:
                    raise PlatformError(403, "FORBIDDEN", "Controller project membership required")
                cached = (self.creation(actor_id, command_id, fingerprint) if creation else
                          self.command(document.session_id, command_id, fingerprint))
                if cached is not None:
                    self.connection.execute("ROLLBACK")
                    return cached
                row = self.connection.execute("SELECT revision FROM sessions WHERE id=?",
                                              (document.session_id,)).fetchone()
                if expected_revision is None:
                    if row:
                        raise PlatformError(409, "REVISION_CONFLICT", "Session already exists")
                elif not row or row[0] != expected_revision:
                    raise PlatformError(409, "REVISION_CONFLICT", "Concurrent durable update; reload snapshot")
                else:
                    check_transition(self.get(document.session_id), document)
                revision = 0 if expected_revision is None else expected_revision + 1
                document = document.model_copy(update={"revision": revision})
                serialized = document.model_dump_json()
                self.connection.execute("INSERT INTO sessions VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET "
                                        "revision=excluded.revision,document=excluded.document",
                                        (document.session_id, revision, serialized))
                self.connection.execute("INSERT INTO checkpoints VALUES(?,?,?)",
                                        (document.session_id, revision, serialized))
                seq = self.connection.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE session_id=?",
                                              (document.session_id,)).fetchone()[0]
                for event in events:
                    seq += 1
                    event = event.model_copy(update={"event_seq": seq})
                    self.connection.execute("INSERT INTO events VALUES(?,?,?)",
                                            (document.session_id, seq, event.model_dump_json()))
                self.connection.execute("INSERT INTO commands VALUES(?,?,?,?)",
                                        (document.session_id, command_id, fingerprint, json.dumps(response)))
                if creation:
                    self.connection.execute("INSERT INTO creations VALUES(?,?,?,?)",
                                            (actor_id, command_id, fingerprint, json.dumps(response)))
                if self.before_commit:
                    self.before_commit()
                self.connection.execute("COMMIT")
                return response
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise

    def events(self, session_id, after_seq=0):
        with self.lock:
            return [AuditEvent.model_validate_json(r[0]) for r in self.connection.execute(
                "SELECT event FROM events WHERE session_id=? AND seq>? ORDER BY seq", (session_id, after_seq))]

    def close(self):
        self.connection.close()
