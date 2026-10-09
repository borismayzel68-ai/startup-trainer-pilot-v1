"""
trainer/nodes/summarize.py

Node: summarize
Terminal node. Produces a structured session summary and writes it
to state["session_summary"]. Appends the summary as the final
assistant message in state["messages"].

Summary must include:
TrainerState field names (frozen contract):
  state["company_profile"]  — source for coverage stats (was: profile)
  state["user_intent"]      — should be "stop" or coverage-complete trigger

Summary must include:
  - per-domain readiness_scores
  - company_profile field coverage by status (fact/hypothesis/missing/weak)
  - top 3 gaps (missing or weak fields with highest domain weight)
  - recommended next artifacts (uses jurisdiction if available)

May retrieve for artifact recommendations:
  retrieve key: "next_artifact {jurisdiction}"
    where jurisdiction is taken from state["jurisdiction"] if set.

Constraints:
  - do NOT increment state["turn"]
  - do NOT add new fields to state
  - do NOT invent clinical figures, patents, regulatory approvals, n values
  - session_summary is a human-readable string (markdown acceptable)
"""

from __future__ import annotations

from typing import Any


def summarize(state: dict[str, Any]) -> dict[str, Any]:
    """
    Generate and store session summary.

    Steps (to implement):
      1. Compute coverage stats from state["company_profile"].
      2. Identify top gaps by domain weight (from trainer.scoring).
      3. Optionally retrieve: "next_artifact {jurisdiction}".
      4. Format summary (markdown).
      5. Append to conversation output as assistant turn.
      6. Write to state["session_summary"].

    Returns partial state patch: {"session_summary": str}.
    """
    raise NotImplementedError
