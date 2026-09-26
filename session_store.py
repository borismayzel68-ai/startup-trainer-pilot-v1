import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DEFAULT_RETENTION_DAYS = 30

def utc_now():
    return datetime.now(timezone.utc)

def iso_z(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class DurableSessionStore:
    def __init__(self, database_url=None, retention_days=DEFAULT_RETENTION_DAYS):
        self.database_url = database_url or "sqlite:///trainer_sessions.db"
        self.retention_days = int(retention_days)
        self.backend = "postgres" if self.database_url.startswith(("postgres://", "postgresql://")) else "sqlite"
        self._lock = threading.RLock()
        self._memory_connection = None
        if self.backend == "sqlite":
            self.sqlite_path = self._sqlite_path(self.database_url)
            if self.sqlite_path == ":memory:":
                self._memory_connection = sqlite3.connect(
                    ":memory:", check_same_thread=False
                )
        self.initialize()

    @staticmethod
    def _sqlite_path(database_url):
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ValueError("SESSION_DATABASE_URL must use sqlite:/// or postgresql://")
        path = database_url[len(prefix):]
        if not path:
            raise ValueError("SQLite path is required")
        return path

    def _connect(self):
        if self.backend == "sqlite":
            if self._memory_connection is not None:
                return self._memory_connection
            parent = os.path.dirname(os.path.abspath(self.sqlite_path))
            os.makedirs(parent, exist_ok=True)
            return sqlite3.connect(self.sqlite_path)
        import psycopg
        return psycopg.connect(self.database_url)

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            if conn is not self._memory_connection:
                conn.close()

    def initialize(self):
        sql = """
        CREATE TABLE IF NOT EXISTS trainer_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            event_status TEXT NOT NULL,
            data_json TEXT NOT NULL
        )
        """
        index_sql = """
        CREATE INDEX IF NOT EXISTS trainer_sessions_expires_idx
        ON trainer_sessions (expires_at)
        """
        with self._lock:
            with self._connection() as conn:
                conn.execute(sql)
                conn.execute(index_sql)
                conn.commit()

    def _expiry(self, session):
        value = str(session.get("expires_at") or "").strip()
        if value:
            return value
        created = str(session.get("created_at") or "").strip()
        try:
            base = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            base = utc_now()
        return iso_z(base + timedelta(days=self.retention_days))

    def save(self, session):
        data = dict(session)
        data["expires_at"] = self._expiry(data)
        data.setdefault("event_status", "not_emitted")
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        values = (
            str(data["id"]),
            str(data.get("project_id") or ""),
            str(data.get("created_at") or iso_z(utc_now())),
            str(data.get("updated_at") or iso_z(utc_now())),
            str(data["expires_at"]),
            str(data["event_status"]),
            payload,
        )
        if self.backend == "sqlite":
            sql = """
            INSERT INTO trainer_sessions
            (id, project_id, created_at, updated_at, expires_at, event_status, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project_id=excluded.project_id,
                updated_at=excluded.updated_at,
                expires_at=excluded.expires_at,
                event_status=excluded.event_status,
                data_json=excluded.data_json
            WHERE trainer_sessions.event_status='not_emitted'
               OR excluded.event_status<>'not_emitted'
            """
        else:
            sql = """
            INSERT INTO trainer_sessions
            (id, project_id, created_at, updated_at, expires_at, event_status, data_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                project_id=EXCLUDED.project_id,
                updated_at=EXCLUDED.updated_at,
                expires_at=EXCLUDED.expires_at,
                event_status=EXCLUDED.event_status,
                data_json=EXCLUDED.data_json
            WHERE trainer_sessions.event_status='not_emitted'
               OR EXCLUDED.event_status<>'not_emitted'
            """
        with self._lock:
            with self._connection() as conn:
                conn.execute(sql, values)
                conn.commit()
        session["expires_at"] = data["expires_at"]
        session["event_status"] = data["event_status"]

    def claim_completion_event(self, session, target_status="emitting"):
        """Atomically move a session out of not_emitted and persist its state."""
        if target_status not in {"emitting", "not_configured"}:
            raise ValueError("invalid completion event claim status")
        data = dict(session)
        data["expires_at"] = self._expiry(data)
        data["event_status"] = target_status
        payload = json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        )
        values = (
            str(data.get("project_id") or ""),
            str(data.get("updated_at") or iso_z(utc_now())),
            str(data["expires_at"]),
            target_status,
            payload,
            str(data["id"]),
            "not_emitted",
        )
        placeholder = "?" if self.backend == "sqlite" else "%s"
        sql = f"""
        UPDATE trainer_sessions SET
            project_id={placeholder},
            updated_at={placeholder},
            expires_at={placeholder},
            event_status={placeholder},
            data_json={placeholder}
        WHERE id={placeholder} AND event_status={placeholder}
        """
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(sql, values)
                conn.commit()
                claimed = cursor.rowcount == 1
        if claimed:
            session["expires_at"] = data["expires_at"]
            session["event_status"] = target_status
        return claimed

    def get(self, session_id, default=None):
        placeholder = "?" if self.backend == "sqlite" else "%s"
        sql = f"SELECT data_json, expires_at FROM trainer_sessions WHERE id={placeholder}"
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(sql, (str(session_id),)).fetchone()
        if not row:
            return default
        if str(row[1]) <= iso_z(utc_now()):
            self.delete(session_id)
            return default
        return json.loads(row[0])

    def delete(self, session_id):
        placeholder = "?" if self.backend == "sqlite" else "%s"
        sql = f"DELETE FROM trainer_sessions WHERE id={placeholder}"
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(sql, (str(session_id),))
                conn.commit()
                return cursor.rowcount > 0

    def purge_expired(self):
        placeholder = "?" if self.backend == "sqlite" else "%s"
        sql = f"DELETE FROM trainer_sessions WHERE expires_at <= {placeholder}"
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(sql, (iso_z(utc_now()),))
                conn.commit()
                return cursor.rowcount

    def values(self):
        self.purge_expired()
        with self._lock:
            with self._connection() as conn:
                rows = conn.execute(
                    "SELECT data_json FROM trainer_sessions ORDER BY created_at DESC"
                ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def clear(self):
        with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM trainer_sessions")
                conn.commit()

    def __setitem__(self, session_id, session):
        if str(session_id) != str(session.get("id")):
            raise ValueError("session key must match session id")
        self.save(session)

    def pop(self, session_id, default=None):
        value = self.get(session_id, default)
        if value is not default:
            self.delete(session_id)
        return value
