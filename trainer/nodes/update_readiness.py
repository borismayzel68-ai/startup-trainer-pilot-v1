"""
trainer/nodes/update_readiness.py

Node: update_readiness
Recomputes state["readiness_scores"] from the current profile.
Uses weights from trainer/scoring.py — DO NOT redefine weights here.

Weight constraints (from trainer/scoring.py — read-only, never override):
  Team = 0.12
  (all other domain weights defined in scoring.py)

TrainerState field names (frozen contract):
  state["company_profile"]  — source of field statuses (was: profile)

Scoring logic:
  fact        → full weight contribution
  hypothesis  → partial weight (defined in scoring.py)
  weak        → minimal weight (defined in scoring.py)
  missing     → zero contribution

May retrieve regulatory/artifact requirements for scoring context:
  retrieve key: "next_artifact {jurisdiction}"
    where jurisdiction is taken from state["jurisdiction"] if set.

Constraints:
  - import weights from trainer.scoring, never hardcode them here
  - Team weight = 0.12 (frozen in scoring.py — do NOT override)
  - do NOT modify state["turn"]
  - do NOT add new fields to state
  - do NOT invent numeric scores without profile evidence
"""

from __future__ import annotations

from typing import Any


def update_readiness(state: dict[str, Any]) -> dict[str, Any]:
    """
    Recompute readiness_scores from company_profile + scoring weights.

    Steps (to implement):
      1. Import weights from trainer.scoring.
      2. Walk state["company_profile"] fields, map status → contribution.
      3. Compute per-domain weighted scores.
      4. Write to state["readiness_scores"].

    Returns partial state patch: {"readiness_scores": dict}.
    """
    raise NotImplementedError
