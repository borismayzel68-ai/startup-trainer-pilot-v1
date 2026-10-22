"""MOS-5: Decision Snapshot, решения IC (Go/Pause/Stop), чек-лист стандарта. Работает с psycopg-соединением.
Вызывающий управляет транзакцией (commit/rollback)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from dealflow.status import change_status

STANDARD_PATH = Path(__file__).resolve().parent.parent / "docs" / "standards" / "ARTUR_STANDARD.md"
STANDARD_MISSING = "стандарт не загружен"

EDITABLE = ("thesis", "deal_type", "buyer", "buyer_when", "trigger_event", "key_risks", "next_action")
COLUMNS = EDITABLE + ("asf_engagement", "evidence_status", "fact_signed_at", "engagement_at", "updated_at")
DECISIONS = ("GO", "PAUSE", "STOP")
DECIDABLE = ("ACTIVE", "DD", "IC", "ON_HOLD")
_QUARTER = re.compile(r"^\d{4}-Q[1-4]$")


class FundError(ValueError):
    pass


def audit(conn, actor_id, action: str, entity: str, entity_id, detail: dict | None = None) -> None:
    conn.execute("INSERT INTO audit_log (actor_id, action, entity, entity_id, detail) VALUES (%s,%s,%s,%s,%s)",
                 (actor_id, action, entity, entity_id, json.dumps(detail or {}, ensure_ascii=False)))


def find_startup(conn, vc_id: str):
    row = conn.execute("SELECT id, vc_id, name, current_status::text FROM startups WHERE vc_id=%s",
                       (vc_id,)).fetchone()
    return None if row is None else {"id": row[0], "vc_id": row[1], "name": row[2], "status": row[3]}


def load_snapshot(conn, startup_id: int) -> dict | None:
    row = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM decision_snapshots WHERE startup_id=%s",
                       (startup_id,)).fetchone()
    if row is None:
        return None
    snap = dict(zip(COLUMNS, row))
    for k in ("fact_signed_at", "engagement_at", "updated_at"):
        snap[k] = snap[k].isoformat() if snap[k] else None
    return snap


def save_snapshot(conn, startup_id: int, fields: dict, actor_id) -> dict:
    """Правка текстовых полей. Статус доказательств этим путём не повышается; любая правка сбрасывает подпись FACT."""
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise FundError(f"недопустимые поля: {sorted(unknown)}")
    vals = {k: str(fields[k]).strip() for k in fields}
    conn.execute("INSERT INTO decision_snapshots (startup_id) VALUES (%s) ON CONFLICT (startup_id) DO NOTHING",
                 (startup_id,))
    sets = [f"{k}=%s" for k in vals] + ["evidence_status='hypothesis'", "fact_signed_by=NULL",
                                        "fact_signed_at=NULL", "updated_by=%s", "updated_at=now()"]
    conn.execute(f"UPDATE decision_snapshots SET {', '.join(sets)} WHERE startup_id=%s",
                 (*vals.values(), actor_id, startup_id))
    audit(conn, actor_id, "snapshot.save", "startup", startup_id, {"fields": sorted(vals)})
    return load_snapshot(conn, startup_id)


def set_engagement(conn, startup_id: int, value: str, actor_id) -> dict:
    """Фиксация участия ASF (Engagement gate). Вызывать только для роли owner."""
    if value not in ("FIXED", "DECLINED", "NOT_DECIDED"):
        raise FundError("asf_engagement: FIXED | DECLINED | NOT_DECIDED")
    conn.execute("INSERT INTO decision_snapshots (startup_id) VALUES (%s) ON CONFLICT (startup_id) DO NOTHING",
                 (startup_id,))
    conn.execute("UPDATE decision_snapshots SET asf_engagement=%s, engagement_by=%s, engagement_at=now(), "
                 "updated_at=now() WHERE startup_id=%s", (value, actor_id, startup_id))
    audit(conn, actor_id, "snapshot.engagement", "startup", startup_id, {"value": value})
    return load_snapshot(conn, startup_id)


def sign_fact(conn, startup_id: int, actor_id) -> dict:
    """FACT только по подписи владельца; гипотеза сама в факт не превращается."""
    snap = load_snapshot(conn, startup_id)
    if not snap or not snap["thesis"]:
        raise FundError("нечего подписывать: тезис пуст")
    conn.execute("UPDATE decision_snapshots SET evidence_status='fact', fact_signed_by=%s, fact_signed_at=now(), "
                 "updated_at=now() WHERE startup_id=%s", (actor_id, startup_id))
    audit(conn, actor_id, "snapshot.sign_fact", "startup", startup_id)
    return load_snapshot(conn, startup_id)


def record_decision(conn, startup_id: int, quarter: str, decision: str, reason: str, actor_id) -> dict:
    """Квартальное решение IC: ic_decisions (history) + при смене статуса deal_status_history + audit_log."""
    decision = (decision or "").upper()
    if decision not in DECISIONS:
        raise FundError("decision: GO | PAUSE | STOP")
    if not _QUARTER.match(quarter or ""):
        raise FundError("quarter: формат YYYY-Qn")
    if not (reason or "").strip():
        raise FundError("reason обязателен")
    row = conn.execute("SELECT current_status::text FROM startups WHERE id=%s FOR UPDATE", (startup_id,)).fetchone()
    if row is None:
        raise LookupError(f"startup {startup_id} не найден")
    before = row[0]
    if before not in DECIDABLE:
        raise FundError(f"решение IC невозможно в статусе {before}")
    target = {"GO": "ACTIVE" if before == "ON_HOLD" else before, "PAUSE": "ON_HOLD", "STOP": "PASS"}[decision]
    if target != before:
        change_status(conn, startup_id, target, reason=f"IC {quarter} {decision}: {reason.strip()}",
                      changed_by=actor_id)
    conn.execute("INSERT INTO ic_decisions (startup_id, quarter, decision, reason, decided_by) "
                 "VALUES (%s,%s,%s,%s,%s)", (startup_id, quarter, decision, reason.strip(), actor_id))
    audit(conn, actor_id, "ic.decision", "startup", startup_id,
          {"quarter": quarter, "decision": decision, "from": before, "to": target})
    return {"quarter": quarter, "decision": decision, "from_status": before, "to_status": target}


def standard_checklist(path: Path | None = None) -> dict:
    """Чек-лист из docs/standards/ARTUR_STANDARD.md дословно (заголовки и пункты `- [ ]`). Файла нет → «не загружен»."""
    p = path or STANDARD_PATH
    if not p.is_file():
        return {"loaded": False, "label": STANDARD_MISSING, "items": []}
    text = p.read_text(encoding="utf-8")
    items, section = [], ""
    for line in text.splitlines():
        h = re.match(r"^#{1,6}\s+(.*)", line)
        if h:
            section = h.group(1).strip()
            continue
        m = re.match(r"^\s*[-*]\s+\[[ xX]\]\s+(.*)", line)
        if m:
            items.append({"section": section, "text": m.group(1).strip()})
    return {"loaded": True, "label": "docs/standards/ARTUR_STANDARD.md", "items": items,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
