"""MOS-5 (#56): вид фонда (owner / fund_staff). Вида инвестора (Артур) здесь нет — Д-92.
Доступ — auth MOS-1: на каждом маршруте auth.require_role("owner", "fund_staff"), пользователь и роль из
auth.current_user() и грантов; investor → 403, без входа → 401. X-Robots-Tag + meta noindex на всех ответах /fund/*."""
from __future__ import annotations

import os
from html import escape

from flask import Blueprint, Response, g, jsonify, request

import auth
from dealflow.status import InvalidTransition, change_status
from . import service as S
from .gates import GateBlocked, evaluate

bp = Blueprint("fund_view", __name__, url_prefix="/fund")
FUND_ROLES = ("owner", "fund_staff")


def connect():
    import psycopg
    url = os.environ.get("FUND_DATABASE_URL") or os.environ.get("SESSION_DATABASE_URL") \
        or os.environ.get("DATABASE_URL") or ""
    if not url.startswith(("postgres://", "postgresql://")):
        return None
    return psycopg.connect(url)


def resolve_actor() -> dict | None:
    """-> {"user_id", "email", "role"} из сессии MOS-1; owner приоритетнее fund_staff."""
    user = auth.current_user()
    if not user:
        return None
    roles = g.get("rbac_roles") or {r for r, _ in user["grants"]}
    role = next((r for r in FUND_ROLES if r in roles), None)
    return {"user_id": user["id"], "email": user["email"], "role": role} if role else None


@bp.after_request
def _noindex(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _fail(msg, code, status):
    return jsonify({"error": msg, "code": code}), status


def _guard(fn):
    """Соединение + проверка роли (сервер). Пишущие вызовы коммитятся только при успехе."""
    from functools import wraps

    @wraps(fn)
    def wrapper(vc_id, *a, **kw):
        conn = connect()
        if conn is None:
            return _fail("БД Postgres не настроена", "db_unavailable", 503)
        with conn:
            actor = resolve_actor()
            if actor is None:  # require_role уже отсёк; защита на случай отключённого RBAC в тестах
                return _fail("нет доступа", "unauthorized", 401)
            st = S.find_startup(conn, vc_id)
            if st is None:
                return _fail("стартап не найден", "not_found", 404)
            try:
                return fn(conn, actor, st, *a, **kw)
            except (S.FundError, InvalidTransition) as exc:
                conn.rollback()
                return _fail(str(exc), "bad_request", 400)
            except GateBlocked as exc:
                conn.rollback()
                return jsonify({"error": str(exc), "code": "gate_blocked", "failed_gates": exc.failed}), 409
    return wrapper


def _owner_only(actor):
    return None if actor["role"] == "owner" else _fail("только owner", "forbidden", 403)


def _view(conn, st) -> dict:
    snap = S.load_snapshot(conn, st["id"])
    return {"startup": st, "snapshot": snap, "gates": evaluate(snap),
            "standard": S.standard_checklist()}


@bp.get("/startups/<vc_id>/snapshot")
@auth.require_role(FUND_ROLES, scope="*")
@_guard
def get_snapshot(conn, actor, st):
    return jsonify(_view(conn, st))


@bp.get("/startups/<vc_id>/snapshot.html")
@auth.require_role(FUND_ROLES, scope="*")
@_guard
def get_snapshot_html(conn, actor, st):
    v = _view(conn, st)
    snap = v["snapshot"] or {}
    rows = "".join(f"<tr><th>{escape(k)}</th><td>{escape(str(snap.get(k) or '—'))}</td></tr>"
                   for k in (*S.EDITABLE, "asf_engagement", "evidence_status"))
    gates = "".join(f"<li>{g}: {'PASS' if ok else 'BLOCK'}</li>" for g, ok in v["gates"].items())
    std = v["standard"]
    items = "".join(f"<li>[ ] {escape(i['text'])} <small>({escape(i['section'])})</small></li>" for i in std["items"])
    page = (f"<!doctype html><meta charset=utf-8><meta name=robots content='noindex,nofollow'>"
            f"<title>Decision Snapshot {escape(st['vc_id'])}</title>"
            f"<h1>{escape(st['name'])} · {escape(st['status'])}</h1><table>{rows}</table>"
            f"<h2>Шлюзы</h2><ul>{gates}</ul><h2>Стандарт: {escape(std['label'])}</h2><ul>{items}</ul>")
    return Response(page, mimetype="text/html")


@bp.put("/startups/<vc_id>/snapshot")
@auth.require_role(FUND_ROLES, scope="*")
@_guard
def put_snapshot(conn, actor, st):
    body = request.get_json(silent=True) or {}
    if "evidence_status" in body or "asf_engagement" in body:
        return _fail("evidence_status и asf_engagement меняются только подписью owner", "bad_request", 400)
    snap = S.save_snapshot(conn, st["id"], body, actor)
    return jsonify({"snapshot": snap, "gates": evaluate(snap)})


@bp.post("/startups/<vc_id>/engagement")
@auth.require_role(FUND_ROLES, scope="*")
@_guard
def post_engagement(conn, actor, st):
    denied = _owner_only(actor)
    if denied:
        return denied
    snap = S.set_engagement(conn, st["id"], (request.get_json(silent=True) or {}).get("value", ""),
                            actor)
    return jsonify({"snapshot": snap, "gates": evaluate(snap)})


@bp.post("/startups/<vc_id>/sign-fact")
@auth.require_role(FUND_ROLES, scope="*")
@_guard
def post_sign_fact(conn, actor, st):
    denied = _owner_only(actor)
    if denied:
        return denied
    return jsonify({"snapshot": S.sign_fact(conn, st["id"], actor)})


@bp.post("/startups/<vc_id>/transition")
@auth.require_role(FUND_ROLES, scope="*")
@_guard
def post_transition(conn, actor, st):
    body = request.get_json(silent=True) or {}
    to = body.get("to_status", "")
    change_status(conn, st["id"], to, reason=str(body.get("reason", "")), changed_by=actor["user_id"])
    S.audit(conn, actor, "status.change", "startup", st["id"], {"from": st["status"], "to": to})
    return jsonify({"from_status": st["status"], "to_status": to})


@bp.post("/startups/<vc_id>/ic-decision")
@auth.require_role(FUND_ROLES, scope="*")
@_guard
def post_decision(conn, actor, st):
    body = request.get_json(silent=True) or {}
    return jsonify(S.record_decision(conn, st["id"], body.get("quarter", ""), body.get("decision", ""),
                                     body.get("reason", ""), actor))
