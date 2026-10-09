"""
Node: extract_profile — факты из материалов в профиль; missing/weak.

Пишет: company_profile, unanswered_gaps.

Правила (NORM-01, NORM-02):
  - каждый слот профиля существует, по умолчанию status=missing;
  - потолок статуса задаёт ТИП материала, а не модель:
      deck/other -> weak (claim со слайда = unverified, хранится как weak),
      note (текст основателя) -> hypothesis,
      primary (протокол, договор, реестр, патент) -> fact;
  - цитата evidence_span обязана дословно встречаться в материале, иначе
    запись отбрасывается (нет цитаты — нет сигнала);
  - существующее значение заменяется только более сильным статусом.
"""

from __future__ import annotations

import json
import re
from typing import Any

from trainer.llm import call_json
from trainer.nodes._common import materials_text
from trainer.slots import (
    SLOTS, SLOT_IDS, STATUS_RANK, compute_gaps, empty_entry, normalize_status, span_in,
)

TYPE_CAP = {"deck": "weak", "other": "weak", "note": "hypothesis", "primary": "fact"}

EXTRACT_SYSTEM = """\
Ты извлекаешь из материалов стартапа значения слотов профиля. Только то,
что прямо написано в тексте. Ничего не додумывай (NORM-01): нет значения —
не включай слот в ответ.

Для каждого найденного слота верни дословную цитату evidence_span (точная
подстрока материала, 5–200 символов) и имя материала.

Статус: fact только если цитата из материала типа [primary]. Утверждение
из презентации [deck] без первички — weak (NORM-02). Иных статусов, кроме
fact | hypothesis | missing | weak, не существует.

Формат: {"slots": {"<slot_id>": {"value": str, "evidence_span": str,
"material": str, "status": str}}}
"""


def _cap(status: str, mtype: str) -> str:
    cap = TYPE_CAP.get(mtype, "weak")
    return status if STATUS_RANK[status] <= STATUS_RANK[cap] else cap


def _keyword_candidates(materials: list) -> dict[str, dict]:
    """Детерминированный фолбэк: первое предложение с ключевым словом слота.
    Даже из первички фолбэк не ставит fact — максимум hypothesis."""
    found: dict[str, dict] = {}
    for m in materials:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", m.get("text_excerpt", "")) if s.strip()]
        for slot_id in SLOT_IDS:
            if slot_id in found:
                continue
            for sent in sentences:
                low = sent.lower()
                if any(kw in low for kw in SLOTS[slot_id]["keywords"]):
                    span = sent[:200]
                    status = _cap("hypothesis", m["type"])
                    found[slot_id] = {
                        "value": span, "evidence_span": span, "status": status,
                        "source": f"material:{m['name']}",
                    }
                    break
    return found


def _llm_candidates(materials: list) -> dict[str, dict]:
    if not materials:
        return {}
    user = json.dumps({
        "slots": {sid: SLOTS[sid]["title"] for sid in SLOT_IDS},
        "materials": materials_text(materials),
    }, ensure_ascii=False)
    out = call_json(EXTRACT_SYSTEM, user) or {}
    by_name = {m["name"]: m for m in materials}
    found: dict[str, dict] = {}
    for slot_id, item in (out.get("slots") or {}).items():
        if slot_id not in SLOTS or not isinstance(item, dict):
            continue
        mat = by_name.get(str(item.get("material") or ""))
        span = str(item.get("evidence_span") or "")[:300]
        # дословность цитаты проверяет код; материал по имени или любой, где цитата есть
        if mat is None:
            mat = next((m for m in materials if span_in(span, m["text_excerpt"])), None)
        if mat is None or not span_in(span, mat["text_excerpt"]):
            continue
        status = _cap(normalize_status(item.get("status")), mat["type"])
        if status == "missing":
            continue
        found[slot_id] = {
            "value": str(item.get("value") or span)[:500],
            "evidence_span": span,
            "status": status,
            "source": f"material:{mat['name']}",
        }
    return found


def extract_profile(state: dict[str, Any]) -> dict[str, Any]:
    materials = state.get("materials") or []
    profile = {sid: dict(state.get("company_profile", {}).get(sid) or empty_entry()) for sid in SLOT_IDS}
    for entry in profile.values():
        entry["status"] = normalize_status(entry.get("status"))

    candidates = _llm_candidates(materials) or _keyword_candidates(materials)

    for slot_id, cand in candidates.items():
        current = profile[slot_id]
        if STATUS_RANK[cand["status"]] > STATUS_RANK[current["status"]]:
            profile[slot_id] = cand

    asked_ids = {q.get("id") for q in state.get("asked_questions") or []}
    return {
        "company_profile": profile,
        "unanswered_gaps": compute_gaps(profile, asked_ids),
    }
