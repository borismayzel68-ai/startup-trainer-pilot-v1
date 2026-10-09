"""
trainer/nodes/plan_next_question.py

Node: plan_next_question
Selects the next question to ask the founder based on:
  - current profile coverage (missing / weak fields)
  - domain and stage
  - readiness_scores (to prioritise gaps)

THIS IS THE ONLY NODE ALLOWED TO INCREMENT state["turn"].
  turn += 1  ← happens here, and only when a genuinely new
               current_question is set (not a repeat, not ask_help).

TrainerState field names (frozen contract):
  state["company_profile"]  — source for coverage gaps (was: profile)
  state["user_intent"]      — must NOT be "ask_help" to reach this node
                              (ask_help bypasses plan_next_question entirely)

Constraints:
  - turn++ ONLY on new current_question; never on ask_help routing
  - ask_help path does NOT reach this node (route_after_intent → ask directly)
  - do NOT add new fields to state
  - retrieve literals for kb lookup (future S1):
      questions {domain} {stage}
  - profile statuses: fact | hypothesis | missing | weak
"""

from __future__ import annotations

from typing import Any


def plan_next_question(state: dict[str, Any]) -> dict[str, Any]:
    """
    Determine next question and increment turn counter.

    Logic (to implement):
      1. Query kb for domain+stage question bank:
           retrieve key: "questions {domain} {stage}"
      2. Find highest-priority missing/weak profile field.
      3. Set state["current_question"] to the selected question text.
      4. Increment state["turn"] += 1.
         IMPORTANT: turn is NOT incremented if the question is a repeat
         or if this node was reached via ask_help intent.

    Returns partial state patch: {"current_question": str, "turn": int}.
    """
    raise NotImplementedError
