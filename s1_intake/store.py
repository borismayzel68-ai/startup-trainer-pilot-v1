"""Хранилище заявок S1: та же БД, что у тренажёра (SESSION_DATABASE_URL / DATABASE_URL),
отдельная таблица s1_readiness_jobs. SQLite — локально и в тестах, PostgreSQL — на Render.

Файловую систему Render не используем: она эфемерна. Readiness Pack хранится в БД
(JSON + HTML), PDF рендерится из сохранённого HTML при скачивании.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

STATUSES = ("received", "processing", "ready_for_review", "sent", "failed", "rejected")
_COLUMNS = ("status", "project_name", "founder_email", "verdict", "readiness", "error", "data_json")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ReadinessJobStore:
    def __init__(self, database_url: str, retention_days: int = 90):
        self.database_url = database_url
        self.retention_days = int(retention_days)
        self.backend = "postgres" if database_url.startswith(("postgres://", "postgresql://")) else "sqlite"
        self.ph = "%s" if self.backend == "postgres" else "?"
        self._lock = threading.RLock()
        self._memory = None
        if self.backend == "sqlite":
            prefix = "sqlite:///"
            if not database_url.startswith(prefix) or not database_url[len(prefix):]:
                raise ValueError("database url must be sqlite:///<path> or postgresql://")
            self.sqlite_path = database_url[len(prefix):]
            if self.sqlite_path == ":memory:":
                self._memory = sqlite3.connect(":memory:", check_same_thread=False)
        self._init()

    # ------------------------------------------------------------ plumbing --
    @contextmanager
    def _conn(self):
        if self.backend == "sqlite":
            conn = self._memory or sqlite3.connect(self.sqlite_path, timeout=10)
        else:
            import psycopg
            conn = psycopg.connect(self.database_url)
        try:
            yield conn
        finally:
            if conn is not self._memory:
                conn.close()

    def _exec(self, sql: str, params: tuple = (), fetch: str | None = None):
        with self._lock, self._conn() as conn:
            cur = conn.execute(sql, params)
            out = cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else cur.rowcount
            conn.commit()
            return out

    def _init(self) -> None:
        self._exec("""
            CREATE TABLE IF NOT EXISTS s1_readiness_jobs (
                id TEXT PRIMARY KEY,
                submission_id TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                project_name TEXT, founder_email TEXT, verdict TEXT, readiness INTEGER, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                data_json TEXT NOT NULL
            )""")
        self._exec("CREATE INDEX IF NOT EXISTS s1_readiness_jobs_status_idx ON s1_readiness_jobs (status)")

    @staticmethod
    def _row(r) -> dict | None:
        if not r:
            return None
        keys = ("id", "submission_id", "status", "project_name", "founder_email", "verdict",
                "readiness", "error", "created_at", "updated_at", "expires_at", "data_json")
        d = dict(zip(keys, r))
        d["data"] = json.loads(d.pop("data_json") or "{}")
        return d

    _SELECT = ("SELECT id, submission_id, status, project_name, founder_email, verdict, readiness, error, "
               "created_at, updated_at, expires_at, data_json FROM s1_readiness_jobs")

    # ----------------------------------------------------------------- API --
    def create_or_get(self, submission_id: str, project_name: str, founder_email: str, data: dict) -> tuple[str, bool]:
        """(job_id, created). Повторная доставка того же tranid не создаёт дубль (UNIQUE + ON CONFLICT)."""
        job_id = uuid.uuid4().hex[:16]
        now = _now()
        expires = (datetime.now(timezone.utc) + timedelta(days=self.retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        p = self.ph
        inserted = self._exec(
            f"INSERT INTO s1_readiness_jobs (id, submission_id, status, project_name, founder_email, "
            f"created_at, updated_at, expires_at, data_json) VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p}) "
            f"ON CONFLICT (submission_id) DO NOTHING",
            (job_id, submission_id, "received", project_name, founder_email, now, now, expires,
             json.dumps(data, ensure_ascii=False, default=str)),
        )
        if inserted == 1:
            return job_id, True
        row = self._exec(f"SELECT id FROM s1_readiness_jobs WHERE submission_id={p}", (submission_id,), "one")
        return row[0], False

    def claim(self, job_id: str, stale_before: str | None = None) -> bool:
        """Атомарно забрать заявку в обработку (status received → processing).
        stale_before: также забрать «зависшую» processing-заявку, не обновлявшуюся с этого момента
        (воркер умер посреди обработки). Несколько gunicorn-воркеров не обработают её дважды."""
        p = self.ph
        sql = (f"UPDATE s1_readiness_jobs SET status={p}, updated_at={p}, error=NULL "
               f"WHERE id={p} AND (status='received'")
        params: list = ["processing", _now(), job_id]
        if stale_before:
            sql += f" OR (status='processing' AND updated_at < {p})"
            params.append(stale_before)
        return self._exec(sql + ")", tuple(params)) == 1

    def reset_for_rerun(self, job_id: str) -> bool:
        return self._exec(
            f"UPDATE s1_readiness_jobs SET status='received', updated_at={self.ph} "
            f"WHERE id={self.ph} AND status IN ('ready_for_review','failed','sent')", (_now(), job_id)) == 1

    def update(self, job_id: str, data_patch: dict | None = None, **fields) -> None:
        bad = set(fields) - set(_COLUMNS)
        if bad:
            raise ValueError(f"unknown columns {bad}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(fields["status"])
        if data_patch:
            job = self.get(job_id) or {"data": {}}
            data = {**job["data"], **data_patch}
            fields["data_json"] = json.dumps(data, ensure_ascii=False, default=str)
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}={self.ph}" for k in fields)
        self._exec(f"UPDATE s1_readiness_jobs SET {cols} WHERE id={self.ph}", (*fields.values(), job_id))

    def get(self, job_id: str) -> dict | None:
        return self._row(self._exec(f"{self._SELECT} WHERE id={self.ph}", (job_id,), "one"))

    def list(self, limit: int = 50) -> list[dict]:
        self.purge_expired()
        rows = self._exec(f"{self._SELECT} ORDER BY created_at DESC LIMIT {int(limit)}", (), "all")
        out = []
        for r in rows:
            d = self._row(r)
            d.pop("data")  # список — только метаданные
            out.append(d)
        return out

    def recoverable(self, stale_before: str) -> list[str]:
        """received — не начаты; processing старше stale_before — воркер умер."""
        rows = self._exec(
            f"SELECT id FROM s1_readiness_jobs WHERE status='received' "
            f"OR (status='processing' AND updated_at < {self.ph})", (stale_before,), "all")
        return [r[0] for r in rows]

    def purge_expired(self) -> int:
        return self._exec(f"DELETE FROM s1_readiness_jobs WHERE expires_at <= {self.ph}", (_now(),))

    def clear(self) -> None:
        self._exec("DELETE FROM s1_readiness_jobs")
