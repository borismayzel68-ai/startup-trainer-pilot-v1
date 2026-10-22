"""MOS-2: единый словарь статусов dealflow, таблица переходов, маппинг источников.
Правило: статус не выдумывается; при неоднозначности — NOT_SCREENED + флаг ручной проверки."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

STATUSES = ("NOT_SCREENED", "SCREENING", "ACTIVE", "DD", "IC", "INVESTED", "PASS", "ON_HOLD")

TRANSITIONS: dict[str, frozenset[str]] = {
    "NOT_SCREENED": frozenset({"SCREENING", "PASS", "ON_HOLD"}),
    "SCREENING": frozenset({"ACTIVE", "PASS", "ON_HOLD"}),
    "ACTIVE": frozenset({"DD", "PASS", "ON_HOLD"}),
    "DD": frozenset({"ACTIVE", "IC", "PASS", "ON_HOLD"}),
    "IC": frozenset({"DD", "INVESTED", "PASS", "ON_HOLD"}),
    "INVESTED": frozenset(),
    "PASS": frozenset({"SCREENING"}),
    "ON_HOLD": frozenset({"SCREENING", "ACTIVE", "DD", "PASS"}),
}


class InvalidTransition(ValueError):
    pass


def validate_transition(from_status: str, to_status: str) -> None:
    if from_status not in TRANSITIONS or to_status not in TRANSITIONS:
        raise InvalidTransition(f"неизвестный статус: {from_status!r} -> {to_status!r}")
    if to_status not in TRANSITIONS[from_status]:
        raise InvalidTransition(f"переход {from_status} -> {to_status} не разрешён")


def create_startup(conn, vc_id: str, name: str, *, sector=None, stage=None, source=None,
                   status: str = "NOT_SCREENED", changed_by=None, reason: str = "создан") -> int:
    """Создаёт стартап и первичную строку истории (from_status NULL)."""
    if status not in STATUSES:
        raise InvalidTransition(f"неизвестный статус: {status!r}")
    sid = conn.execute(
        "INSERT INTO startups (vc_id, name, sector, stage, source, current_status) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id", (vc_id, name, sector, stage, source, status)).fetchone()[0]
    conn.execute(
        "INSERT INTO deal_status_history (startup_id, from_status, to_status, reason, changed_by) "
        "VALUES (%s,NULL,%s,%s,%s)", (sid, status, reason, changed_by))
    return sid


def change_status(conn, startup_id: int, to_status: str, *, reason: str = "", changed_by=None) -> None:
    """Валидирует переход, обновляет startups и пишет строку в deal_status_history (одна транзакция)."""
    row = conn.execute("SELECT current_status::text FROM startups WHERE id=%s FOR UPDATE",
                       (startup_id,)).fetchone()
    if row is None:
        raise LookupError(f"startup {startup_id} не найден")
    validate_transition(row[0], to_status)
    if to_status in ("DD", "IC"):  # MOS-5: шлюзы Operating Manual, серверная проверка
        from fund_view.service import load_snapshot
        from fund_view.gates import check_transition
        check_transition(to_status, load_snapshot(conn, startup_id))
    conn.execute("UPDATE startups SET current_status=%s, updated_at=now() WHERE id=%s",
                 (to_status, startup_id))
    conn.execute(
        "INSERT INTO deal_status_history (startup_id, from_status, to_status, reason, changed_by) "
        "VALUES (%s,%s,%s,%s,%s)", (startup_id, row[0], to_status, reason, changed_by))


@dataclass(frozen=True)
class Mapped:
    status: str
    needs_review: bool = False
    note: str = ""


def _review(note: str) -> Mapped:
    return Mapped("NOT_SCREENED", True, note)


ZONE_INBOX = "00 — INBOX / НОВЫЕ"
ZONE_ACTIVE = "10 — ACTIVE DEALFLOW / SCREENING & DD"
_STAGE_TEXT = {"SCREENING": "SCREENING", "ACTIVE": "ACTIVE", "DD": "DD"}


def map_zone(zone: str, stage_text: Optional[str] = None) -> Mapped:
    z = (zone or "").strip()
    if z == ZONE_INBOX:
        return Mapped("NOT_SCREENED")
    if z == ZONE_ACTIVE:
        st = _STAGE_TEXT.get((stage_text or "").strip().upper())
        if st:
            return Mapped(st)
        return _review(f"зона 10: этап {stage_text!r} не распознан")
    return _review(f"неизвестная зона {zone!r}")


# (INVESTMENT_VIEW, PROCESS_ACTION) -> статус. Остальное — флаг.
_CORE_TABLE = {
    ("CONDITIONAL", "MONITOR"): "ON_HOLD",
    ("CONDITIONAL", "REQUEST_EVIDENCE"): "DD",
    ("CONDITIONAL", ""): "ACTIVE",
    ("UNATTRACTIVE", "MONITOR"): "ON_HOLD",
    ("UNATTRACTIVE", "CLOSE"): "PASS",
}


def map_core(vc_id: str = "", screening: str = "", investment_view: str = "",
             process_action: str = "") -> Mapped:
    if (vc_id or "").strip().upper().startswith("IM-"):
        return Mapped("INVESTED")
    s = (screening or "").strip().upper()
    if s == "NOT YET SCREENED":
        return Mapped("NOT_SCREENED")
    if s == "ANALYZED":
        v = (investment_view or "").strip().upper()
        a = (process_action or "").strip().upper()
        st = _CORE_TABLE.get((v, a))
        if st:
            return Mapped(st)
        return _review(f"ANALYZED: комбинация {v!r}/{a!r} не описана")
    return _review(f"неизвестное значение screening {screening!r}")
