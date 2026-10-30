"""Журнал согласия на обработку ПДн.

Postgres: схема только из db/migrations/006_pdn_consent.sql (шаг миграции, не старт web).
SQLite (тесты и локальный файл): тот же набор колонок, свой DDL — как auth_store для 001.
В журнал пишутся email, версия текста, время UTC, IP и форма. Анкета и телефон сюда не кладутся.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

COLUMNS = ("id", "user_email", "text_version", "consented_at", "ip", "revoked_at", "flow")

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS pdn_consents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email TEXT NOT NULL,
    text_version TEXT NOT NULL,
    consented_at TEXT NOT NULL,
    ip TEXT,
    revoked_at TEXT,
    flow TEXT NOT NULL CHECK (flow IN ('s1_intake', 'investor_login'))
);
CREATE INDEX IF NOT EXISTS pdn_consents_email_idx ON pdn_consents (user_email);
"""

_INSERT = (
    "INSERT INTO pdn_consents (user_email, text_version, consented_at, ip, flow) "
    "VALUES (?, ?, ?, ?, ?) RETURNING " + ", ".join(COLUMNS)
)
_REVOKE = (
    "UPDATE pdn_consents SET revoked_at = ? WHERE user_email = ? AND revoked_at IS NULL "
    "RETURNING " + ", ".join(COLUMNS)
)
_REVOKE_ONE = (
    "UPDATE pdn_consents SET revoked_at = ? WHERE id = ? AND user_email = ? AND revoked_at IS NULL "
    "RETURNING " + ", ".join(COLUMNS)
)
_BY_EMAIL = "SELECT " + ", ".join(COLUMNS) + " FROM pdn_consents WHERE user_email = ? ORDER BY id"
_BY_ID = "SELECT " + ", ".join(COLUMNS) + " FROM pdn_consents WHERE id = ?"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc_z(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


class PdnConsentStore:
    def __init__(self, database_url: str):
        self.url = database_url
        self.pg = database_url.startswith(("postgres://", "postgresql://"))
        self._lock = threading.RLock()
        self._mem = None
        if not self.pg:
            prefix = "sqlite:///"
            if not database_url.startswith(prefix):
                raise ValueError("pdn consent: нужен sqlite:/// или postgresql://")
            self.path = database_url[len(prefix):]
            if self.path == ":memory:":
                self._mem = sqlite3.connect(":memory:", check_same_thread=False)
        self.initialize()

    def initialize(self) -> None:
        if self.pg:
            return
        with self._conn() as conn:
            conn.executescript(SQLITE_DDL)

    def _connect(self):
        if self.pg:
            import psycopg
            return psycopg.connect(self.url)
        if self._mem is not None:
            return self._mem
        return sqlite3.connect(self.path)

    def _conn(self):
        return _Conn(self)

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.pg else sql

    def _stamp(self, when: datetime | None) -> datetime | str:
        when = when or utc_now()
        if self.pg:
            return when
        return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _rows(self, sql: str, params: tuple, fetch: str):
        with self._lock, self._conn() as conn:
            cur = conn.execute(self._sql(sql), params)
            if fetch == "all":
                return cur.fetchall()
            return cur.fetchone()

    def record(self, email: str, text_version: str, ip: str, flow: str, when: datetime | None = None) -> dict:
        row = self._rows(
            _INSERT,
            (email.strip().lower(), text_version, self._stamp(when), (ip or "")[:64], flow),
            "one",
        )
        return _norm(row)

    def revoke(self, email: str, when: datetime | None = None) -> list[dict]:
        rows = self._rows(_REVOKE, (self._stamp(when), email.strip().lower()), "all")
        return [_norm(r) for r in rows]

    def revoke_ids(self, email: str, ids, when: datetime | None = None) -> list[dict]:
        """Отзыв только перечисленных строк этого email (строки, отмеченные в одной сессии браузера)."""
        out = []
        for consent_id in ids:
            try:
                consent_id = int(consent_id)
            except (TypeError, ValueError):
                continue
            row = self._rows(_REVOKE_ONE, (self._stamp(when), consent_id, email.strip().lower()), "one")
            if row:
                out.append(_norm(row))
        return out

    def for_email(self, email: str) -> list[dict]:
        return [_norm(r) for r in self._rows(_BY_EMAIL, (email.strip().lower(),), "all")]

    def by_id(self, consent_id) -> dict | None:
        try:
            consent_id = int(consent_id)
        except (TypeError, ValueError):
            return None
        row = self._rows(_BY_ID, (consent_id,), "one")
        return _norm(row) if row else None


class _Conn:
    def __init__(self, store: PdnConsentStore):
        self.store = store
        self.conn = None

    def __enter__(self):
        self.conn = self.store._connect()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        if self.conn is not self.store._mem:
            self.conn.close()
        return False


def _norm(row) -> dict:
    data = dict(zip(COLUMNS, row))
    data["consented_at"] = as_utc_z(data["consented_at"])
    data["revoked_at"] = as_utc_z(data["revoked_at"])
    data["ip"] = data["ip"] or ""
    return data
