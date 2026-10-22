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

STATUSES = ("received", "processing", "awaiting_founder", "out_of_scope", "manual_review", "ready_for_review", "sending", "sent", "failed", "rejected")
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
        # TG-1: журнал подписей (append-only): кто, когда, что (sha256 отчёта), чем кончилось.
        self._exec("""
            CREATE TABLE IF NOT EXISTS s1_approvals (
                job_id TEXT NOT NULL, approver TEXT NOT NULL, approved_at TEXT NOT NULL,
                report_sha256 TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT
            )""")
        # HQ-028: журнал согласий (кто, что, кому, когда, отзыв) и журнал передач (G-4)
        self._exec("""
            CREATE TABLE IF NOT EXISTS s1_consents (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, granted_by TEXT NOT NULL, scope TEXT NOT NULL,
                recipient TEXT NOT NULL, granted_at TEXT NOT NULL, revoked_at TEXT, revoked_by TEXT
            )""")
        self._exec("""
            CREATE TABLE IF NOT EXISTS s1_transfers (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, kind TEXT NOT NULL, recipient TEXT NOT NULL,
                status TEXT NOT NULL, requested_by TEXT, requested_at TEXT NOT NULL,
                g4_by TEXT, g4_at TEXT, detail TEXT
            )""")
        # HQ-028: внутренние задачи (очередь подписанта); наружу ничего не уходит
        self._exec("""
            CREATE TABLE IF NOT EXISTS s1_tasks (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, assignee TEXT NOT NULL, title TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
        self._exec("CREATE UNIQUE INDEX IF NOT EXISTS s1_tasks_once ON s1_tasks (job_id, title)")
        # HQ-035: журнал подписей факта (кто, когда, роль, слот, основание) — append-only
        self._exec("""
            CREATE TABLE IF NOT EXISTS s1_fact_signoffs (
                job_id TEXT NOT NULL, slot TEXT NOT NULL, signer TEXT NOT NULL, role TEXT NOT NULL,
                signed_at TEXT NOT NULL, basis TEXT NOT NULL
            )""")
        # один и тот же отчёт (job + sha256) может быть отправлен только один раз
        self._exec("CREATE UNIQUE INDEX IF NOT EXISTS s1_approvals_sent_once ON s1_approvals "
                   "(job_id, report_sha256) WHERE outcome='sent'")

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

    # ------------------------------------------------------------- TG-1 --
    def begin_send(self, job_id: str) -> bool:
        """Атомарно ready_for_review → sending. Второй параллельный approve получает False."""
        return self._exec(f"UPDATE s1_readiness_jobs SET status='sending', updated_at={self.ph} "
                          f"WHERE id={self.ph} AND status='ready_for_review'", (_now(), job_id)) == 1

    def was_sent(self, job_id: str, report_sha256: str) -> bool:
        return bool(self._exec(f"SELECT 1 FROM s1_approvals WHERE job_id={self.ph} AND report_sha256={self.ph} "
                               f"AND outcome='sent'", (job_id, report_sha256), "one"))

    def log_approval(self, job_id: str, approver: str, report_sha256: str, outcome: str, detail: str = "") -> None:
        p = self.ph
        self._exec(f"INSERT INTO s1_approvals (job_id, approver, approved_at, report_sha256, outcome, detail) "
                   f"VALUES ({p},{p},{p},{p},{p},{p})", (job_id, approver, _now(), report_sha256, outcome, detail[:500]))

    def approvals(self, job_id: str) -> list[dict]:
        rows = self._exec(f"SELECT approver, approved_at, report_sha256, outcome, detail FROM s1_approvals "
                          f"WHERE job_id={self.ph} ORDER BY approved_at", (job_id,), "all")
        return [dict(zip(("approver", "approved_at", "report_sha256", "outcome", "detail"), r)) for r in rows]

    # ---------------------------------------------------------- HQ-028 ----
    def grant_consent(self, job_id: str, by: str, scope: str, recipient: str) -> str:
        import uuid
        cid, p = uuid.uuid4().hex, self.ph
        self._exec(f"INSERT INTO s1_consents (id, job_id, granted_by, scope, recipient, granted_at) "
                   f"VALUES ({p},{p},{p},{p},{p},{p})", (cid, job_id, by, scope, recipient, _now()))
        return cid

    def revoke_consents(self, job_id: str, by: str, recipient: str | None = None) -> int:
        p = self.ph
        sql = f"UPDATE s1_consents SET revoked_at={p}, revoked_by={p} WHERE job_id={p} AND revoked_at IS NULL"
        args = [_now(), by, job_id]
        if recipient:
            sql += f" AND recipient={p}"
            args.append(recipient)
        return self._exec(sql, tuple(args))

    def consents(self, job_id: str) -> list[dict]:
        keys = ("id", "granted_by", "scope", "recipient", "granted_at", "revoked_at", "revoked_by")
        rows = self._exec(f"SELECT {', '.join(keys)} FROM s1_consents WHERE job_id={self.ph} ORDER BY granted_at",
                          (job_id,), "all")
        return [dict(zip(keys, r)) for r in rows]

    def consent_active(self, job_id: str, scope: str, recipient: str) -> bool:
        return any(c["scope"] == scope and c["recipient"] == recipient and not c["revoked_at"]
                   for c in self.consents(job_id))

    def add_transfer(self, job_id: str, kind: str, recipient: str, status: str, by: str, detail: str = "") -> str:
        import uuid
        tid, p = uuid.uuid4().hex, self.ph
        self._exec(f"INSERT INTO s1_transfers (id, job_id, kind, recipient, status, requested_by, requested_at, detail) "
                   f"VALUES ({p},{p},{p},{p},{p},{p},{p},{p})", (tid, job_id, kind, recipient, status, by, _now(), detail[:500]))
        return tid

    def set_transfer(self, tid: str, status: str, g4_by: str | None = None, detail: str = "") -> None:
        p = self.ph
        self._exec(f"UPDATE s1_transfers SET status={p}, g4_by=COALESCE({p}, g4_by), g4_at={p}, detail={p} "
                   f"WHERE id={p}", (status, g4_by, _now(), detail[:500], tid))

    def transfer(self, tid: str) -> dict | None:
        keys = ("id", "job_id", "kind", "recipient", "status", "requested_by", "requested_at", "g4_by", "g4_at", "detail")
        r = self._exec(f"SELECT {', '.join(keys)} FROM s1_transfers WHERE id={self.ph}", (tid,), "one")
        return dict(zip(keys, r)) if r else None

    def add_task_once(self, job_id: str, assignee: str, title: str) -> bool:
        import uuid
        p = self.ph
        try:
            self._exec(f"INSERT INTO s1_tasks (id, job_id, assignee, title, status, created_at) "
                       f"VALUES ({p},{p},{p},{p},'open',{p})", (uuid.uuid4().hex, job_id, assignee, title, _now()))
            return True
        except Exception:  # noqa: BLE001 — уже создана (UNIQUE)
            return False

    def tasks(self, assignee: str | None = None) -> list[dict]:
        keys = ("id", "job_id", "assignee", "title", "status", "created_at")
        sql, args = f"SELECT {', '.join(keys)} FROM s1_tasks", ()
        if assignee:
            sql, args = sql + f" WHERE assignee={self.ph}", (assignee,)
        return [dict(zip(keys, r)) for r in self._exec(sql + " ORDER BY created_at", args, "all")]

    def timer_jobs(self) -> list[str]:
        rows = self._exec("SELECT id FROM s1_readiness_jobs WHERE data_json LIKE '%\"fund_timer\"%'", (), "all")
        return [r[0] for r in rows]

    def log_fact_signoff(self, job_id: str, slot: str, signer: str, role: str, basis: str) -> dict:
        p, at = self.ph, _now()
        self._exec(f"INSERT INTO s1_fact_signoffs (job_id, slot, signer, role, signed_at, basis) "
                   f"VALUES ({p},{p},{p},{p},{p},{p})", (job_id, slot, signer, role, at, basis[:1000]))
        return {"slot": slot, "signer": signer, "role": role, "at": at, "basis": basis}

    def fact_signoffs(self, job_id: str) -> list[dict]:
        keys = ("slot", "signer", "role", "at", "basis")
        rows = self._exec(f"SELECT slot, signer, role, signed_at, basis FROM s1_fact_signoffs WHERE job_id={self.ph} "
                          f"ORDER BY signed_at", (job_id,), "all")
        return [dict(zip(keys, r)) for r in rows]

    def reset_for_rerun(self, job_id: str) -> bool:
        return self._exec(
            f"UPDATE s1_readiness_jobs SET status='received', updated_at={self.ph} "
            f"WHERE id={self.ph} AND status IN ('ready_for_review','awaiting_founder','manual_review','failed','sent')", (_now(), job_id)) == 1

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

    def list(self, limit: int = 50, s3: str = "exclude") -> list[dict]:
        """HQ-062: s3="exclude" — записи S3 (data.s3) не отдаются; "only" — только они; "all" — без фильтра."""
        self.purge_expired()
        rows = self._exec(f"{self._SELECT} ORDER BY created_at DESC", (), "all")
        out = []
        for r in rows:
            d = self._row(r)
            is_s3 = bool(d["data"].get("s3"))
            if (s3 == "exclude" and is_s3) or (s3 == "only" and not is_s3):
                continue
            d.pop("data")  # список — только метаданные
            out.append(d)
            if len(out) >= int(limit):
                break
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
