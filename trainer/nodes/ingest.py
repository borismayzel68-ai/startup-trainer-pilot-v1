"""
trainer/nodes/ingest.py

Node: ingest
Entry point. Receives raw user message, populates messages list,
initialises domain/stage/turn if first call.

TrainerState contract — frozen field names (do NOT add fields):
  state["founder_message"]   str  — raw user input (was: messages[-1].content)
  state["user_intent"]       str | None  — answer|ask_help|stop|upload_note
                                           (was: intent; "off_topic" → "stop")
  state["company_profile"]   dict — field statuses: fact|hypothesis|missing|weak
                                    (was: profile)
  state["turn"]              int, initialised to 0
  state["domain"]            str, e.g. "oncology"
  state["stage"]             str, e.g. "pre_seed"
  state["current_question"]  str | None
  state["current_answer"]    str | None
  state["readiness_scores"]  dict
  state["kb_results"]        list
  state["jurisdiction"]      str | None
  state["study_design"]      str | None
  state["coaching_note"]     str | None
  state["session_summary"]   str | None

Constraints:
  - turn += 1 happens ONLY in plan_next_question on new current_question
  - ask_help → ask does NOT consume a turn
  - profile statuses: fact | hypothesis | missing | weak (never "unverified")
  - do NOT add fields to state
"""

from __future__ import annotations

from typing import Any


def ingest(state: dict[str, Any]) -> dict[str, Any]:
    """
    Ingest raw user input into the state.

    Expected caller contract:
      - caller sets state["_raw_input"] (str) before invoking the graph
      - ingest appends it as a user message to state["messages"]
      - initialises current_answer from _raw_input
      - does NOT increment turn (plan_next_question owns that)

    Returns partial state patch (LangGraph merges).
    """
    raise NotImplementedError
