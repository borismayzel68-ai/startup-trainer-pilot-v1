"""
Node: decide + условное ребро route_after_decide.

Пишет: should_continue, stop_reason. Детерминированно, без модели.

Стоп (NORM-09), по порядку:
  1. founder: user_intent == "stop" (безусловно);
  2. max_turns: turn >= max_turns (из стейта, дефолт 12 в initial_state);
  3. agent_low_value: среди ещё не заданных пробелов нет missing/weak —
     критические пробелы закрыты или помечены, остались только hypothesis.
Иначе — continue.
"""

from __future__ import annotations

from typing import Any

from trainer.nodes.plan_next_question import candidate_gaps


def decide(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("user_intent") == "stop":
        return {"should_continue": False, "stop_reason": "founder"}
    if int(state.get("turn") or 0) >= int(state.get("max_turns") or 12):
        return {"should_continue": False, "stop_reason": "max_turns"}
    valuable = [g for g in candidate_gaps(state) if g["status"] in ("missing", "weak")]
    if not valuable:
        return {"should_continue": False, "stop_reason": "agent_low_value"}
    return {"should_continue": True, "stop_reason": ""}


def route_after_decide(state: dict[str, Any]) -> str:
    return "continue" if state.get("should_continue") else "stop"
