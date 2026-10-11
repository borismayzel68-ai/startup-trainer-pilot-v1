"""Tilda webhook payload -> Submission.

Tilda шлёт POST (обычно application/x-www-form-urlencoded, бывает JSON),
поля называются по «Имени переменной» из настроек формы.
- При подключении вебхука Tilda отправляет тестовый запрос test=test и ждёт 200.
- tranid — уникальный ID заявки, используем для идемпотентности (Tilda может
  повторить доставку при таймауте).
- Поле «Загрузка файла» приходит строкой со ссылкой(ами) на хранилище Tilda /
  Uploadcare. Несколько файлов — несколько ссылок в одной строке.
"""
from __future__ import annotations

import hashlib
import json
import re

from routing_v2 import normalize_project_id

from .models import Answer, FileRef, Submission

URL_RE = re.compile(r"https?://[^\s,;\"'<>]+", re.IGNORECASE)


def is_test_ping(payload: dict) -> bool:
    return str(payload.get("test", "")).lower() == "test"


def _as_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "\n".join(_as_str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def extract_urls(value) -> list[str]:
    return list(dict.fromkeys(URL_RE.findall(_as_str(value))))  # uniq, order kept


def _truthy(v) -> bool:
    return _as_str(v).lower() in {"yes", "on", "true", "1", "да", "согласен", "y"}


def parse_payload(payload: dict, methodology: dict) -> Submission:
    form = methodology["form"]
    cf, ff = form["contact_fields"], form["file_fields"]

    answers = []
    for q in form["questions"]:
        val = _as_str(payload.get(q["name"]))
        if val:
            answers.append(Answer(field=q["name"], block=q["block"], label=q["label"], value=val[:5000]))

    files: list[FileRef] = []
    for kind, fname in ff.items():
        for url in extract_urls(payload.get(fname)):
            files.append(FileRef(kind=kind, url=url))

    sub_id = _as_str(payload.get("tranid"))
    if not sub_id:
        # Без tranid (ручной тест, другой источник) — стабильный хэш содержимого.
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        sub_id = "h_" + hashlib.sha256(canon.encode()).hexdigest()[:24]

    rf = form["routing_fields"]
    project_id = normalize_project_id(_as_str(payload.get(cf["project_id"]))) if cf.get("project_id") else None
    return Submission(
        submission_id=sub_id,
        project_id=project_id,
        project_name=_as_str(payload.get(cf["project_name"]))[:160],
        founder_name=_as_str(payload.get(cf["founder_name"]))[:160],
        founder_email=_as_str(payload.get(cf["founder_email"]))[:320],
        consent=_truthy(payload.get(cf["consent"])),
        jurisdiction=_as_str(payload.get(rf["jurisdiction"]))[:40],
        target_markets=_as_str(payload.get(rf["target_markets"]))[:200],
        product_class=_as_str(payload.get(rf["product_class"]))[:60],
        intended_use=_as_str(payload.get(rf["intended_use"]))[:4000],
        ls_stage=_as_str(payload.get(rf["ls_stage"])).lower()[:40] if rf.get("ls_stage") else "",
        answers=answers,
        files=files,
    )
