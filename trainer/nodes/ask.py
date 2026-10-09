"""
trainer/nodes/ask.py

Node: ask
Delivers state["current_question"] to the user (appends to messages).
In S0.5 this is a terminal output node — the graph returns here and
the next user reply re-enters via ingest.

In S1 there will be a LangGraph interrupt() after this node so the
graph suspends and waits for real user input. The edge is already
reserved in graph.py — do NOT hide or remove it.

ask_help → ask routing (frozen contract):
  - route_after_intent("ask_help") returns "ask" DIRECTLY.
    coach is NOT a route target for ask_help; it is called as an
    internal helper inside ask when user_intent == "ask_help".
  - ask does NOT increment turn (plan_next_question owns turn++).
  - If coaching_note is already set (coach was called internally),
    ask prepends it to the question text before delivery.

TrainerState field names (frozen contract):
  state["user_intent"]   — check for "ask_help" to trigger coaching path
                           (was: intent)
  state["founder_message"] is not written here; ask writes to output only.

Constraints:
  - do NOT increment state["turn"]
  - do NOT add new fields to state
  - output is appended as assistant message to conversation history
"""

from __future__ import annotations

from typing import Any


def ask(state: dict[str, Any]) -> dict[str, Any]:
    """
    Output current_question to the conversation.

    When state["user_intent"] == "ask_help":
      - call coach() as a helper (NOT via graph routing) to generate
        coaching_note, then prepend it to the question.
    Clears state["coaching_note"] after use.

    In S1: LangGraph interrupt() fires here; in S0.5 the graph ends.

    Returns partial state patch with updated conversation output
    and {"coaching_note": None}.
    """
    raise NotImplementedError
