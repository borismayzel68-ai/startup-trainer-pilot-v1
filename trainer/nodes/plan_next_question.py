"""
Node: plan_next_question — один вопрос максимальной ценности; retrieve канона.

Пишет: current_question, asked_questions, turn.

ЕДИНСТВЕННЫЙ узел, который увеличивает turn (решение штаба 2026-08-23 п.3):
  turn += 1 только когда выставлен новый current_question.
  ask_help -> ask сюда не попадает и turn не тратит.

Кандидаты: unanswered_gaps без уже заданных field_id (NORM-03, PROMPT:
не спрашивать закрытый field_id), кроме поля, переоткрытого противоречием
в last_analysis. Модель может переформулировать вопрос, но выбрать может
только из кандидатов; иначе — шаблон слота.
"""

from __future__ import annotations

import json
from typing import Any

from trainer.llm import call_json
from trainer.nodes._common import profile_context, retrieve_snippets
from trainer.prompts import PLAN_NEXT_QUESTION
from trainer.slots import SLOTS, compute_gaps

SEVERITIES = {"low", "medium", "high"}


def candidate_gaps(state: dict[str, Any]) -> list[dict]:
    asked = state.get("asked_questions") or []
    asked_ids = {q.get("id") for q in asked}
    reopened = None
    last = state.get("last_analysis") or {}
    if last.get("contradiction"):
        fid = last.get("field_id")
        # Противоречие переоткрывает поле один раз, не бесконечно.
        if sum(1 for q in asked if q.get("id") == fid) < 2:
            reopened = fid
    gaps = state.get("unanswered_gaps") or compute_gaps(state.get("company_profile") or {}, asked_ids)
    out = [g for g in gaps if g["field_id"] not in asked_ids or g["field_id"] == reopened]
    # Снять противоречие важнее следующего пробела (PLAN: «или снимает противоречие»).
    out.sort(key=lambda g: g["field_id"] != reopened)
    return out


def _template_question(gap: dict) -> dict:
    slot = SLOTS[gap["field_id"]]
    severity = slot["severity"] if gap["status"] != "hypothesis" else "low"
    return {
        "id": gap["field_id"],
        "text": slot["question"],
        "why": slot["why"],
        "topic": slot["title"],
        "severity": severity,
    }


def _llm_question(state: dict[str, Any], candidates: list[dict]) -> dict | None:
    domain, stage, _ = profile_context(state.get("company_profile") or {})
    cards = retrieve_snippets(f"questions {domain} {stage}", k=3)
    profile = state.get("company_profile") or {}
    user = json.dumps({
        "candidates": [
            {"field_id": g["field_id"], "status": g["status"], "topic": SLOTS[g["field_id"]]["title"],
             "current_value": (profile.get(g["field_id"]) or {}).get("value")}
            for g in candidates[:3]
        ],
        "contradiction": (state.get("last_analysis") or {}).get("contradiction"),
        "knowledge": cards,
        "rule": "id обязан быть одним из candidates.field_id; вопрос один, по-русски, коротко.",
    }, ensure_ascii=False)
    out = call_json(PLAN_NEXT_QUESTION, user)
    if not out:
        return None
    allowed = {g["field_id"] for g in candidates[:3]}
    qid = str(out.get("id") or "")
    text = str(out.get("text") or "").strip()
    if qid not in allowed or not text or text.count("?") > 1:
        return None
    sev = str(out.get("severity") or "").lower()
    return {
        "id": qid,
        "text": text[:400],
        "why": str(out.get("why") or SLOTS[qid]["why"])[:400],
        "topic": SLOTS[qid]["title"],
        "severity": sev if sev in SEVERITIES else SLOTS[qid]["severity"],
    }


def plan_next_question(state: dict[str, Any]) -> dict[str, Any]:
    candidates = candidate_gaps(state)
    if not candidates:
        # Новых вопросов нет — turn не тратим; decide/ask закроют сессию.
        return {"current_question": {}}

    question = _llm_question(state, candidates) or _template_question(candidates[0])
    turn = int(state.get("turn") or 0) + 1
    asked = list(state.get("asked_questions") or [])
    asked.append({"id": question["id"], "text": question["text"], "turn": turn})
    return {"current_question": question, "asked_questions": asked, "turn": turn}
