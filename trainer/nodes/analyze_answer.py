"""
Node: analyze_answer — полнота, доказательства, противоречия, риск.

Пишет: last_analysis, company_profile, qa_log (новая запись {q, a, analysis, coach}).

Модель — советчик, потолок статуса задаёт код:
  - «нет данных» / «не знаю»                 -> missing (NORM-01);
  - устный ответ без первички                -> максимум hypothesis (NORM-02);
  - fact                                      -> только если evidence_span
                                                 дословно есть в материале type=primary;
  - цитата не найдена дословно                -> цитата = первое предложение ответа;
  - противоречие с имеющимся значением        -> оба значения сохраняются в
                                                 last_analysis.contradiction, поле -> weak
                                                 (запрет молча перезаписывать).
Выдуманные числа не появляются: значение = текст ответа, не пересказ модели.
"""

from __future__ import annotations

import json
from typing import Any

from trainer.llm import call_json
from trainer.nodes._common import retrieve_snippets
from trainer.prompts import ANALYZE_ANSWER
from trainer.slots import (
    STATUS_RANK, empty_entry, first_sentence, is_no_data, normalize_status, numbers, span_in,
)


def _baseline(answer: str) -> tuple[str, str | None]:
    if not answer.strip() or is_no_data(answer):
        return "missing", None
    span = first_sentence(answer)
    concrete = bool(numbers(answer)) or len(answer.strip()) >= 60
    return ("hypothesis" if concrete else "weak"), span


def _cap(status: str, span: str | None, answer: str, primary_texts: list[str]) -> str:
    if status == "fact" and not span_in(span, *primary_texts):
        status = "hypothesis"
    if status != "missing" and not span_in(span, answer, *primary_texts):
        status = "weak"  # нет цитаты — не подтверждено
    return status


def _contradiction(field_id: str, previous: dict, answer: str, turn: int) -> dict | None:
    """Детерминированно: у старого и нового значения есть числа, и они не пересекаются."""
    old_val = previous.get("value") or ""
    old_nums, new_nums = numbers(old_val), numbers(answer)
    if previous.get("status") == "missing" or not old_nums or not new_nums or old_nums & new_nums:
        return None
    return {
        "field_id": field_id,
        "previous": {"value": old_val, "source": previous.get("source")},
        "new": {"value": answer[:500], "source": f"answer:turn_{turn}"},
        "rule": "числа в ответе не совпадают с ранее зафиксированным значением",
    }


def analyze_answer(state: dict[str, Any]) -> dict[str, Any]:
    question = state.get("current_question") or {}
    field_id = question.get("id")
    answer = (state.get("founder_message") or "").strip()
    turn = int(state.get("turn") or 0)
    profile = {k: dict(v) for k, v in (state.get("company_profile") or {}).items()}
    previous = profile.get(field_id) or empty_entry()
    primary_texts = [m["text_excerpt"] for m in state.get("materials") or [] if m.get("type") == "primary"]

    status, span = _baseline(answer)
    risk_note = None
    contradiction = _contradiction(field_id, previous, answer, turn) if status != "missing" else None

    if status != "missing":
        out = call_json(ANALYZE_ANSWER, json.dumps({
            "current_question": question,
            "answer": answer,
            "profile_value": previous,
            "knowledge": retrieve_snippets(f"evidence_standard study_design {question.get('topic', '')}", k=3),
        }, ensure_ascii=False))
        if out:
            llm_span = str(out.get("evidence_span") or "")[:300] or None
            if span_in(llm_span, answer, *primary_texts):
                span = llm_span
            status = normalize_status(out.get("status"))
            risk_note = (str(out["risk_note"])[:400] if out.get("risk_note") else None)
            if contradiction is None and isinstance(out.get("contradiction"), dict):
                contradiction = {"field_id": field_id, **out["contradiction"],
                                 "previous": {"value": previous.get("value"), "source": previous.get("source")}}
        status = _cap(status, span, answer, primary_texts)

    if status == "missing":
        span = None

    analysis = {
        "field_id": field_id,
        "status": status,
        "evidence_span": span,
        "contradiction": contradiction,
        "risk_note": risk_note,
    }

    new_entry = {
        "value": answer[:500] or None,
        "evidence_span": span,
        "status": status,
        "source": f"answer:turn_{turn}",
    }
    if contradiction:
        new_entry["status"] = "weak"
        profile[field_id] = new_entry
    elif STATUS_RANK[status] >= STATUS_RANK[normalize_status(previous.get("status"))]:
        profile[field_id] = new_entry
    # иначе более сильное прежнее значение (например fact из первички) сохраняется

    qa_log = list(state.get("qa_log") or [])
    qa_log.append({
        "q": {"id": field_id, "text": question.get("text"), "turn": turn},
        "a": answer,
        "analysis": analysis,
        "coach": None,
    })
    return {"last_analysis": analysis, "company_profile": profile, "qa_log": qa_log}
