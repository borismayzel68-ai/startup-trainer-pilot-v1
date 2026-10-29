"""S2 · шлюз мандата: /s2. Вход заявки — только через пройденный периметр S6 (s6_gate_id, Д-59).
Роли admin и owner этот запрет не обходят: NEEDS_EVIDENCE → 403 blocked и запись в аудит-лог.
Исключение — явное решение человека в аудит-логе (не поле release в JSON и не admin-токен).
Решение по EXCEPTION — только персональная подпись человека (механизм S1: S1_APPROVERS +
журнал s1_approvals). Отправки наружу нет."""
from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request

from s1_intake.main import _admin_ok, _approver, _err, _state

from s6_perimeter import claim as CLAIM
from s6_perimeter import engine as S6
from s6_perimeter import waiver as W

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


def _caller() -> tuple[str, str]:
    """(актор, роль) для записи в аудит. Admin-токен — роль admin, не именной человек."""
    from flask import g
    roles = set(g.get("rbac_roles") or ())
    if "owner" in roles:
        role = "owner"
    elif "fund_staff" in roles:
        role = "fund_staff"
    elif _admin_ok():
        role = "admin"
    else:
        role = "unknown"
    email = None
    try:
        import auth
        from flask import current_app
        if "auth_store" in current_app.extensions:
            email = (auth.current_user() or {}).get("email")
    except RuntimeError:
        email = None
    return (email or role), role


def _gate_problem(gate_id, body: dict):
    """None, если допуск есть. Иначе (код, сообщение, доп.поля).

    403 — периметр не пройден (NEEDS_EVIDENCE) и в аудит-логе нет решения человека.
    409 — нет id, шлюз не найден, заявка не совпадает со шлюзом или шлюз уже использован.
    """
    if not isinstance(gate_id, str) or not gate_id.strip():
        return 409, "s6_gate_id обязателен: заявка без пройденного периметра входа S6 в S2 не идёт", {}
    s6 = ((_state().store.get(gate_id) or {}).get("data") or {}).get("s6")
    if not s6:
        return 409, "шлюз S6 не найден: " + str(gate_id), {}
    rec, release = s6["gate"], s6.get("release")
    digest = S6.sha256(rec)
    try:
        S6.admit(gate_id, rec, release, human_decision=W.human_decision(_state().store, gate_id, digest))
    except S6.GateBlocked as exc:
        return 403, str(exc), {"digest": digest, "problems": exc.problems, "gate_id": gate_id}
    inp = rec["input"]
    if body["check_rub"] != inp["check_rub"]["value"] or body["instrument"] != inp["deal_form"]["value"]:
        return 409, "заявка не совпадает с данными шлюза S6 (check_rub, instrument)", {}
    if CLAIM.used_by(s6):
        return 409, CLAIM.USED_MSG + CLAIM.used_by(s6), {}
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
    problem = _gate_problem(gate_id, body)
    if problem:
        status, message, extra = problem
        if status == 403:
            actor, role = _caller()
            W.record_block(_state().store, extra.get("gate_id") or "", actor, role, extra.get("digest") or "")
            return jsonify({"error": message, "blocked": True, "gate_status": S6.NEEDS_EVIDENCE,
                            "problems": extra.get("problems") or []}), 403
        return _err(message, status)
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
