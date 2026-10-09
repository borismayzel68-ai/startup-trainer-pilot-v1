"""
Node: ask — короткий вопрос по-русски основателю.

Пишет: current_question (добавляет ключ display — готовый текст для
показа), last_coach (только в ветке ask_help).

После ask граф ОБЯЗАН ждать человека (решение штаба 2026-08-23 п.4):
это обеспечивает compile(interrupt_after=["ask"]) в trainer/runtime.py,
ребро ask -> route_intent в graph.py не меняется.

ask_help: coach вызывается как помощник (не маршрут), подсказка ставится
перед тем же вопросом. turn не меняется.
"""

from __future__ import annotations

from typing import Any

from trainer.nodes.coach import build_coach

NO_QUESTION = "Вопросов с ценностью для пакета больше нет. Напишите «готово», чтобы получить отчёт."


def ask(state: dict[str, Any]) -> dict[str, Any]:
    question = dict(state.get("current_question") or {})
    if not question.get("text"):
        question["display"] = NO_QUESTION
        return {"current_question": question}

    patch: dict[str, Any] = {}
    display = question["text"]
    if state.get("user_intent") == "ask_help":
        advice = build_coach(state, question.get("id"))
        if advice:
            display = (
                f"Подсказка: чтобы ответ засчитался, опирайтесь на «{advice['artifact']}». "
                f"Если документа нет — так и напишите «нет данных», это тоже ответ.\n\n{display}"
            )
            patch["last_coach"] = advice
    question["display"] = display
    patch["current_question"] = question
    return patch
