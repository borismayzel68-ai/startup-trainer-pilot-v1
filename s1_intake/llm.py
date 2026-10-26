"""LLM-слой: один метод complete_json(), возвращающий провалидированную Pydantic-модель.

- OpenAI Responses API (MOS-7: base_url, ключ и модель — из env через config.Settings; единая точка
  замены LLM и для S1, и для тренажёра app.py). TG-1: модель S1 по умолчанию — фиксированный ID
  (config.LLM_MODEL_ID), смена только явной S1_LLM_MODEL_ID; temperature=0 у S1. На каждый вызов пишется
  запись в self.calls (deque, последние CALLS_MAXLEN): стадия, попытка, модель, temperature, sha256 промпта.
- Формат ответа (LLM_JSON_FORMAT): json_object (по умолчанию) | json_schema (только complete_json, где есть
  схема; для complete_dict остаётся json_object) | none (text.format не передаётся, JSON достаётся через
  _extract_json — для провайдеров без поддержки json_object).
- JSON-mode + валидация Pydantic + 1 повтор с текстом ошибки валидации.
- Mock-провайдер — детерминированные ответы для тестов и dry-run без ключа.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import deque
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from .config import LLM_TEMPERATURE, Settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


CALLS_MAXLEN = 50  # журнал вызовов ограничен: долгоживущий процесс app.py не копит записи без предела


class LLMError(Exception):
    pass


def check_prod_config(settings: Settings, app_env: str) -> None:
    """APP_ENV=staging|production с mock-провайдером: тренажёр падал бы на первом вызове — ошибка при старте."""
    if app_env.strip().lower() in ("staging", "production") and settings.llm_provider == "mock":
        raise LLMError("S1_LLM_PROVIDER=mock недопустим при APP_ENV=staging|production: "
                       "задайте S1_LLM_PROVIDER=openai (ключ OPENAI_API_KEY, при необходимости OPENAI_BASE_URL)")
    # Staging — сервер в РФ (MOS-6): LLM только через явный OPENAI_BASE_URL не на api.openai.com,
    # иначе при пустом секрете Lockbox данные тихо ушли бы в США.
    base = (settings.openai_base_url or "").strip().lower()
    if app_env.strip().lower() == "staging" and settings.llm_provider != "mock" and (
            not base or "api.openai.com" in base):
        raise LLMError("APP_ENV=staging: OPENAI_BASE_URL обязателен и не api.openai.com "
                       "(Yandex AI Studio: https://llm.api.cloud.yandex.net/v1)")


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
        self.model_name = settings.openai_model if self.provider == "openai" else "mock"
        self.calls: deque[dict] = deque(maxlen=CALLS_MAXLEN)
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

    def _text_format(self, schema: type[BaseModel] | None) -> dict | None:
        if self.s.llm_json_format == "none":
            return None
        if self.s.llm_json_format == "json_schema" and schema is not None:
            return {"format": {"type": "json_schema", "name": schema.__name__, "strict": False,
                               "schema": schema.model_json_schema()}}
        return {"format": {"type": "json_object"}}

    def _raw(self, stage: str, system: str, user: str, schema: type[BaseModel] | None = None,
             temperature: float | None = LLM_TEMPERATURE) -> str:
        if self.provider == "mock":
            if not self._mock:
                raise LLMError("mock provider without responder")
            return json.dumps(self._mock(stage, user), ensure_ascii=False)
        kwargs = {} if temperature is None else {"temperature": temperature}
        text_format = self._text_format(schema)
        if text_format is not None:
            kwargs["text"] = text_format
        resp = self._client.responses.create(
            model=self.s.openai_model,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {"role": "user", "content": [{"type": "input_text", "text": user}]},
            ],
            **kwargs,
        )
        return (resp.output_text or "").strip()

    def complete_dict(self, stage: str, system: str, user: str) -> dict:
        """Свободный JSON-объект без схемы (тренажёр app.py). Модель/base_url — те же, что у S1."""
        raw = self._raw(stage, system, user, temperature=None)
        self.calls.append({"stage": stage, "attempt": 1, "model": self.model_name})
        try:
            out = json.loads(_extract_json(raw))
        except json.JSONDecodeError as e:
            raise LLMError(f"stage {stage}: invalid JSON: {e}") from e
        if not isinstance(out, dict):
            raise LLMError(f"stage {stage}: JSON object expected")
        return out

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
            raw = self._raw(stage, system_full, msg, schema)
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
