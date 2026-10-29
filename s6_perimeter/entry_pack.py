"""HQ-032 · входной пакет до term sheet: список условий и пробелов.

Нет текста term sheet. Нет оценки и кратности — ни для pre-revenue, ни для остальных кейсов
(комментарий к #17, Д-41/Д-82). Нет данных по полю периметра → NEEDS_EVIDENCE. FACT только
с verified_by аналитика или владельца. Пакет никуда не отправляется.
"""
from __future__ import annotations

import json

from . import engine as E

FACT_ROLES = frozenset({"analyst", "owner"})
FORBIDDEN_REQUESTS = frozenset({"term_sheet", "valuation", "send", "multiplier"})
LEAK_MARKERS = ("10×", "10x", "900", "мультипликатор", "term sheet", "term_sheet")


def _facts(claims) -> tuple[list[dict], list[dict]]:
    accepted, rejected = [], []
    for claim in claims or []:
        if not isinstance(claim, dict):
            rejected.append({"reason": "не запись"})
            continue
        blob = json.dumps(claim, ensure_ascii=False).lower()
        if any(marker.lower() in blob for marker in LEAK_MARKERS):
            rejected.append({"reason": "вне периметра S6"})
            continue
        if claim.get("label") == "FACT":
            who = claim.get("verified_by")
            if not isinstance(who, str) or who.strip() not in FACT_ROLES:
                rejected.append({"reason": "FACT без verified_by аналитика или владельца"})
                continue
        accepted.append({k: claim[k] for k in ("label", "statement", "verified_by") if k in claim})
    return accepted, rejected


def build_entry_pack(case: dict) -> dict:
    """Условия и пробелы по полям периметра. Ключей оценки и term sheet в ответе нет."""
    if not isinstance(case, dict):
        case = {}
    if case.get("request") in FORBIDDEN_REQUESTS:
        return {
            "state": "STOP_FORBIDDEN",
            "conditions": [],
            "gaps": [{"field": "request", "status": "NEEDS_EVIDENCE", "reason": "запрос вне S6"}],
            "sent": False,
            "dispatched": False,
            "facts_accepted": [],
            "facts_rejected": [],
            "lawyer_required": True,
            "pre_revenue": case.get("revenue_status") == "pre-revenue",
        }
    fields = case.get("fields") if isinstance(case.get("fields"), dict) else {}
    evaluated = E.evaluate(fields, E.load_policy())
    conditions, gaps = [], []
    for name in E.FIELDS:
        problem = next((p for p in evaluated["problems"] if p["field"] == name), None)
        row = {"field": name, "lawyer_required": True}
        if problem:
            gaps.append({**row, "status": E.NEEDS_EVIDENCE, "reason": problem["reason"]})
        else:
            conditions.append({**row, "status": "CLOSED"})
    accepted, rejected = _facts(case.get("claims"))
    return {
        "state": evaluated["result"],
        "conditions": conditions,
        "gaps": gaps,
        "sent": False,
        "dispatched": False,
        "facts_accepted": accepted,
        "facts_rejected": rejected,
        "lawyer_required": True,
        "pre_revenue": case.get("revenue_status") == "pre-revenue",
    }
