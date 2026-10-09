"""
Тонкий LLM-адаптер для узлов (S1, инкремент 1).

Принцип: модель — советчик, а не арбитр. Каждый узел имеет
детерминированный фолбэк и сам проверяет выход модели (статус из
четырёх, дословность цитаты, fact только из первички). Поэтому:
  - нет OPENAI_API_KEY или TRAINER_LLM=off  -> call_json возвращает None,
    граф проходит цикл на правилах;
  - модель вернула мусор / упала            -> None, тот же фолбэк;
  - в тестах модель подменяется set_llm(fake).

Ключи только из окружения (.env), в код и логи не попадают.
Имя модели — параметр окружения OPENAI_MODEL.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable, Optional

log = logging.getLogger("trainer.llm")

LLMFn = Callable[[str, str], Optional[dict]]

_override: Optional[LLMFn] = None
_client = None


def set_llm(fn: Optional[LLMFn]) -> None:
    """Подменить модель (тесты, другой провайдер). None — вернуть поведение по окружению."""
    global _override, _client
    _override = fn
    _client = None


def _openai_client():
    global _client
    if _client is None:
        from langchain_openai import ChatOpenAI  # импорт только при наличии ключа

        _client = ChatOpenAI(
            model=os.environ.get("OPENAI_MODEL", "gpt-5.6"),
            timeout=float(os.environ.get("TRAINER_LLM_TIMEOUT", "60")),
            max_retries=1,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
    return _client


def llm_enabled() -> bool:
    if _override is not None:
        return True
    if os.environ.get("TRAINER_LLM", "on").lower() == "off":
        return False
    return bool(os.environ.get("OPENAI_API_KEY"))


def _parse_json(text: str) -> Optional[dict]:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def call_json(system: str, user: str) -> Optional[dict]:
    """Вызов модели с ответом-JSON. Любая ошибка -> None (узел уходит в фолбэк)."""
    if _override is not None:
        try:
            out = _override(system, user)
        except Exception:  # noqa: BLE001 — тестовая подмена не должна ронять граф
            log.exception("llm override failed")
            return None
        return out if isinstance(out, dict) else None
    if not llm_enabled():
        return None
    try:
        msg = _openai_client().invoke(
            [("system", system + "\n\nОтвечай только JSON-объектом."), ("user", user)]
        )
        return _parse_json(getattr(msg, "content", "") or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("llm call failed: %s", type(exc).__name__)
        return None
