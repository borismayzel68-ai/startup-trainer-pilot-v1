"""S2 · шлюз мандата: /s2. Вход заявки — только через пройденный периметр S6 (s6_gate_id, Д-59; admin-токен это не обходит).
Решение по EXCEPTION — только персональная подпись человека (механизм S1: S1_APPROVERS +
журнал s1_approvals). Admin-токен и система решение не принимают. Отправки наружу нет."""
from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request

from s1_intake.main import _admin_ok, _approver, _err, _state

from s6_perimeter import claim as CLAIM
from s6_perimeter import engine as S6

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


def _gate_problem(gate_id, body: dict) -> str | None:
    """Причина отказа в допуске к S2 или None. Допуск считает S6.admit() (PASS либо снятие owner с тем же sha256);
    заявка обязана совпадать с данными шлюза (чек, форма сделки), шлюз — одноразовый."""
    if not isinstance(gate_id, str) or not gate_id.strip():
        return "s6_gate_id обязателен: заявка без пройденного периметра входа S6 в S2 не идёт"
    s6 = ((_state().store.get(gate_id) or {}).get("data") or {}).get("s6")
    if not s6:
        return "шлюз S6 не найден: " + gate_id
    rec, release = s6["gate"], s6.get("release")
    try:
        S6.admit(gate_id, rec, release)
    except S6.GateBlocked as exc:
        return str(exc)
    inp = rec["input"]
    if body["check_rub"] != inp["check_rub"]["value"] or body["instrument"] != inp["deal_form"]["value"]:
        return "заявка не совпадает с данными шлюза S6 (check_rub, instrument)"
    if CLAIM.used_by(s6):
        return CLAIM.USED_MSG + CLAIM.used_by(s6)
    return None


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
    gate_id = body.get("s6_gate_id")
    why = _gate_problem(gate_id, body)
    if why:
        return _err(why, 409)
    rec = {"input": {k: body[k] for k in E.FIELDS}, **E.evaluate(body, E.load_policy()), "s6_gate": gate_id}
    store, token = _state().store, uuid.uuid4().hex
    if not CLAIM.claim(store, gate_id, token):  # атомарно: из параллельных вызовов шлюз займёт один
        return _err(CLAIM.USED_MSG + "параллельным вызовом", 409)
    try:
        cid, _ = store.create_or_get("s2-" + uuid.uuid4().hex, "S2 mandate check", "", {"s2": {"check": rec}},
                                     status="ready_for_review")
        CLAIM.settle(store, gate_id, token, cid)
    except Exception:
        CLAIM.abandon(store, gate_id, token)  # запись не создана — шлюз снова свободен
        raise
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
    if _decision(check_id, rec) or not _state().store.log_decision_once(
            check_id, who, E.sha256(rec), APPROVED if choice == "approve" else REJECTED):
        return _err("already decided", 409)  # второй подписант (в т.ч. гонка) — UNIQUE в журнале
    return jsonify(_view(check_id, rec))
