"""Минимальный журнал действий пользователей MOS (обязателен к 05.11).

Только факты о событии: кто (роль + синтетический/псевдонимный id), что, когда.
Без текстов ответов, без статуса оплаты. Ничего не отправляется наружу.
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

ROLES = ("startup", "private_investor", "fund", "portfolio_company")

# Событие активации -> (роль, срок в днях). См. docs/system/LIVING_SPEC.md.
ACTIVATION_EVENTS = {
    "fund_deal_memo_draft": ("fund", 7),
    "portfolio_kpi_update_sent": ("portfolio_company", 14),
    "startup_profile_and_readiness_report": ("startup", 3),
    "investor_brief_and_question": ("private_investor", 7),
}

_FORBIDDEN_KEYS = {"payment", "payment_status", "paid", "email", "text", "answer"}
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class ActivityLog:
    def __init__(self, path=":memory:"):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS activity_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, role TEXT NOT NULL,"
            "user_id TEXT NOT NULL, event TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}')"
        )
        self._conn.commit()

    def record(self, role, user_id, event, meta=None, ts=None):
        if role not in ROLES:
            raise ValueError(f"unknown role: {role}")
        meta = dict(meta or {})
        bad = _FORBIDDEN_KEYS & {k.lower() for k in meta}
        if bad:
            raise ValueError(f"meta must not contain: {sorted(bad)}")
        ts = ts or datetime.now(timezone.utc).strftime(_TS_FORMAT)
        with self._lock:
            self._conn.execute(
                "INSERT INTO activity_events (ts, role, user_id, event, meta) VALUES (?,?,?,?,?)",
                (ts, role, str(user_id), event, json.dumps(meta, ensure_ascii=False, sort_keys=True)),
            )
            self._conn.commit()

    def events(self, user_id=None):
        query, args = "SELECT ts, role, user_id, event, meta FROM activity_events", ()
        if user_id is not None:
            query, args = query + " WHERE user_id = ?", (str(user_id),)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY id", args).fetchall()
        return [
            {"ts": r[0], "role": r[1], "user_id": r[2], "event": r[3], "meta": json.loads(r[4])}
            for r in rows
        ]

    def activated(self, user_id, signup_ts):
        """True, если событие активации роли наступило в срок (дни от регистрации)."""
        signup = datetime.strptime(signup_ts, _TS_FORMAT)
        for e in self.events(user_id):
            spec = ACTIVATION_EVENTS.get(e["event"])
            if spec and spec[0] == e["role"]:
                at = datetime.strptime(e["ts"], _TS_FORMAT)
                if (at - signup).days <= spec[1]:
                    return True
        return False
