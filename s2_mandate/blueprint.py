"""S2 · шлюз мандата: /s2. Решение по EXCEPTION — только персональная подпись человека (механизм S1: S1_APPROVERS +
журнал s1_approvals). Admin-токен и система решение не принимают. Отправки наружу нет."""
from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request

from s1_intake.main import _admin_ok, _approver, _err, _state

from . import engine as E

bp = Blueprint("s2_mandate", __name__, url_prefix="/s2")
APPROVED, REJECTED = "s2_exception_approved", "s2_exception_rejected"


def _decision(check_id: str, rec: dict) -> dict | None:
    digest = E.sha256(rec)
    return next((a for a in reversed(_state().store.approvals(check_id))
                 if a["outcome"] in (APPROVED, REJECTED) and a["report_sha256"] == digest), None)


def _view(check_id: str, rec: dict) -> dict:
    d = _decision(check_id, rec)
    human = ({"decision": "approved" if d["outcome"] == APPROVED else "rejected",
              "by": d["approver"], "at": d["approved_at"]} if d else None)
    cleared = rec["result"] == E.FIT or bool(
        human and human["decision"] == "approved" and rec["result"] == E.EXCEPTION)
    return {"id": check_id, **rec, "human_decision": human, "cleared_for_meeting": cleared}


def _load(check_id: str):
    s2 = ((_state().store.get(check_id) or {}).get("data") or {}).get("s2")
    return s2["check"] if s2 else None


@bp.post("/checks")
def create_check():
    if not _admin_ok():
        return _err("unauthorized", 401)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("bad payload", 400)
    missing = E.validate_input(body)
    if missing:
        return jsonify({"status": "returned", "missing": missing}), 422
    rec = {"input": {k: body[k] for k in E.FIELDS}, **E.evaluate(body, E.load_policy())}
    store = _state().store
    cid, _ = store.create_or_get("s2-" + uuid.uuid4().hex, "S2 mandate check", "", {"s2": {"check": rec}})
    store.update(cid, status="ready_for_review")
    return jsonify(_view(cid, rec)), 201


@bp.get("/checks/<check_id>")
def get_check(check_id):
    if not (_admin_ok() or _approver()):
        return _err("unauthorized", 401)
    rec = _load(check_id)
    return jsonify(_view(check_id, rec)) if rec else _err("not found", 404)


@bp.post("/checks/<check_id>/decision")
def decide(check_id):
    """Human Gate для EXCEPTION. Тело: decision = approve | reject. Только персональный токен."""
    who = _approver()
    if not who:
        return _err("personal signature required", 403 if _admin_ok() else 401)
    rec = _load(check_id)
    if not rec:
        return _err("not found", 404)
    if rec["result"] != E.EXCEPTION:
        return _err("решение человека нужно только для EXCEPTION", 409)
    choice = (request.get_json(silent=True) or {}).get("decision")
    if choice not in ("approve", "reject"):
        return _err("decision: approve|reject", 400)
    if _decision(check_id, rec):
        return _err("already decided", 409)
    _state().store.log_approval(check_id, who, E.sha256(rec), APPROVED if choice == "approve" else REJECTED)
    return jsonify(_view(check_id, rec))
