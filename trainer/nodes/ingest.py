"""
Node: ingest — принять материалы и сообщение, нормализовать.

Пишет: materials.
Вход сессии: вызывающий код передаёт materials=[{name, type, text_excerpt, source}].
Ветка upload_note: текст founder_message добавляется как материал type="note".

S0.5-докстринг ссылался на поля _raw_input/messages/current_answer — их нет
в замороженном TrainerState; реализация следует trainer/state.py.
Не трогает turn (turn++ только в plan_next_question).
"""

from __future__ import annotations

from typing import Any

from trainer.slots import MATERIAL_TYPES

MAX_EXCERPT = 20000


def _normalize(m: dict, idx: int) -> dict:
    mtype = str(m.get("type") or "other").lower()
    return {
        "name": str(m.get("name") or f"material_{idx}")[:200],
        "type": mtype if mtype in MATERIAL_TYPES else "other",
        "text_excerpt": str(m.get("text_excerpt") or "")[:MAX_EXCERPT],
        "source": str(m.get("source") or "upload")[:200],
    }


def ingest(state: dict[str, Any]) -> dict[str, Any]:
    materials = [_normalize(m, i) for i, m in enumerate(state.get("materials") or [], 1)]

    if state.get("user_intent") == "upload_note":
        text = (state.get("founder_message") or "").strip()
        if text:
            materials.append(_normalize({
                "name": f"note_turn_{state.get('turn', 0)}",
                "type": "note",
                "text_excerpt": text,
                "source": "founder_message",
            }, len(materials) + 1))

    return {"materials": materials}
