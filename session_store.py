import json
import os
import sqlite3
import threading
import uuid
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
            state TEXT NOT NULL,
            attempt_token TEXT,
            lease_until TEXT,
            accepted_at TEXT NOT NULL,
            completed_at TEXT,
            updated_at TEXT NOT NULL
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
            claim_token TEXT,
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
        receipt_index_sql = """
        CREATE INDEX IF NOT EXISTS completion_receipts_state_idx
        ON completion_receipts (state, lease_until)
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
                conn.execute(receipt_index_sql)
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
        placeholder = "?" if self.backend == "sqlite" else "%s"
        suffix = "" if self.backend == "sqlite" else " FOR UPDATE"
        select_sql = f"""
        SELECT event_status, data_json FROM trainer_sessions
        WHERE id={placeholder}{suffix}
        """
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
            """
        with self._lock:
            with self._connection() as conn:
                try:
                    if self.backend == "sqlite":
                        conn.execute("BEGIN IMMEDIATE")
                    current_row = conn.execute(
                        select_sql, (str(data["id"]),)
                    ).fetchone()
                    if current_row and current_row[0] != "not_emitted" and (
                        current_row[0] != data["event_status"]
                    ):
                        current = json.loads(current_row[1])
                        protected = {
                            key for key in set(data) | set(current)
                            if key.startswith("event_") or key in {
                                "completed_at", "idempotency_key"
                            }
                        }
                        for key in protected:
                            data.pop(key, None)
                            if key in current:
                                data[key] = current[key]
                        data["event_status"] = current_row[0]
                    payload = json.dumps(
                        data, ensure_ascii=False, separators=(",", ":")
                    )
                    values = (
                        str(data["id"]),
                        str(data.get("project_id") or ""),
                        str(data.get("created_at") or iso_z(utc_now())),
                        str(data.get("updated_at") or iso_z(utc_now())),
                        str(data["expires_at"]),
                        str(data["event_status"]),
                        payload,
                    )
                    conn.execute(sql, values)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        session.clear()
        session.update(data)

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
            "claim_token": row[7],
            "claimed_at": row[8],
            "delivered_at": row[9],
            "last_error": row[10],
            "http_status": row[11],
            "created_at": row[12],
            "updated_at": row[13],
        }

    def claim_next_completion_delivery(
        self, claimed_at=None, stale_before=None, max_attempts=8
    ):
        """Claim one ready or stale outbox row across processes."""
        claimed_at = str(claimed_at or iso_z(utc_now()))
        stale_before = str(stale_before or iso_z(
            utc_now() - timedelta(seconds=60)
        ))
        claim_token = str(uuid.uuid4())
        if self.backend == "sqlite":
            select_sql = """
            SELECT idempotency_key
            FROM completion_outbox
            WHERE attempts < ? AND ((
                status IN ('pending', 'retry') AND available_at <= ?
            ) OR (
                status='delivering' AND claimed_at <= ?
            ))
            ORDER BY created_at, idempotency_key
            LIMIT 1
            """
            update_sql = """
            UPDATE completion_outbox SET
                status='delivering',
                attempts=attempts+1,
                claim_token=?,
                claimed_at=?,
                updated_at=?
            WHERE idempotency_key=? AND attempts < ? AND ((
                (status IN ('pending', 'retry') AND available_at <= ?)
                OR (status='delivering' AND claimed_at <= ?)
            ))
            """
        else:
            select_sql = """
            SELECT idempotency_key
            FROM completion_outbox
            WHERE attempts < %s AND ((
                status IN ('pending', 'retry') AND available_at <= %s
            ) OR (
                status='delivering' AND claimed_at <= %s
            ))
            ORDER BY created_at, idempotency_key
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
            update_sql = """
            UPDATE completion_outbox SET
                status='delivering',
                attempts=attempts+1,
                claim_token=%s,
                claimed_at=%s,
                updated_at=%s
            WHERE idempotency_key=%s AND attempts < %s AND ((
                (status IN ('pending', 'retry') AND available_at <= %s)
                OR (status='delivering' AND claimed_at <= %s)
            ))
            """
        placeholder = "?" if self.backend == "sqlite" else "%s"
        fetch_sql = f"""
        SELECT idempotency_key, event_id, session_id, payload_json,
               status, attempts, available_at, claim_token, claimed_at,
               delivered_at, last_error, http_status, created_at, updated_at
        FROM completion_outbox WHERE idempotency_key={placeholder}
        """
        with self._lock:
            with self._connection() as conn:
                try:
                    if self.backend == "sqlite":
                        conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        select_sql, (
                            int(max_attempts), claimed_at, stale_before
                        )
                    ).fetchone()
                    if not row:
                        conn.commit()
                        return None
                    updated = conn.execute(
                        update_sql,
                        (
                            claim_token,
                            claimed_at,
                            claimed_at,
                            str(row[0]),
                            int(max_attempts),
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
        self, idempotency_key, claim_token, http_status, delivered_at=None
    ):
        delivered_at = str(delivered_at or iso_z(utc_now()))
        placeholder = "?" if self.backend == "sqlite" else "%s"
        select_sql = f"""
        SELECT session_id, attempts FROM completion_outbox
        WHERE idempotency_key={placeholder} AND status='delivering'
          AND claim_token={placeholder}
        """
        update_sql = f"""
        UPDATE completion_outbox SET
            status='delivered', delivered_at={placeholder},
            http_status={placeholder}, updated_at={placeholder},
            last_error=NULL, claim_token=NULL, payload_json='{{}}'
        WHERE idempotency_key={placeholder} AND status='delivering'
          AND claim_token={placeholder}
        """
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    select_sql, (str(idempotency_key), str(claim_token))
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
                        str(claim_token),
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
        self, idempotency_key, claim_token, error, available_at, failed_at=None,
        terminal=False
    ):
        failed_at = str(failed_at or iso_z(utc_now()))
        status = "dead" if terminal else "retry"
        session_status = "dead" if terminal else "retrying"
        placeholder = "?" if self.backend == "sqlite" else "%s"
        select_sql = f"""
        SELECT session_id, attempts, payload_json FROM completion_outbox
        WHERE idempotency_key={placeholder} AND status='delivering'
          AND claim_token={placeholder}
        """
        update_sql = f"""
        UPDATE completion_outbox SET
            status={placeholder}, available_at={placeholder},
            claim_token=NULL, claimed_at=NULL, last_error={placeholder},
            updated_at={placeholder}, payload_json={placeholder}
        WHERE idempotency_key={placeholder} AND status='delivering'
          AND claim_token={placeholder}
        """
        safe_error = str(error or "")[:500]
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    select_sql, (
                        str(idempotency_key), str(claim_token)
                    )
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
                        "{}" if terminal else row[2],
                        str(idempotency_key),
                        str(claim_token),
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

    def fail_exhausted_completion_deliveries(
        self, max_attempts, stale_before=None, failed_at=None
    ):
        """Move crashed claims that exhausted their budget to dead."""
        failed_at = str(failed_at or iso_z(utc_now()))
        stale_before = str(stale_before or iso_z(
            utc_now() - timedelta(seconds=60)
        ))
        placeholder = "?" if self.backend == "sqlite" else "%s"
        suffix = "" if self.backend == "sqlite" else (
            " FOR UPDATE SKIP LOCKED"
        )
        select_sql = f"""
        SELECT idempotency_key, session_id, attempts
        FROM completion_outbox
        WHERE status='delivering' AND attempts >= {placeholder}
          AND claimed_at <= {placeholder}{suffix}
        """
        update_sql = f"""
        UPDATE completion_outbox SET
            status='dead', claim_token=NULL, claimed_at=NULL,
            payload_json='{{}}', last_error={placeholder},
            updated_at={placeholder}
        WHERE idempotency_key={placeholder} AND status='delivering'
          AND attempts >= {placeholder}
        """
        error = "delivery attempts exhausted after worker interruption"
        changed = 0
        with self._lock:
            with self._connection() as conn:
                try:
                    if self.backend == "sqlite":
                        conn.execute("BEGIN IMMEDIATE")
                    rows = conn.execute(
                        select_sql, (int(max_attempts), stale_before)
                    ).fetchall()
                    for row in rows:
                        cursor = conn.execute(
                            update_sql,
                            (
                                error,
                                failed_at,
                                str(row[0]),
                                int(max_attempts),
                            ),
                        )
                        if cursor.rowcount != 1:
                            continue
                        changed += 1
                        self._update_session_delivery_state(
                            conn, row[1], "dead", row[2], failed_at,
                            error=error,
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        return changed

    def get_completion_outbox(self, idempotency_key):
        placeholder = "?" if self.backend == "sqlite" else "%s"
        sql = f"""
        SELECT idempotency_key, event_id, session_id, payload_json,
               status, attempts, available_at, claim_token, claimed_at,
               delivered_at, last_error, http_status, created_at, updated_at
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

    def begin_completion_delivery(
        self, idempotency_key, event_id, session_id, accepted_at=None,
        lease_until=None
    ):
        """Acquire a renewable processing lease for the Make route."""
        accepted_at = str(accepted_at or iso_z(utc_now()))
        lease_until = str(lease_until or iso_z(
            utc_now() + timedelta(minutes=5)
        ))
        attempt_token = str(uuid.uuid4())
        values = (
            str(idempotency_key), str(event_id), str(session_id),
            "processing", attempt_token, lease_until, accepted_at,
            None, accepted_at,
        )
        placeholders = (
            "?, ?, ?, ?, ?, ?, ?, ?, ?"
            if self.backend == "sqlite"
            else "%s, %s, %s, %s, %s, %s, %s, %s, %s"
        )
        insert_sql = f"""
        INSERT INTO completion_receipts
        (idempotency_key, event_id, session_id, state, attempt_token,
         lease_until, accepted_at, completed_at, updated_at)
        VALUES ({placeholders})
        ON CONFLICT(idempotency_key) DO NOTHING
        """
        placeholder = "?" if self.backend == "sqlite" else "%s"
        suffix = "" if self.backend == "sqlite" else " FOR UPDATE"
        session_sql = f"""
        SELECT id FROM trainer_sessions
        WHERE id={placeholder}{suffix}
        """
        select_sql = f"""
        SELECT event_id, session_id, state, attempt_token, lease_until,
               accepted_at, completed_at, updated_at
        FROM completion_receipts
        WHERE idempotency_key={placeholder}{suffix}
        """
        update_sql = f"""
        UPDATE completion_receipts SET
            state='processing', attempt_token={placeholder},
            lease_until={placeholder}, accepted_at={placeholder},
            completed_at=NULL, updated_at={placeholder}
        WHERE idempotency_key={placeholder}
        """
        with self._lock:
            with self._connection() as conn:
                try:
                    if self.backend == "sqlite":
                        conn.execute("BEGIN IMMEDIATE")
                    session_row = conn.execute(
                        session_sql, (str(session_id),)
                    ).fetchone()
                    if not session_row:
                        conn.commit()
                        return {"status": "session_deleted"}
                    inserted = conn.execute(insert_sql, values)
                    if inserted.rowcount == 1:
                        conn.commit()
                        return {
                            "status": "accepted",
                            "attempt_token": attempt_token,
                            "lease_until": lease_until,
                        }
                    row = conn.execute(
                        select_sql, (str(idempotency_key),)
                    ).fetchone()
                    if not row:
                        raise RuntimeError("completion receipt disappeared")
                    if row[0] != str(event_id) or row[1] != str(session_id):
                        conn.rollback()
                        return {"status": "identity_conflict"}
                    if row[2] == "completed":
                        conn.commit()
                        return {
                            "status": "duplicate",
                            "completed_at": row[6],
                        }
                    if row[4] and str(row[4]) > accepted_at:
                        conn.commit()
                        return {
                            "status": "in_progress",
                            "lease_until": row[4],
                        }
                    conn.execute(
                        update_sql,
                        (
                            attempt_token, lease_until, accepted_at,
                            accepted_at, str(idempotency_key),
                        ),
                    )
                    conn.commit()
                    return {
                        "status": "accepted",
                        "attempt_token": attempt_token,
                        "lease_until": lease_until,
                    }
                except Exception:
                    conn.rollback()
                    raise

    def complete_completion_delivery(
        self, idempotency_key, event_id, session_id, attempt_token,
        completed_at=None
    ):
        """Complete only the currently leased Make attempt."""
        completed_at = str(completed_at or iso_z(utc_now()))
        placeholder = "?" if self.backend == "sqlite" else "%s"
        suffix = "" if self.backend == "sqlite" else " FOR UPDATE"
        select_sql = f"""
        SELECT event_id, session_id, state, attempt_token, completed_at
        FROM completion_receipts
        WHERE idempotency_key={placeholder}{suffix}
        """
        update_sql = f"""
        UPDATE completion_receipts SET
            state='completed', attempt_token=NULL, lease_until=NULL,
            completed_at={placeholder}, updated_at={placeholder}
        WHERE idempotency_key={placeholder} AND state='processing'
          AND attempt_token={placeholder}
        """
        with self._lock:
            with self._connection() as conn:
                try:
                    if self.backend == "sqlite":
                        conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        select_sql, (str(idempotency_key),)
                    ).fetchone()
                    if not row:
                        conn.commit()
                        return {"status": "missing"}
                    if row[0] != str(event_id) or row[1] != str(session_id):
                        conn.rollback()
                        return {"status": "identity_conflict"}
                    if row[2] == "completed":
                        conn.commit()
                        return {
                            "status": "completed",
                            "completed_at": row[4],
                        }
                    if row[3] != str(attempt_token):
                        conn.rollback()
                        return {"status": "stale_attempt"}
                    cursor = conn.execute(
                        update_sql,
                        (
                            completed_at, completed_at,
                            str(idempotency_key), str(attempt_token),
                        ),
                    )
                    if cursor.rowcount != 1:
                        conn.rollback()
                        return {"status": "stale_attempt"}
                    conn.commit()
                    return {
                        "status": "completed",
                        "completed_at": completed_at,
                    }
                except Exception:
                    conn.rollback()
                    raise

    def get_completion_receipt(self, idempotency_key):
        placeholder = "?" if self.backend == "sqlite" else "%s"
        sql = f"""
        SELECT idempotency_key, event_id, session_id, state,
               attempt_token, lease_until, accepted_at, completed_at,
               updated_at
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
            "state": row[3],
            "attempt_token": row[4],
            "lease_until": row[5],
            "accepted_at": row[6],
            "completed_at": row[7],
            "updated_at": row[8],
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
        with self._lock:
            with self._connection() as conn:
                try:
                    if self.backend == "sqlite":
                        conn.execute("BEGIN IMMEDIATE")
                    values = (str(session_id),)
                    conn.execute(
                        f"DELETE FROM completion_outbox "
                        f"WHERE session_id={placeholder}", values
                    )
                    conn.execute(
                        f"DELETE FROM completion_receipts "
                        f"WHERE session_id={placeholder}", values
                    )
                    cursor = conn.execute(
                        f"DELETE FROM trainer_sessions "
                        f"WHERE id={placeholder}", values
                    )
                    conn.commit()
                    return cursor.rowcount > 0
                except Exception:
                    conn.rollback()
                    raise

    def purge_expired(self):
        placeholder = "?" if self.backend == "sqlite" else "%s"
        now_value = iso_z(utc_now())
        cutoff = iso_z(utc_now() - timedelta(days=self.retention_days))
        with self._lock:
            with self._connection() as conn:
                try:
                    if self.backend == "sqlite":
                        conn.execute("BEGIN IMMEDIATE")
                    expired_session_sql = (
                        f"SELECT id FROM trainer_sessions "
                        f"WHERE expires_at <= {placeholder}"
                    )
                    conn.execute(
                        f"DELETE FROM completion_outbox WHERE session_id IN "
                        f"({expired_session_sql})", (now_value,)
                    )
                    conn.execute(
                        f"DELETE FROM completion_receipts WHERE session_id IN "
                        f"({expired_session_sql})", (now_value,)
                    )
                    cursor = conn.execute(
                        f"DELETE FROM trainer_sessions "
                        f"WHERE expires_at <= {placeholder}", (now_value,)
                    )
                    conn.execute(
                        f"DELETE FROM completion_outbox "
                        f"WHERE status IN ('delivered','dead') "
                        f"AND updated_at <= {placeholder}", (cutoff,)
                    )
                    conn.execute(
                        f"DELETE FROM completion_receipts "
                        f"WHERE updated_at <= {placeholder}", (cutoff,)
                    )
                    conn.commit()
                    return cursor.rowcount
                except Exception:
                    conn.rollback()
                    raise

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
