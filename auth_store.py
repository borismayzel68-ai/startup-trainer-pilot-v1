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

MIGRATION_007 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "migrations", "007_access_invites.sql")
DEFAULT_TENANT = "pilot"

ROLE_LABELS = {
    "founder": "Основатель", "investor": "Фонд / инвестор", "expert": "Эксперт",
    "signer": "Владелец / подписант", "clinic": "Клиника", "syndicator": "Синдикатор",
    "owner": "Владелец фонда", "fund_staff": "Сотрудник фонда",
}

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, blocked_at TEXT);
CREATE TABLE IF NOT EXISTS roles (code TEXT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS role_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_code TEXT NOT NULL REFERENCES roles(code), scope TEXT NOT NULL DEFAULT '*',
    granted_by INTEGER REFERENCES users(id), granted_at TEXT NOT NULL, revoked_at TEXT,
    UNIQUE (user_id, role_code, scope));
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT, action TEXT NOT NULL,
    object TEXT, result TEXT NOT NULL CHECK (result IN ('allow', 'deny')), ip TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS access_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL DEFAULT 'pilot', email TEXT NOT NULL,
    role TEXT NOT NULL REFERENCES roles(code), scope TEXT NOT NULL DEFAULT '*', invited_by TEXT,
    created_at TEXT NOT NULL, revoked_at TEXT);
CREATE INDEX IF NOT EXISTS access_invites_email_idx ON access_invites (lower(email));
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
                for path in (MIGRATION, MIGRATION_007):  # обе идемпотентны
                    with open(path, encoding="utf-8") as f:
                        conn.execute(f.read())
            else:
                conn.executescript(SQLITE_DDL)
                conn.executemany("INSERT OR IGNORE INTO roles (code, label) VALUES (?, ?)", list(ROLE_LABELS.items()))
                if "blocked_at" not in {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}:
                    conn.execute("ALTER TABLE users ADD COLUMN blocked_at TEXT")  # SQLite-файл, созданный до 007

    # ---- users / grants -------------------------------------------------
    def upsert_user(self, email, display_name):
        email = email.strip().lower()
        self._run("INSERT INTO users (email, display_name, created_at) VALUES (?, ?, ?) ON CONFLICT (email) DO NOTHING",
                  (email, display_name or email, utc_iso()))
        return self.user_by_email(email)

    def user_by_email(self, email):
        rows = self._run("SELECT id, email, display_name, blocked_at FROM users WHERE email = ?",
                         (email.strip().lower(),), fetch=True)
        return self._user(rows[0]) if rows else None

    def user_by_id(self, user_id):
        rows = self._run("SELECT id, email, display_name, blocked_at FROM users WHERE id = ?", (user_id,), fetch=True)
        return self._user(rows[0]) if rows else None

    @staticmethod
    def _user(r):
        return {"id": r[0], "email": r[1], "display_name": r[2], "blocked_at": str(r[3]) if r[3] else None}

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

    # ---- invites / block (FEAT-001 T-002) ---------------------------------
    def create_invite(self, email, role, scope="*", invited_by=None, tenant_id=DEFAULT_TENANT):
        """Приглашение на email (нижний регистр). Ошибки валидации — ValueError, строка не создаётся.
        Дубль действующего (email, role, scope) не создаётся — возвращается существующий id."""
        email = (email or "").strip().lower()
        scope = (scope or "*").strip() or "*"
        if "@" not in email or role not in ROLE_LABELS:
            raise ValueError("invalid_invite")
        if role == "founder" and scope == "*":
            raise ValueError("founder_needs_project")  # spec §9: основатель без проекта — не создаётся
        rows = self._run("SELECT id FROM access_invites WHERE tenant_id = ? AND lower(email) = ? AND role = ? "
                         "AND scope = ? AND revoked_at IS NULL", (tenant_id, email, role, scope), fetch=True)
        if rows:
            return rows[0][0]
        self._run("INSERT INTO access_invites (tenant_id, email, role, scope, invited_by, created_at) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (tenant_id, email, role, scope, invited_by, utc_iso()))
        rows = self._run("SELECT max(id) FROM access_invites WHERE lower(email) = ?", (email,), fetch=True)
        return rows[0][0]

    def revoke_invite(self, invite_id):
        """Отзывает приглашение; возвращает его (email, role, scope) или None, если нет или уже отозвано."""
        rows = self._run("SELECT email, role, scope FROM access_invites WHERE id = ? AND revoked_at IS NULL",
                         (invite_id,), fetch=True)
        if not rows:
            return None
        self._run("UPDATE access_invites SET revoked_at = ? WHERE id = ?", (utc_iso(), invite_id))
        return {"email": rows[0][0], "role": rows[0][1], "scope": rows[0][2]}

    def active_invites(self, email=None):
        """Действующие приглашения: {email: [(role, scope), ...]} (или только по одному email)."""
        sql = "SELECT lower(email), role, scope FROM access_invites WHERE revoked_at IS NULL"
        params = ()
        if email is not None:
            sql, params = sql + " AND lower(email) = ?", (email.strip().lower(),)
        result = {}
        for e, role, scope in self._run(sql + " ORDER BY id", params, fetch=True):
            result.setdefault(e, []).append((role, scope))
        return result

    def block_user(self, user_id):
        self._run("UPDATE users SET blocked_at = ? WHERE id = ? AND blocked_at IS NULL", (utc_iso(), user_id))

    def unblock_user(self, user_id):
        self._run("UPDATE users SET blocked_at = NULL WHERE id = ?", (user_id,))

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
