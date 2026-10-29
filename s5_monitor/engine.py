"""HQ-031 · светофор мониторинга портфеля. Считает код, не модель.

Читает срез в форме HQ-030 (s5_monitor.schema) и не пишет в базу. Пороги по умолчанию —
комментарий к #16 (CANON v1.8, Д-39 / Д-28): красный при runway < 6 мес, жёлтый при runway < 9 мес.
Инвестор может сдвинуть каждый порог в диапазоне 6–18 мес; в ответе видно, какой порог действует.

Светофор паспорта MOS-4 (portfolio/rag.py, Д-68: красный < 9, жёлтый до 12) — другой экран.
Его пороги этот модуль не меняет.

FACT принимается только с verified_by аналитика или владельца (Д-41). Черновик действий
не отправляется: sent всегда False (Human Gate).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from .schema import FACT_ROLES, PROMISE_STATUSES

RED, YELLOW, GREEN = "RED", "YELLOW", "GREEN"
LABELS = {RED: "красный", YELLOW: "жёлтый", GREEN: "зелёный"}
VERTICALS = frozenset({"ls", "life_sciences", "medtech", "biotech", "healthtech"})
THRESHOLD_MIN = Decimal("6")
THRESHOLD_MAX = Decimal("18")
DEFAULT_RED = Decimal("6")
DEFAULT_YELLOW = Decimal("9")
UPDATE_STALE_DAYS = 14
CASH_STALE_DAYS = 60


class InvalidThresholds(ValueError):
    pass


class Stop(ValueError):
    """Пустой вход: светофор не ставится."""


def _dec(value) -> Decimal:
    return Decimal(str(value))


def thresholds_of(raw: dict | None) -> tuple[Decimal, Decimal]:
    """(красный ниже, жёлтый ниже). Оба в 6–18, красный строго меньше жёлтого."""
    raw = raw or {}
    red = _dec(raw.get("runway_red_below", DEFAULT_RED))
    yellow = _dec(raw.get("runway_yellow_below", DEFAULT_YELLOW))
    for name, value in (("runway_red_below", red), ("runway_yellow_below", yellow)):
        if not THRESHOLD_MIN <= value <= THRESHOLD_MAX:
            raise InvalidThresholds(f"{name}={value} вне диапазона {THRESHOLD_MIN}–{THRESHOLD_MAX} мес")
    if red >= yellow:
        raise InvalidThresholds("runway_red_below должен быть меньше runway_yellow_below")
    return red, yellow


def screen_facts(claims: list | None) -> tuple[list[dict], list[dict]]:
    """FACT без verified_by аналитика или владельца отклоняется и в факты не попадает."""
    accepted, rejected = [], []
    for claim in claims or []:
        if not isinstance(claim, dict):
            rejected.append({"label": "FACT", "reason": "не запись"})
            continue
        if claim.get("label") == "FACT":
            who = claim.get("verified_by")
            if not isinstance(who, str) or who.strip() not in FACT_ROLES:
                rejected.append({**claim, "reason": "FACT без verified_by аналитика или владельца"})
                continue
        accepted.append(claim)
    return accepted, rejected


def runway_months(metrics: dict, as_of: date) -> Optional[Decimal]:
    """(касса − известные обязательства) / регулярный расход, от даты кассы.

    Обещанные поступления и гранты до зачисления в расчёт не входят — аргументов под них нет.
    """
    if not isinstance(metrics, dict):
        return None
    cash, burn = metrics.get("cash"), metrics.get("regular_burn")
    if cash is None or burn is None:
        return None
    burn_d = _dec(burn)
    if burn_d <= 0:
        return None
    net = _dec(cash) - _dec(metrics.get("obligations") or 0)
    months = net / burn_d
    cash_date = metrics.get("cash_date")
    if isinstance(cash_date, date):
        months -= Decimal((as_of - cash_date).days) / Decimal(30)
    return months


def _promise_rows(promises) -> list[dict]:
    if not isinstance(promises, list):
        return []
    rows = []
    for row in promises:
        if isinstance(row, dict) and row.get("status") in PROMISE_STATUSES:
            rows.append(row)
    return rows


def _attach_hq042(case: dict, company_id: str) -> dict:
    """Закрытый #41: сверка с паспортом входа и флаг «новый раунд → проверка частичного выхода».

    Вопросы наружу не задаются. Правила частичного выхода в #41 не заданы — флаг несёт NEEDS_EVIDENCE.
    """
    from portfolio.access import Actor
    from portfolio_update import NEEDS_EVIDENCE, EntryPassport, PortfolioUpdates, reconcile

    actor = Actor("fund_staff")
    out: dict[str, Any] = {"sent": False}
    if case.get("new_round"):
        flag = PortfolioUpdates().mark_new_round(company_id, str(case["new_round"]), actor=actor)
        out["partial_exit_check"] = {
            "status": flag.status,
            "rules": flag.rules or NEEDS_EVIDENCE,
            "round_label": flag.round_label,
            "sent": False,
        }
    passport, update = case.get("entry_passport"), case.get("update")
    if isinstance(passport, dict) and isinstance(update, dict):
        diffs = reconcile(EntryPassport(company_id, passport), update, actor=actor)
        out["passport_diffs"] = [
            {"field": d.field, "kind": d.kind, "entry_value": d.entry_value, "update_value": d.update_value}
            for d in diffs
        ]
    return out


def evaluate(case: dict) -> dict:
    """Один прогон = одна компания на одну дату. Повтор на тех же данных даёт тот же цвет."""
    if not isinstance(case, dict):
        raise Stop("пустой вход")
    company_id = case.get("company_id")
    as_of = case.get("as_of")
    vertical = case.get("vertical")
    if not isinstance(company_id, str) or not company_id.strip() or not isinstance(as_of, date):
        raise Stop("нет шапки или даты среза")
    if vertical not in VERTICALS:
        raise Stop("компания вне LS")

    red_below, yellow_below = thresholds_of(case.get("thresholds"))
    accepted, rejected = screen_facts(case.get("claims"))
    promises = _promise_rows(case.get("promises"))
    thesis = case.get("thesis")
    has_thesis = isinstance(thesis, str) and bool(thesis.strip())
    base = {
        "company_id": company_id,
        "synthetic": True,
        "thresholds": {
            "runway_red_below": format(red_below, "f"),
            "runway_yellow_below": format(yellow_below, "f"),
        },
        "threshold_note": f"Порог: красный < {red_below} мес, жёлтый < {yellow_below} мес",
        "facts_accepted": accepted,
        "facts_rejected": rejected,
        "sent": False,
        "dispatched": False,
    }
    if not has_thesis and not promises:
        return {**base, "state": "HOLD", "color": None, "color_label": None, "runway_months": None,
                "drivers": [], "draft_actions": [], "human_gate": True, **_attach_hq042(case, company_id)}

    metrics = case.get("metrics") if isinstance(case.get("metrics"), dict) else {}
    runway = runway_months(metrics, as_of)
    red, yellow = [], []

    thesis_miss = [p for p in promises if p.get("status") == "MISS" and p.get("thesis_bearing")]
    if len(thesis_miss) >= 2:
        red.append({"code": "repeat_miss", "text": "повторный MISS по обещаниям тезиса"})
    slips = [p for p in promises if p.get("status") in ("SLIP", "MISS")]
    if not red and slips:
        yellow.append({"code": "slip_or_miss", "text": "существенный SLIP или MISS"})
    if any(p.get("missing_from_update") for p in promises):
        yellow.append({"code": "promise_missing", "text": "ОБЕЩАНИЕ_ПРОПАЛО"})
    if any(isinstance(e, dict) and e.get("negative") for e in (case.get("material_events") or [])):
        red.append({"code": "material_event", "text": "негативное существенное событие"})
    tranche = metrics.get("tranche_checkpoint") if isinstance(metrics, dict) else None
    if isinstance(tranche, dict) and tranche.get("passed") and not (tranche.get("evidence") or "").strip():
        red.append({"code": "deviation", "text": "контрольная точка транша без доказательства"})
    for claim in accepted:
        if claim.get("conflict") and claim.get("thesis"):
            red.append({"code": "thesis_conflict", "text": "конфликт по ключевому тезису"})
        elif claim.get("conflict"):
            yellow.append({"code": "conflict", "text": "конфликт не по ключевому тезису"})

    if runway is not None:
        if runway < red_below:
            red.append({"code": "runway", "text": f"runway {runway} мес < {red_below}"})
        elif runway < yellow_below:
            yellow.append({"code": "runway", "text": f"runway {runway} мес < {yellow_below}"})

    last_update = metrics.get("last_update") if isinstance(metrics, dict) else None
    if last_update is None:
        yellow.append({"code": "stale_update", "text": "апдейт инвестору ни разу не был"})
    elif isinstance(last_update, date) and (as_of - last_update).days > UPDATE_STALE_DAYS:
        yellow.append({"code": "stale_update", "text": f"апдейт просрочен > {UPDATE_STALE_DAYS} дн."})
    cash_date = metrics.get("cash_date") if isinstance(metrics, dict) else None
    cash_stale = isinstance(cash_date, date) and (as_of - cash_date).days > CASH_STALE_DAYS

    if red:
        color = RED
    elif yellow or cash_stale:
        color = YELLOW
        if cash_stale and not any(d["code"] == "stale_cash" for d in yellow):
            yellow.append({"code": "stale_cash", "text": f"данные о деньгах старше {CASH_STALE_DAYS} дн."})
    elif runway is None:
        return {**base, "state": "UNEVALUATED", "color": None, "color_label": None, "runway_months": None,
                "drivers": yellow, "draft_actions": [], "human_gate": True, **_attach_hq042(case, company_id)}
    else:
        color = GREEN

    drivers = red + yellow if color == RED else yellow
    drafts = []
    if color != GREEN:
        drafts.append("Черновик запроса основателю — не отправлен")
    return {
        **base,
        "state": "OK",
        "color": color,
        "color_label": LABELS[color],
        "runway_months": format(runway.quantize(Decimal("0.1")), "f") if runway is not None else None,
        "drivers": drivers,
        "draft_actions": drafts,
        "human_gate": color == RED or bool(drafts),
        **_attach_hq042(case, company_id),
    }
