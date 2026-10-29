"""MOS-1 (#52): хранилище users / role_grants / audit_log.

Postgres — применяется db/migrations/001_roles_access.sql (единый источник схемы).
SQLite (тесты, локально) — эквивалентный DDL. Только синтетические данные в тестах.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

MIGRATION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "migrations", "001_roles_access.sql")

ROLE_LABELS = {
    "founder": "Основатель", "investor": "Фонд / инвестор", "expert": "Эксперт",
    "signer": "Владелец / подписант", "clinic": "Клиника", "syndicator": "Синдикатор",
    "owner": "Владелец фонда", "fund_staff": "Сотрудник фонда",
}

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS roles (code TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS role_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_code TEXT NOT NULL REFERENCES roles(code), scope TEXT NOT NULL DEFAULT '*',
    granted_by INTEGER REFERENCES users(id), granted_at TEXT NOT NULL, revoked_at TEXT,
    UNIQUE (user_id, role_code, scope));
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT, action TEXT NOT NULL,
    object TEXT, result TEXT NOT NULL CHECK (result IN ('allow', 'deny')), ip TEXT, detail TEXT);
"""


def utc_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class AuthStore:
    def __init__(self, database_url):
        self.url = database_url
        self.pg = database_url.startswith(("postgres://", "postgresql://"))
        self._lock = threading.RLock()
        self._mem = None
        if not self.pg:
            path = database_url[len("sqlite:///"):]
            self.path = path
            if path == ":memory:":
                self._mem = sqlite3.connect(":memory:", check_same_thread=False)
        self.initialize()

    @contextmanager
    def _conn(self):
        with self._lock:
            if self.pg:
                import psycopg
                conn = psycopg.connect(self.url)
            else:
                conn = self._mem or sqlite3.connect(self.path)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                if conn is not self._mem:
                    conn.close()

    def _run(self, sql, params=(), fetch=False):
        if self.pg:
            sql = sql.replace("?", "%s")
        with self._conn() as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall() if fetch else None

    def initialize(self):
        with self._conn() as conn:
            if self.pg:
                with open(MIGRATION, encoding="utf-8") as f:
                    conn.execute(f.read())
            else:
                conn.executescript(SQLITE_DDL)
                conn.executemany("INSERT OR IGNORE INTO roles (code, label) VALUES (?, ?)", list(ROLE_LABELS.items()))

    # ---- users / grants -------------------------------------------------
    def upsert_user(self, email, display_name):
        email = email.strip().lower()
        self._run("INSERT INTO users (email, display_name, created_at) VALUES (?, ?, ?) ON CONFLICT (email) DO NOTHING",
                  (email, display_name or email, utc_iso()))
        return self.user_by_email(email)

    def user_by_email(self, email):
        rows = self._run("SELECT id, email, display_name FROM users WHERE email = ?", (email.strip().lower(),), fetch=True)
        return {"id": rows[0][0], "email": rows[0][1], "display_name": rows[0][2]} if rows else None

    def user_by_id(self, user_id):
        rows = self._run("SELECT id, email, display_name FROM users WHERE id = ?", (user_id,), fetch=True)
        return {"id": rows[0][0], "email": rows[0][1], "display_name": rows[0][2]} if rows else None

    def ensure_grant(self, user_id, role, scope="*"):
        """Создаёт грант, если строки нет. Отозванный грант НЕ воскрешается."""
        self._run("INSERT INTO role_grants (user_id, role_code, scope, granted_at) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT (user_id, role_code, scope) DO NOTHING", (user_id, role, scope, utc_iso()))

    def revoke(self, user_id, role, scope="*"):
        self._run("UPDATE role_grants SET revoked_at = ? WHERE user_id = ? AND role_code = ? AND scope = ? "
                  "AND revoked_at IS NULL", (utc_iso(), user_id, role, scope))

    def active_grants(self, user_id):
        rows = self._run("SELECT role_code, scope FROM role_grants WHERE user_id = ? AND revoked_at IS NULL",
                         (user_id,), fetch=True)
        return [(r[0], r[1]) for r in rows]

    # ---- audit ------------------------------------------------------------
    def audit(self, actor, action, obj, result, ip, detail=None):
        assert result in ("allow", "deny")
        self._run("INSERT INTO audit_log (at, actor, action, object, result, ip, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (utc_iso(), actor, action, obj, result, ip, detail))

    def _audit_dicts(self, rows):
        keys = ("at", "actor", "action", "object", "result", "ip", "detail")
        return [dict(zip(keys, (str(v) if k == "at" else v for k, v in zip(keys, r)))) for r in rows]

    def audit_rows(self, limit=200):
        rows = self._run("SELECT at, actor, action, object, result, ip, detail FROM audit_log ORDER BY id DESC LIMIT ?",
                         (int(limit),), fetch=True)
        return self._audit_dicts(rows)

    def audit_for(self, action, obj, result):
        """Все строки одного действия по объекту, без отсечения последними N записями журнала."""
        rows = self._run(
            "SELECT at, actor, action, object, result, ip, detail FROM audit_log "
            "WHERE action = ? AND object = ? AND result = ? ORDER BY id DESC",
            (action, obj, result), fetch=True)
        return self._audit_dicts(rows)
