"""
trainer/nodes/route_intent.py

Node: route_intent  +  conditional-edge function: route_after_intent

Classifies the intent of the founder's message and sets
state["user_intent"] to one of:

  "answer"       — founder is answering the current question
  "ask_help"     — founder is asking for clarification / coaching
  "stop"         — founder wants to end the session (was: off_topic)
  "upload_note"  — founder is attaching a document / note

TrainerState field names (frozen contract):
  state["founder_message"]   — raw user input   (was: messages[-1])
  state["user_intent"]       — classified intent (was: intent)
  state["company_profile"]   — startup profile   (was: profile)

route_after_intent() is the LangGraph conditional edge that reads
state["user_intent"] and returns the name of the next node:

  "answer"       → "analyze_answer"
  "ask_help"     → "ask"          ← DIRECT: no intermediate coach node
                                    (ask reads coaching_note set by coach
                                     if coach was called earlier; here it
                                     goes straight to ask which calls coach
                                     internally as a helper, NOT as a route)
  "stop"         → "summarize"    (was: off_topic → ask)
  "upload_note"  → "extract_profile"

turn is NOT incremented on ask_help path (plan_next_question owns turn++).

Constraints:
  - do NOT increment state["turn"] here
  - do NOT add new fields to state
  - user_intent values: answer | ask_help | stop | upload_note
"""

from __future__ import annotations

from typing import Any


def route_intent(state: dict[str, Any]) -> dict[str, Any]:
    """
    Classify state["founder_message"] and write state["user_intent"].

    Intent vocabulary: answer | ask_help | stop | upload_note

    Returns partial state patch: {"user_intent": str}.
    """
    raise NotImplementedError


def route_after_intent(state: dict[str, Any]) -> str:
    """
    LangGraph conditional edge function.

    Reads state["user_intent"] and returns the next node name:
      "answer"       → "analyze_answer"
      "ask_help"     → "ask"             (NOT "coach" — see module docstring)
      "stop"         → "summarize"
      "upload_note"  → "extract_profile"

    Must return a string matching a node name registered in graph.py.
    """
    raise NotImplementedError
