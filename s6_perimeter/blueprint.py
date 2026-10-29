"""S6 · шлюз «Периметр входа»: /s6. Доступ — только auth.require_role (MOS-1): создание, просмотр и передача в S2 —
owner и fund_staff; снятие блокировки (release) — только owner, это решение человека, ИИ его не принимает.
Investor → 403, без входа → 401. Отправки наружу нет. Поведение S2 и S5 не меняется: S6 лишь вызывает функции S2."""
from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request

import auth
from s1_intake.main import _state
from s2_mandate import engine as S2

from . import claim as CLAIM
from . import engine as E
from . import waiver as W

bp = Blueprint("s6_perimeter", __name__, url_prefix="/s6")
STAFF = ("owner", "fund_staff")


def _err(msg: str, status: int, **extra):
    return jsonify({"error": msg, **extra}), status


def _actor() -> str | None:
    user = auth.current_user()
    return user["email"] if user else None


def _load(gate_id: str):
    s6 = ((_state().store.get(gate_id) or {}).get("data") or {}).get("s6")
    return (s6["gate"], s6.get("release"), s6.get("s2_check_id")) if s6 else (None, None, None)


def _decision(gate_id: str, rec: dict) -> dict | None:
    return W.human_decision(_state().store, gate_id, E.sha256(rec))


def _view(gate_id: str, rec: dict, release: dict | None, s2_id: str | None) -> dict:
    """Что видно снаружи. Допуск в S2 считается тем же admit(), что и при передаче."""
    try:
        E.admit(gate_id, rec, release, human_decision=_decision(gate_id, rec))
        admitted = True
    except E.GateBlocked:
        admitted = False
    return {"id": gate_id, **rec, "released_by_owner": release, "admitted_to_s2": admitted, "s2_check_id": s2_id}


class GateUsed(Exception):
    """Шлюз уже использован или занят параллельным вызовом. Ответ на повтор — 409, как у POST /s2/checks."""


def forward_to_s2(gate_id: str, sector, innovative) -> tuple[dict, int]:
    """Единственный путь заявки из S6 в S2 — и для HTTP, и для прямого вызова. GateBlocked, если периметр не пройден."""
    rec, release, s2_id = _load(gate_id)
    if rec is None:
        raise LookupError(gate_id)
    E.admit(gate_id, rec, release, human_decision=_decision(gate_id, rec))
    if s2_id or CLAIM.used_by((((_state().store.get(gate_id) or {}).get("data") or {}).get("s6")) or {}):
        raise GateUsed(gate_id)
    inp = rec["input"]
    s2_app = {"check_rub": inp["check_rub"]["value"], "instrument": inp["deal_form"]["value"],
              "sector": sector, "innovative": innovative}
    missing = S2.validate_input(s2_app)
    if missing:
        return {"status": "returned", "missing": missing}, 422
    s2_rec = {"input": {k: s2_app[k] for k in S2.FIELDS}, **S2.evaluate(s2_app, S2.load_policy()), "s6_gate": gate_id}
    store, token = _state().store, uuid.uuid4().hex
    if not CLAIM.claim(store, gate_id, token):  # тот же атомарный захват, что у POST /s2/checks
        raise GateUsed(gate_id)
    try:
        cid, _ = store.create_or_get("s2-" + uuid.uuid4().hex, "S2 mandate check", "", {"s2": {"check": s2_rec}},
                                     status="ready_for_review")
        CLAIM.settle(store, gate_id, token, cid)
    except Exception:
        CLAIM.abandon(store, gate_id, token)
        raise
    return {"s2_check_id": cid, "result": s2_rec["result"]}, 201


@bp.after_request
def _noindex(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/gates")
@auth.require_role(STAFF, scope="*")
def create_gate():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("company_id"), str) or not body["company_id"].strip():
        return _err("bad payload: company_id", 400)
    fields = body.get("fields")
    rec = {"company_id": body["company_id"], "input": fields if isinstance(fields, dict) else {},
           **E.evaluate(fields if isinstance(fields, dict) else {}, E.load_policy()), "created_by": _actor()}
    gid, _ = _state().store.create_or_get("s6-" + uuid.uuid4().hex, "S6 perimeter gate", "", {"s6": {"gate": rec}},
                                          status="ready_for_review")
    return jsonify(_view(gid, rec, None, None)), 201


@bp.get("/gates/<gate_id>")
@auth.require_role(STAFF, scope="*")
def get_gate(gate_id):
    rec, release, s2_id = _load(gate_id)
    return jsonify(_view(gate_id, rec, release, s2_id)) if rec else _err("not found", 404)


@bp.post("/gates/<gate_id>/release")
@auth.require_role(["owner"], scope="*")
def release_gate(gate_id):
    """Снятие блокировки — решение человека-owner с обязательной причиной; привязано к хешу данных, правка данных
    (новый шлюз) решение не наследует."""
    rec, release, s2_id = _load(gate_id)
    if rec is None:
        return _err("not found", 404)
    if E.evaluate(rec["input"], E.load_policy())["result"] == E.PASS:
        return _err("снятие не нужно: периметр пройден", 409)
    if release:
        return _err("already released", 409)
    reason = (request.get_json(silent=True) or {}).get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return _err("reason обязателен", 400)
    actor = _actor()
    if not W.is_person(actor or ""):
        return _err("решение человека требует именную сессию", 403)
    digest = E.sha256(rec)
    release = {"by": actor, "reason": reason.strip(), "sha256": digest}
    # Сначала аудит-лог: без этой записи admit() кейс не пропустит, даже если release уже лежит в JSON.
    W.record_waiver(_state().store, gate_id, actor, digest, reason.strip())
    if not CLAIM.set_release(_state().store, gate_id, release):  # условная запись: ссылку S2 не затирает
        return _err("already released", 409)
    return jsonify(_view(gate_id, rec, release, _load(gate_id)[2]))


@bp.post("/gates/<gate_id>/forward-to-s2")
@auth.require_role(STAFF, scope="*")
def forward_gate(gate_id):
    body = request.get_json(silent=True) or {}
    try:
        out, status = forward_to_s2(gate_id, body.get("sector"), body.get("innovative"))
    except LookupError:
        return _err("not found", 404)
    except GateUsed:
        return _err(CLAIM.USED_MSG + gate_id, 409)
    except E.GateBlocked as exc:
        return _err(str(exc), 409, gate_status=E.NEEDS_EVIDENCE, problems=exc.problems)
    return jsonify(out), status
