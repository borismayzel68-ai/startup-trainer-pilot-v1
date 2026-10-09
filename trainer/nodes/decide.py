"""
trainer/nodes/decide.py

Node: decide  +  conditional-edge function: route_after_decide

Evaluates whether the session should continue (ask another question)
or terminate (produce summary).

TrainerState field names (frozen contract):
  state["company_profile"]   — startup profile with field statuses
                               (was: profile)
  state["user_intent"]       — current intent; "stop" triggers termination
                               (was: intent)

Termination conditions (any one sufficient):
  - state["user_intent"] == "stop"  (founder requested end)
  - All company_profile fields have status fact or hypothesis (coverage complete)
  - turn >= max_turns threshold (defined in graph.py or config, not here)
  - readiness_scores indicate sufficient or terminal low confidence

route_after_decide() is the LangGraph conditional edge:
  continue → "plan_next_question"
  stop     → "summarize"          ← literal return value is "stop"
                                    (was incorrectly documented as "end")

Constraints:
  - do NOT increment state["turn"]
  - do NOT add new fields to state
  - do NOT hardcode max_turns (read from state or graph config)
  - terminal branch returns the string "stop", not "end"
"""

from __future__ import annotations

from typing import Any


def decide(state: dict[str, Any]) -> dict[str, Any]:
    """
    Evaluate session continuation vs termination.

    Writes an internal decision signal consumed by route_after_decide.
    Implementation may use a "_decision" key on state or derive purely
    from existing fields — do NOT add permanent new state fields.

    Returns partial state patch (minimal; routing logic in route_after_decide).
    """
    raise NotImplementedError


def route_after_decide(state: dict[str, Any]) -> str:
    """
    LangGraph conditional edge function.

    Reads decide signal from state and returns next node name:
      session continues → "plan_next_question"
      session ends      → "stop"          ← "stop", NOT "end"

    "stop" must match the node name (or END sentinel) registered in graph.py.
    """
    raise NotImplementedError
