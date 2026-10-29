"""GET /s5/today — экран «Сегодня» по шести синтетическим компаниям.

Только чтение. Черновики действий в ответе есть, отправки нет (Human Gate).
"""
from __future__ import annotations

from flask import Blueprint, jsonify

import auth

from .engine import RED, YELLOW, evaluate
from .synthetic import COMPANIES

bp = Blueprint("s5_monitor", __name__, url_prefix="/s5")
READERS = ("owner", "fund_staff", "investor")
_ORDER = {RED: 0, YELLOW: 1, "GREEN": 2}


def _public(row: dict) -> dict:
    keep = ("company_id", "synthetic", "state", "color", "color_label", "runway_months",
            "thresholds", "threshold_note", "drivers", "draft_actions", "human_gate", "sent",
            "facts_rejected", "partial_exit_check")
    return {k: row[k] for k in keep if k in row}


@bp.after_request
def _noindex(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/today")
@auth.require_role(READERS)
def today():
    rows = [_public(evaluate(c)) for c in COMPANIES]
    rows.sort(key=lambda r: _ORDER.get(r.get("color"), 9))
    return jsonify({"synthetic": True, "sent": False, "companies": rows})


@bp.get("/today/<company_id>")
@auth.require_role(READERS)
def one(company_id):
    case = next((c for c in COMPANIES if c["company_id"] == company_id), None)
    if case is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(_public(evaluate(case)))
