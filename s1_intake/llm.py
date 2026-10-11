"""LLM-слой: один метод complete_json(), возвращающий провалидированную Pydantic-модель.

- OpenAI Responses API. TG-1: модель — фиксированный ID (config.LLM_MODEL_ID), temperature=0,
  на каждый вызов пишется запись в self.calls: стадия, попытка, модель, temperature, sha256 промпта.
- JSON-mode + валидация Pydantic + 1 повтор с текстом ошибки валидации.
- Mock-провайдер — детерминированные ответы для тестов и dry-run без ключа.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from .config import LLM_MODEL_ID, LLM_TEMPERATURE, Settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


def _extract_json(txt: str) -> str:
    """Как call_json в app.py: если модель обернула JSON текстом — берём объект."""
    txt = (txt or "").strip()
    if txt.startswith("{"):
        return txt
    m = re.search(r"\{[\s\S]*\}", txt)
    return m.group(0) if m else txt


class LLMClient:
    def __init__(self, settings: Settings, mock_responder: Callable[[str, str], dict] | None = None):
        self.s = settings
        self.provider = settings.llm_provider
        self.model_name = LLM_MODEL_ID if self.provider == "openai" else "mock"
        self.calls: list[dict] = []
        self._mock = mock_responder
        self._client = None
        if self.provider == "openai":
            from openai import OpenAI

            kwargs = {"api_key": settings.openai_api_key, "timeout": settings.llm_timeout_s, "max_retries": 3}
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = OpenAI(**kwargs)
        elif self.provider != "mock":
            raise LLMError(f"Unknown LLM_PROVIDER={self.provider}")

    def _raw(self, stage: str, system: str, user: str) -> str:
        if self.provider == "mock":
            if not self._mock:
                raise LLMError("mock provider without responder")
            return json.dumps(self._mock(stage, user), ensure_ascii=False)
        resp = self._client.responses.create(
            model=LLM_MODEL_ID,
            temperature=LLM_TEMPERATURE,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {"role": "user", "content": [{"type": "input_text", "text": user}]},
            ],
            text={"format": {"type": "json_object"}},
        )
        return (resp.output_text or "").strip()

    def complete_json(self, stage: str, system: str, user: str, schema: type[T]) -> T:
        schema_hint = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        system_full = (
            f"{system}\n\nОтвечай ТОЛЬКО одним JSON-объектом, строго по этой JSON Schema:\n{schema_hint}"
        )
        last_err = None
        msg = user
        for attempt in (1, 2):
            t0 = time.monotonic()
            rec = {"stage": stage, "attempt": attempt, "model": self.model_name,
                   "temperature": LLM_TEMPERATURE,
                   "system_prompt_sha256": hashlib.sha256(system_full.encode()).hexdigest(),
                   "prompt_sha256": hashlib.sha256(f"{system_full}\n\n{msg}".encode()).hexdigest()}
            self.calls.append(rec)
            raw = self._raw(stage, system_full, msg)
            log.info("llm stage=%s attempt=%d model=%s prompt_sha256=%s %.1fs", stage, attempt,
                     self.model_name, rec["prompt_sha256"][:16], time.monotonic() - t0)
            try:
                return schema.model_validate(json.loads(_extract_json(raw)))
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = e
                msg = (
                    f"{user}\n\n---\nТвой предыдущий ответ не прошёл валидацию:\n{str(e)[:2000]}\n"
                    "Исправь и верни корректный JSON целиком."
                )
        raise LLMError(f"stage {stage}: invalid JSON after retry: {last_err}")
