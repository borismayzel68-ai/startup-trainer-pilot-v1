"""S6 · шлюз «Периметр входа»: чистая логика. Правила — из perimeter_policy.yaml, не из кода.
Результат: PASS / NEEDS_EVIDENCE. Нет данных, нет доказательства или значение вне политики → NEEDS_EVIDENCE.
Шлюз сам себя не снимает: снятие блокировки — только решение человека-owner (blueprint), ИИ его не принимает."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import yaml

PASS, NEEDS_EVIDENCE = "PASS", "NEEDS_EVIDENCE"
PKG = Path(__file__).resolve().parent
FIELDS = ("legal_entity", "deal_form", "check_rub", "covenants", "tranches")


class GateBlocked(PermissionError):
    """Заявка не допущена в S2: периметр не пройден и owner блокировку не снимал."""

    def __init__(self, gate_id: str, problems: list[dict]):
        super().__init__(f"периметр входа не пройден ({gate_id}): " + ", ".join(p["field"] for p in problems))
        self.problems = problems


def load_policy(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("S6_POLICY_PATH") or PKG / "perimeter_policy.yaml")
    with open(p, encoding="utf-8") as f:
        policy = yaml.safe_load(f)
    for k in FIELDS:
        if k not in policy:
            raise ValueError("perimeter_policy: нет раздела " + k)
    return policy


def _num(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and v > 0


def _value_problem(name: str, value, policy: dict) -> str | None:
    """None, если значение допустимо; иначе причина. Предполагается, что value не пустое."""
    cfg = policy[name]
    if name == "legal_entity":
        ok = (isinstance(value, dict) and value.get("form") in cfg["allowed_forms"]
              and isinstance(value.get("name"), str) and bool(value["name"].strip()))
        return None if ok else "юрлицо: нужны name и form из списка"
    if name == "deal_form":
        return None if isinstance(value, str) and value in cfg["allowed"] else "форма сделки вне списка"
    if name == "check_rub":
        ok = _num(value) and cfg["min"] <= value <= cfg["max"]
        return None if ok else f"чек вне диапазона {cfg['min']}–{cfg['max']} ₽"
    if name == "covenants":
        ok = isinstance(value, list) and len(value) >= cfg["min_count"] and all(
            isinstance(c, dict) and c.get("type") in cfg["allowed_types"] for c in value)
        return None if ok else "ковенанты: не меньше min_count, тип из списка"
    if name == "tranches":
        ok = isinstance(value, list) and cfg["min_count"] <= len(value) <= cfg["max_count"] and all(
            isinstance(t, dict) and _num(t.get("amount_rub")) for t in value)
        return None if ok else "транши: число или суммы вне политики"
    return "неизвестное поле"


def evaluate(app: dict, policy: dict) -> dict:
    """app: {поле: {value, evidence}}, evidence — ссылка на документ. Любая проблема по любому полю → NEEDS_EVIDENCE."""
    problems = []
    for name in FIELDS:
        item = app.get(name)
        value = item.get("value") if isinstance(item, dict) else None
        evidence = item.get("evidence") if isinstance(item, dict) else None
        if value is None or value == "" or value == [] or value == {}:
            problems.append({"field": name, "reason": "нет данных"})
        elif policy.get("evidence_required", True) and not (isinstance(evidence, str) and evidence.strip()):
            problems.append({"field": name, "reason": "нет доказательства"})
        else:
            why = _value_problem(name, value, policy)
            if why:
                problems.append({"field": name, "reason": why})
    if not problems and policy["tranches"].get("sum_equals_check"):
        total = sum(t["amount_rub"] for t in app["tranches"]["value"])
        if not math.isclose(total, app["check_rub"]["value"], abs_tol=0.005):
            problems.append({"field": "tranches", "reason": "сумма траншей не равна чеку"})
    return {"result": NEEDS_EVIDENCE if problems else PASS, "problems": problems,
            "policy_status": policy.get("status", "")}


def sha256(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def admit(gate_id: str, rec: dict, release: dict | None, policy: dict | None = None, *,
          human_decision: dict | None = None) -> None:
    """Единственная проверка допуска в S2. Результат пересчитывается из входных данных (подмена поля result в
    хранилище допуск не даёт). PASS проходит. NEEDS_EVIDENCE проходит только если human_decision — явное
    решение человека из аудит-лога на ТЕ ЖЕ данные (source=audit_log, sha256, именной актор, не admin).
    Поле release в записи шлюза само по себе допуск не даёт. Иначе GateBlocked."""
    fresh = evaluate(rec["input"], policy or load_policy())
    if fresh["result"] == PASS:
        return
    digest = sha256(rec)
    by = human_decision.get("by") if isinstance(human_decision, dict) else None
    person = isinstance(by, str) and bool(by.strip()) and by.strip().lower() not in {"admin", "system", "token"}
    if (person and human_decision.get("source") == "audit_log" and human_decision.get("sha256") == digest):
        return
    # release намеренно не читается: подмена JSON в хранилище не является решением человека.
    del release
    raise GateBlocked(gate_id, fresh["problems"])
