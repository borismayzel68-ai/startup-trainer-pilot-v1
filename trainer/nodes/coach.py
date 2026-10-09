"""
Node: coach — какой факт/документ принести (не «как красивее сказать»).

Пишет: last_coach, qa_log (поле coach в последней записи).

Восстановлен в S1 по контракту graph.py (файл S0.5 повреждён при переносе).
Также вызывается как помощник из ask при user_intent=ask_help — НЕ как
маршрут графа (решение штаба 2026-08-23).

Охранные правила (NORM-04, NORM-08): выход модели отбрасывается, если в нём
нет конкретного артефакта или есть генерация питча/слайда; тогда
используется артефакт слота из каталога.
"""

from __future__ import annotations

import json
import re
from typing import Any

from trainer.llm import call_json
from trainer.nodes._common import profile_context, retrieve_snippets
from trainer.prompts import COACH
from trainer.slots import SLOTS

_FORBIDDEN = re.compile(r"слайд|питч|pitch|one-?pager|презентаци|убедительн", re.IGNORECASE)


def build_coach(state: dict[str, Any], field_id: str | None = None) -> dict:
    """Чистая функция: рекомендация по артефакту для field_id (или текущего вопроса)."""
    field_id = field_id or (state.get("current_question") or {}).get("id")
    if field_id not in SLOTS:
        return {}
    slot = SLOTS[field_id]
    fallback = {"artifact": slot["artifact"], "why": slot["why"], "field_id": field_id}

    _, _, jurisdiction = profile_context(state.get("company_profile") or {})
    analysis = state.get("last_analysis") or {}
    user = json.dumps({
        "field_id": field_id,
        "topic": slot["title"],
        "question": (state.get("current_question") or {}).get("text"),
        "analysis": {k: analysis.get(k) for k in ("status", "evidence_span", "contradiction", "risk_note")},
        "knowledge": retrieve_snippets(f"next_artifact {jurisdiction}", k=3),
    }, ensure_ascii=False)
    out = call_json(COACH, user)
    if not out:
        return fallback
    artifact = str(out.get("artifact") or "").strip()
    why = str(out.get("why") or "").strip()
    if len(artifact) < 10 or _FORBIDDEN.search(artifact + " " + why):
        return fallback
    return {"artifact": artifact[:400], "why": (why or slot["why"])[:400], "field_id": field_id}


def coach(state: dict[str, Any]) -> dict[str, Any]:
    field_id = (state.get("last_analysis") or {}).get("field_id")
    advice = build_coach(state, field_id)
    qa_log = [dict(e) for e in state.get("qa_log") or []]
    if qa_log:
        qa_log[-1]["coach"] = advice
    return {"last_coach": advice, "qa_log": qa_log}
