import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DEFAULT_RETENTION_DAYS = 30
POSTGRES_SCHEMA_LOCK_ID = 8675309007

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
        receipt_sql = """
        CREATE TABLE IF NOT EXISTS completion_receipts (
            idempotency_key TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            received_at TEXT NOT NULL
        )
        """
        outbox_sql = """
        CREATE TABLE IF NOT EXISTS completion_outbox (
            idempotency_key TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at TEXT NOT NULL,
            claimed_at TEXT,
            delivered_at TEXT,
            last_error TEXT,
            http_status INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
        index_sql = """
        CREATE INDEX IF NOT EXISTS trainer_sessions_expires_idx
        ON trainer_sessions (expires_at)
        """
        outbox_index_sql = """
        CREATE INDEX IF NOT EXISTS completion_outbox_ready_idx
        ON completion_outbox (status, available_at)
        """
        with self._lock:
            with self._connection() as conn:
                if self.backend == "postgres":
                    conn.execute(
                        "SELECT pg_advisory_xact_lock(%s)",
                        (POSTGRES_SCHEMA_LOCK_ID,),
                    )
                conn.execute(sql)
                conn.execute(receipt_sql)
                conn.execute(outbox_sql)
                conn.execute(index_sql)
                conn.execute(outbox_index_sql)
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
            WHERE trainer_sessions.event_status=excluded.event_status
               OR trainer_sessions.event_status='not_emitted'
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
            WHERE trainer_sessions.event_status=EXCLUDED.event_status
               OR trainer_sessions.event_status='not_emitted'
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

    def enqueue_completion_event(self, session, payload, queued_at=None):
        """Atomically persist completion state and one durable outbox row."""
        queued_at = str(queued_at or iso_z(utc_now()))
        data = dict(session)
        data["expires_at"] = self._expiry(data)
        data["event_status"] = "queued"
        data["event_attempts"] = int(data.get("event_attempts") or 0)
        data["event_queued_at"] = queued_at
        data["updated_at"] = queued_at
        session_json = json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        )
        payload_json = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
        placeholder = "?" if self.backend == "sqlite" else "%s"
        update_sql = f"""
        UPDATE trainer_sessions SET
            project_id={placeholder},
            updated_at={placeholder},
            expires_at={placeholder},
            event_status={placeholder},
            data_json={placeholder}
        WHERE id={placeholder} AND event_status={placeholder}
        """
        insert_placeholders = (
            "?, ?, ?, ?, ?, ?, ?, ?, ?"
            if self.backend == "sqlite"
            else "%s, %s, %s, %s, %s, %s, %s, %s, %s"
        )
        insert_sql = f"""
        INSERT INTO completion_outbox
        (idempotency_key, event_id, session_id, payload_json, status,
         attempts, available_at, created_at, updated_at)
        VALUES ({insert_placeholders})
        ON CONFLICT(idempotency_key) DO NOTHING
        """
        update_values = (
            str(data.get("project_id") or ""),
            queued_at,
            str(data["expires_at"]),
            "queued",
            session_json,
            str(data["id"]),
            "not_emitted",
        )
        insert_values = (
            str(payload["idempotency_key"]),
            str(payload["event_id"]),
            str(payload["session_id"]),
            payload_json,
            "pending",
            0,
            queued_at,
            queued_at,
            queued_at,
        )
        with self._lock:
            with self._connection() as conn:
                try:
                    if self.backend == "sqlite":
                        conn.execute("BEGIN IMMEDIATE")
                    updated = conn.execute(update_sql, update_values)
                    if updated.rowcount != 1:
                        conn.rollback()
                        return False
                    inserted = conn.execute(insert_sql, insert_values)
                    if inserted.rowcount != 1:
                        conn.rollback()
                        return False
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        session.update({
            "expires_at": data["expires_at"],
            "event_status": "queued",
            "event_attempts": data["event_attempts"],
            "event_queued_at": queued_at,
            "updated_at": queued_at,
        })
        return True

    @staticmethod
    def _outbox_row(row):
        if not row:
            return None
        return {
            "idempotency_key": row[0],
            "event_id": row[1],
            "session_id": row[2],
            "payload": json.loads(row[3]),
            "status": row[4],
            "attempts": int(row[5]),
            "available_at": row[6],
            "claimed_at": row[7],
            "delivered_at": row[8],
            "last_error": row[9],
            "http_status": row[10],
            "created_at": row[11],
            "updated_at": row[12],
        }

    def claim_next_completion_delivery(
        self, claimed_at=None, stale_before=None
    ):
        """Claim one ready or stale outbox row across processes."""
        claimed_at = str(claimed_at or iso_z(utc_now()))
        stale_before = str(stale_before or claimed_at)
        if self.backend == "sqlite":
            select_sql = """
            SELECT idempotency_key
            FROM completion_outbox
            WHERE (
                status IN ('pending', 'retry') AND available_at <= ?
            ) OR (
                status='delivering' AND claimed_at <= ?
            )
            ORDER BY created_at, idempotency_key
            LIMIT 1
            """
            update_sql = """
            UPDATE completion_outbox SET
                status='delivering',
                attempts=attempts+1,
                claimed_at=?,
                updated_at=?,
                last_error=NULL
            WHERE idempotency_key=? AND (
                (status IN ('pending', 'retry') AND available_at <= ?)
                OR (status='delivering' AND claimed_at <= ?)
            )
            """
        else:
            select_sql = """
            SELECT idempotency_key
            FROM completion_outbox
            WHERE (
                status IN ('pending', 'retry') AND available_at <= %s
            ) OR (
                status='delivering' AND claimed_at <= %s
            )
            ORDER BY created_at, idempotency_key
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
            update_sql = """
            UPDATE completion_outbox SET
                status='delivering',
                attempts=attempts+1,
                claimed_at=%s,
                updated_at=%s,
                last_error=NULL
            WHERE idempotency_key=%s AND (
                (status IN ('pending', 'retry') AND available_at <= %s)
                OR (status='delivering' AND claimed_at <= %s)
            )
            """
        placeholder = "?" if self.backend == "sqlite" else "%s"
        fetch_sql = f"""
        SELECT idempotency_key, event_id, session_id, payload_json,
               status, attempts, available_at, claimed_at, delivered_at,
               last_error, http_status, created_at, updated_at
        FROM completion_outbox WHERE idempotency_key={placeholder}
        """
        with self._lock:
            with self._connection() as conn:
                try:
                    if self.backend == "sqlite":
                        conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        select_sql, (claimed_at, stale_before)
                    ).fetchone()
                    if not row:
                        conn.commit()
                        return None
                    updated = conn.execute(
                        update_sql,
                        (
                            claimed_at,
                            claimed_at,
                            str(row[0]),
                            claimed_at,
                            stale_before,
                        ),
                    )
                    if updated.rowcount != 1:
                        conn.rollback()
                        return None
                    claimed = conn.execute(
                        fetch_sql, (str(row[0]),)
                    ).fetchone()
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        return self._outbox_row(claimed)

    def _update_session_delivery_state(
        self, conn, session_id, event_status, attempts, changed_at,
        error=None, http_status=None
    ):
        placeholder = "?" if self.backend == "sqlite" else "%s"
        row = conn.execute(
            f"SELECT data_json FROM trainer_sessions WHERE id={placeholder}",
            (str(session_id),),
        ).fetchone()
        if not row:
            return
        data = json.loads(row[0])
        data["event_status"] = event_status
        data["event_attempts"] = int(attempts)
        data["updated_at"] = changed_at
        if event_status == "sent":
            data["event_sent_at"] = changed_at
            data["event_http_status"] = int(http_status)
            data.pop("event_error", None)
            data.pop("event_failed_at", None)
        else:
            data["event_error"] = str(error or "")[:500]
            data["event_failed_at"] = changed_at
        payload = json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        )
        conn.execute(
            f"""
            UPDATE trainer_sessions
            SET event_status={placeholder}, updated_at={placeholder},
                data_json={placeholder}
            WHERE id={placeholder}
            """,
            (event_status, changed_at, payload, str(session_id)),
        )

    def mark_completion_delivery_succeeded(
        self, idempotency_key, http_status, delivered_at=None
    ):
        delivered_at = str(delivered_at or iso_z(utc_now()))
        placeholder = "?" if self.backend == "sqlite" else "%s"
        select_sql = f"""
        SELECT session_id, attempts FROM completion_outbox
        WHERE idempotency_key={placeholder} AND status='delivering'
        """
        update_sql = f"""
        UPDATE completion_outbox SET
            status='delivered', delivered_at={placeholder},
            http_status={placeholder}, updated_at={placeholder},
            last_error=NULL
        WHERE idempotency_key={placeholder} AND status='delivering'
        """
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    select_sql, (str(idempotency_key),)
                ).fetchone()
                if not row:
                    return False
                cursor = conn.execute(
                    update_sql,
                    (
                        delivered_at,
                        int(http_status),
                        delivered_at,
                        str(idempotency_key),
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                self._update_session_delivery_state(
                    conn, row[0], "sent", row[1], delivered_at,
                    http_status=http_status,
                )
                conn.commit()
        return True

    def mark_completion_delivery_failed(
        self, idempotency_key, error, available_at, failed_at=None,
        terminal=False
    ):
        failed_at = str(failed_at or iso_z(utc_now()))
        status = "dead" if terminal else "retry"
        session_status = "dead" if terminal else "retrying"
        placeholder = "?" if self.backend == "sqlite" else "%s"
        select_sql = f"""
        SELECT session_id, attempts FROM completion_outbox
        WHERE idempotency_key={placeholder} AND status='delivering'
        """
        update_sql = f"""
        UPDATE completion_outbox SET
            status={placeholder}, available_at={placeholder},
            claimed_at=NULL, last_error={placeholder},
            updated_at={placeholder}
        WHERE idempotency_key={placeholder} AND status='delivering'
        """
        safe_error = str(error or "")[:500]
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    select_sql, (str(idempotency_key),)
                ).fetchone()
                if not row:
                    return False
                cursor = conn.execute(
                    update_sql,
                    (
                        status,
                        str(available_at),
                        safe_error,
                        failed_at,
                        str(idempotency_key),
                    ),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return False
                self._update_session_delivery_state(
                    conn, row[0], session_status, row[1], failed_at,
                    error=safe_error,
                )
                conn.commit()
        return True

    def get_completion_outbox(self, idempotency_key):
        placeholder = "?" if self.backend == "sqlite" else "%s"
        sql = f"""
        SELECT idempotency_key, event_id, session_id, payload_json,
               status, attempts, available_at, claimed_at, delivered_at,
               last_error, http_status, created_at, updated_at
        FROM completion_outbox WHERE idempotency_key={placeholder}
        """
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    sql, (str(idempotency_key),)
                ).fetchone()
        return self._outbox_row(row)

    def count_completion_outbox(self, status=None):
        with self._lock:
            with self._connection() as conn:
                if status is None:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM completion_outbox"
                    ).fetchone()
                else:
                    placeholder = "?" if self.backend == "sqlite" else "%s"
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM completion_outbox "
                        f"WHERE status={placeholder}",
                        (str(status),),
                    ).fetchone()
        return int(row[0])

    def accept_completion_delivery(
        self, idempotency_key, event_id, session_id, received_at=None
    ):
        """Persist the first delivery for a key and reject later duplicates."""
        values = (
            str(idempotency_key),
            str(event_id),
            str(session_id),
            str(received_at or iso_z(utc_now())),
        )
        placeholders = "?, ?, ?, ?" if self.backend == "sqlite" else "%s, %s, %s, %s"
        sql = f"""
        INSERT INTO completion_receipts
        (idempotency_key, event_id, session_id, received_at)
        VALUES ({placeholders})
        ON CONFLICT(idempotency_key) DO NOTHING
        """
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(sql, values)
                conn.commit()
                return cursor.rowcount == 1

    def get_completion_receipt(self, idempotency_key):
        placeholder = "?" if self.backend == "sqlite" else "%s"
        sql = f"""
        SELECT idempotency_key, event_id, session_id, received_at
        FROM completion_receipts WHERE idempotency_key={placeholder}
        """
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(sql, (str(idempotency_key),)).fetchone()
        if not row:
            return None
        return {
            "idempotency_key": row[0],
            "event_id": row[1],
            "session_id": row[2],
            "received_at": row[3],
        }

    def count_completion_receipts(self):
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM completion_receipts"
                ).fetchone()
        return int(row[0])

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
                conn.execute("DELETE FROM completion_receipts")
                conn.execute("DELETE FROM completion_outbox")
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
