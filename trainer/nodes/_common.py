"""
Общие помощники узлов: доступ к KnowledgeBase и контексту профиля.
Не узел графа и не поле стейта.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from trainer.kb import KnowledgeBase

_DEFAULT_KB_DIR = Path(__file__).resolve().parents[2] / "knowledge"


@lru_cache(maxsize=1)
def get_kb() -> KnowledgeBase:
    return KnowledgeBase(os.environ.get("TRAINER_KNOWLEDGE_DIR", str(_DEFAULT_KB_DIR)))


def retrieve_snippets(query: str, k: int = 3) -> list[dict]:
    """
    retrieve() канона -> компактные выдержки для промпта.
    Источник historical/superseded отдаётся с явным предупреждением (§7.1).
    """
    out = []
    for card in get_kb().retrieve(query, k=k):
        warn = ""
        if card.status.lower() in {"historical", "superseded"}:
            warn = f"[ВНИМАНИЕ: источник {card.status}, не использовать как действующую норму] "
        out.append({
            "id": card.card_id,
            "title": card.title,
            "do_not_use_for": card.do_not_use_for,
            "text": warn + card.body[:1200],
        })
    return out


def profile_context(profile: dict) -> tuple[str, str, str]:
    """domain, stage, jurisdiction для канонических фильтров §7.3 — из профиля,
    без отдельных полей стейта (см. docstring trainer/state.py)."""
    def val(slot):
        return ((profile.get(slot) or {}).get("value") or "").strip()

    domain = "biotech medtech"
    stage = val("stage_phase")[:80] or "unknown"
    jurisdiction = val("regulatory_map")[:80] or "unknown"
    return domain, stage, jurisdiction


def materials_text(materials: list, types: set[str] | None = None, limit: int = 12000) -> str:
    parts = []
    for m in materials or []:
        if types and m.get("type") not in types:
            continue
        parts.append(f"### {m.get('name', '')} [{m.get('type', '')}]\n{m.get('text_excerpt', '')}")
    return "\n\n".join(parts)[:limit]
