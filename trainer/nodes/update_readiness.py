"""
Node: update_readiness — score и gaps.

Пишет: readiness, unanswered_gaps.
Веса только из trainer.scoring (здесь не определяются). turn не трогает.
S0.5-докстринг называл поле readiness_scores — в TrainerState это readiness.
"""

from __future__ import annotations

from typing import Any

from trainer.scoring import compute_readiness
from trainer.slots import compute_gaps


def update_readiness(state: dict[str, Any]) -> dict[str, Any]:
    profile = state.get("company_profile") or {}
    asked_ids = {q.get("id") for q in state.get("asked_questions") or []}
    return {
        "readiness": compute_readiness(profile, state.get("qa_log") or []),
        "unanswered_gaps": compute_gaps(profile, asked_ids),
    }
