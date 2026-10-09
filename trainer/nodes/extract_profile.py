"""
trainer/nodes/extract_profile.py

Node: extract_profile
Extracts structured profile facts from the current answer and updates
state["profile"] with per-field statuses.

Profile field status vocabulary (canonical, no others):
  fact        — confirmed, sourced claim
  hypothesis  — plausible but unverified claim
  missing     — field not covered by any answer yet
  weak        — unverified or vague; "unverified" is stored as weak

TrainerState field names (frozen contract):
  state["founder_message"]   — raw input to parse (was: current_answer)
  state["company_profile"]   — profile dict to update (was: profile)

Constraints:
  - statuses only: fact | hypothesis | missing | weak
  - do NOT modify state["turn"]
  - do NOT add new fields to state
  - retrieve knowledge literals (for future S1 integration):
      questions {domain} {stage}
      evidence_standard {study_design}
      next_artifact {jurisdiction}
"""

from __future__ import annotations

from typing import Any


def extract_profile(state: dict[str, Any]) -> dict[str, Any]:
    """
    Parse state["founder_message"] and update state["company_profile"].

    Each profile key gets a status: fact | hypothesis | missing | weak.
    Unverified claims are stored as weak, never as "unverified".

    Returns partial state patch with updated {"company_profile": {...}}.
    """
    raise NotImplementedError
