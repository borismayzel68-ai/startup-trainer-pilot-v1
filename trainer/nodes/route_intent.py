"""
Node: route_intent + условное ребро route_after_intent.

Пишет: user_intent; при stop — также should_continue=False, stop_reason="founder".

Классификация детерминированная (без модели): правило NORM-09 «founder
stop безусловен» не должно зависеть от того, как модель поняла фразу.

Маршруты (ключи совпадают с картой рёбер в graph.py):
  answer      -> analyze_answer
  stop        -> summarize
  upload_note -> ingest          (так в graph.py; S0.5-докстринг ошибочно
                                  писал extract_profile — канон рёбер в graph.py)
  ask_help    -> ask             (напрямую, coach — помощник внутри ask)
"""

from __future__ import annotations

import re
from typing import Any

INTENTS = ("answer", "stop", "upload_note", "ask_help")

_STOP_RE = re.compile(
    r"^\s*(хватит|готово|заканчиваем|закончим|стоп|stop|всё|все|достаточно|завершить|заверш(аем|ить) сессию)\s*[.!]*\s*$",
    re.IGNORECASE,
)
_HELP_RE = re.compile(
    r"(не понимаю|не понял[аи]?|поясните|объясните|что (вы )?имеете в виду|что значит|"
    r"в каком смысле|приведите пример|помогите|подскажите)",
    re.IGNORECASE,
)
_UPLOAD_RE = re.compile(r"^\s*(загружаю|прикладываю|добавляю (документ|материал)|вот (документ|файл|выдержка))",
                        re.IGNORECASE)
UPLOAD_MIN_CHARS = 1500  # длинная вставка текста документа = материал, а не ответ


def classify(message: str) -> str:
    text = (message or "").strip()
    if not text or _STOP_RE.match(text):
        return "stop" if text else "ask_help"
    if _UPLOAD_RE.match(text) or len(text) >= UPLOAD_MIN_CHARS:
        return "upload_note"
    if len(text) < 200 and _HELP_RE.search(text):
        return "ask_help"
    return "answer"


def route_intent(state: dict[str, Any]) -> dict[str, Any]:
    intent = classify(state.get("founder_message") or "")
    patch: dict[str, Any] = {"user_intent": intent}
    if intent == "stop":
        patch.update(should_continue=False, stop_reason="founder")
    return patch


def route_after_intent(state: dict[str, Any]) -> str:
    intent = state.get("user_intent")
    return intent if intent in INTENTS else "answer"
