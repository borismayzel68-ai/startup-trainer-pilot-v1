"""Одноразовый захват шлюза S6 — общий для POST /s2/checks и forward_to_s2.
Захват = условная запись (compare-and-set в хранилище, ReadinessJobStore.update_data_if): «занять» удаётся
ровно одному вызову, остальные видят занятый шлюз. Занятый шлюз освобождается, если запись S2 создать не удалось."""
from __future__ import annotations

USED_MSG = "шлюз S6 уже использован: "


def _s6(data: dict) -> dict:
    return data.get("s6") or {}


def used_by(s6: dict) -> str | None:
    """id записи S2, либо метка «в работе» (захвачен, запись ещё создаётся); None — шлюз свободен."""
    return s6.get("s2_check_id") or ("в работе" if s6.get("s2_claim") else None)


def claim(store, gate_id: str, token: str) -> bool:
    """Занять шлюз одной атомарной операцией. False — уже занят или использован."""
    def mutate(data):
        s6 = _s6(data)
        return None if not s6 or used_by(s6) else {**data, "s6": {**s6, "s2_claim": token}}
    return store.update_data_if(gate_id, mutate)


def settle(store, gate_id: str, token: str, check_id: str) -> bool:
    """Запись S2 создана: захват → ссылка s2_check_id (только если захват наш)."""
    def mutate(data):
        s6 = _s6(data)
        if s6.get("s2_claim") != token:
            return None
        return {**data, "s6": {**{k: v for k, v in s6.items() if k != "s2_claim"}, "s2_check_id": check_id}}
    return store.update_data_if(gate_id, mutate)


def abandon(store, gate_id: str, token: str) -> bool:
    """Откат захвата, если запись S2 не создана. Чужой захват и уже поставленную ссылку не трогает."""
    def mutate(data):
        s6 = _s6(data)
        if s6.get("s2_claim") != token:
            return None
        return {**data, "s6": {k: v for k, v in s6.items() if k != "s2_claim"}}
    return store.update_data_if(gate_id, mutate)


def set_release(store, gate_id: str, release: dict) -> bool:
    """Снятие блокировки owner: пишет только release (и только если его ещё нет); ключи s2_check_id / s2_claim,
    поставленные параллельно, не затирает."""
    def mutate(data):
        s6 = _s6(data)
        return None if not s6 or s6.get("release") else {**data, "s6": {**s6, "release": release}}
    return store.update_data_if(gate_id, mutate)
