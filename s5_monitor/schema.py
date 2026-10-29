"""Форма чтения среза HQ-030 (#15). S5 таблиц не создаёт и второй Promise Ledger не заводит.

Хранилище обещаний — одно, его ведёт S4 (HQ-030). Пока миграции HQ-030 в репозитории нет,
мониторинг считает светофор по объекту этой формы в памяти. Поля обещания — из спецификации
S5 (БП-03 §8): promise_id, text, promised_on, owner, due, status, evidence, last_reviewed.
"""
from __future__ import annotations

# Имена полей зафиксированы здесь, чтобы тест схемы мог сверить их с отсутствием SQL-таблицы.
PROMISE_FIELDS = (
    "promise_id",
    "text",
    "promised_on",
    "owner",
    "due",
    "status",
    "evidence",
    "last_reviewed",
)

SNAPSHOT_FIELDS = (
    "company_id",
    "synthetic",
    "vertical",
    "as_of",
    "thesis",
    "promises",
    "metrics",
    "thresholds",
    "claims",
    "new_round",
    "entry_passport",
    "update",
)

PROMISE_STATUSES = frozenset({"HIT", "SLIP", "MISS", "DROPPED", "OPEN"})
FACT_ROLES = frozenset({"analyst", "owner"})
