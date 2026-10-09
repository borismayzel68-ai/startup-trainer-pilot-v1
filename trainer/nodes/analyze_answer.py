"""
trainer/nodes/analyze_answer.py

Node: analyze_answer
Deep analysis of the founder's answer for the current question.
Updates profile fields with evidence quality assessment.

Evidence quality maps to profile status:
  Strong sourced claim   → fact
  Plausible, unsourced   → hypothesis
  Vague / hedged         → weak        (NOT "unverified")
  Not addressed          → missing

May invoke kb retrieve to cross-check against standards:
  retrieve key: "evidence_standard {study_design}"
    where study_design is taken from state["study_design"] if set.

TrainerState field names (frozen contract):
  state["founder_message"]   — answer to analyse (was: current_answer)
  state["company_profile"]   — profile to update  (was: profile)
  state["user_intent"]       — must be "answer" to reach this node

Constraints:
  - statuses only: fact | hypothesis | missing | weak
  - do NOT increment state["turn"]
  - do NOT add new fields to state
  - do NOT invent clinical figures, patents, regulatory approvals, n values
"""

from __future__ import annotations

from typing import Any


def analyze_answer(state: dict[str, Any]) -> dict[str, Any]:
    """
    Analyse state["founder_message"] against state["current_question"].

    Steps (to implement):
      1. Optionally look up kb: "evidence_standard {study_design}"
      2. Score claim strength → status (fact|hypothesis|missing|weak).
      3. Update relevant company_profile fields.
      4. Optionally update state["study_design"] if detected in answer.

    Returns partial state patch:
      {"company_profile": {...}, "study_design": str|None}.
    """
    raise NotImplementedError
