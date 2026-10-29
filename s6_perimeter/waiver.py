"""Исключение из шлюза S6 — только явное решение человека в аудит-логе.

Роль admin/owner и общий admin-токен сами по себе кейс без пройденного периметра не пропускают.
Если приложение подняло audit_log MOS-1, решение читается только оттуда. Без него (узкий тест
шлюза) ту же запись держит журнал s1_approvals с пометкой source=audit_log — подмена поля
release в данных шлюза этой пометки не создаёт.
"""
from __future__ import annotations

WAIVER_ACTION = "s6.human_waiver"
BLOCK_ACTION = "s6.gate"
WAIVER_OUTCOME = "s6_human_waiver"
BLOCK_OUTCOME = "s6_gate_blocked"
NOT_A_PERSON = frozenset({"admin", "system", "token"})


def is_person(actor) -> bool:
    return isinstance(actor, str) and bool(actor.strip()) and actor.strip().lower() not in NOT_A_PERSON


def _auth_ready() -> bool:
    try:
        from flask import current_app
        return "auth_store" in current_app.extensions
    except RuntimeError:
        return False


def record_waiver(store, gate_id: str, actor: str, digest: str, reason: str) -> None:
    if not is_person(actor):
        raise PermissionError("решение человека записывает именной актор, не admin и не система")
    detail = f"source=audit_log sha256={digest} reason={reason}"
    if _auth_ready():
        import auth
        auth.audit(WAIVER_ACTION, gate_id, "allow", actor=actor, detail=detail)
    store.log_approval(gate_id, actor, digest, WAIVER_OUTCOME, detail)


def record_block(store, gate_id: str, actor: str, role: str, digest: str) -> None:
    detail = f"blocked=NEEDS_EVIDENCE role={role} sha256={digest}"
    who = actor if isinstance(actor, str) and actor.strip() else "unknown"
    if gate_id and digest:
        store.log_approval(gate_id, who, digest, BLOCK_OUTCOME, detail)
    if _auth_ready():
        import auth
        auth.audit(BLOCK_ACTION, gate_id or "/s2/checks", "deny", actor=who, detail=detail)


def human_decision(store, gate_id: str, digest: str) -> dict | None:
    """Решение из аудит-лога на этот хеш данных или None."""
    if _auth_ready():
        import auth
        for row in auth.store().audit_rows(500):
            detail = row.get("detail") or ""
            if (row.get("action") == WAIVER_ACTION and row.get("object") == gate_id
                    and row.get("result") == "allow" and f"sha256={digest}" in detail
                    and is_person(row.get("actor"))):
                return {"by": row["actor"], "sha256": digest, "source": "audit_log"}
        return None
    for row in reversed(store.approvals(gate_id)):
        if (row["outcome"] == WAIVER_OUTCOME and row["report_sha256"] == digest
                and is_person(row["approver"]) and "source=audit_log" in (row.get("detail") or "")):
            return {"by": row["approver"], "sha256": digest, "source": "audit_log"}
    return None
