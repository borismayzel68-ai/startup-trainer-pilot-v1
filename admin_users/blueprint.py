"""FEAT-001 T-003 (#114): админка пользователей и «Данные» (API-004…API-008). Только owner на все проекты.
auth.py не меняется: приглашения и блокировка — функции T-002 (auth.invite_user / revoke_invite / block_user /
unblock_user). «Данные» — только чтение, белый список таблиц и столбцов; секретов, токенов и хэшей там нет."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, redirect, render_template, request

import auth
from auth_store import ROLE_LABELS

bp = Blueprint("admin_users", __name__, url_prefix="/admin")
OWNER = ("owner",)

# Белый список: таблица -> (заголовок, явный перечень столбцов). Новый столбец не появится, пока его не добавят здесь.
DATA_TABLES = {
    "users": ("Пользователи", ("id", "email", "display_name", "created_at", "blocked_at")),
    "role_grants": ("Роли", ("id", "user_id", "role_code", "scope", "granted_at", "revoked_at")),
    "access_invites": ("Приглашения", ("id", "email", "role", "scope", "invited_by", "created_at", "revoked_at")),
    "audit_log": ("Журнал", ("id", "at", "actor", "action", "object", "result", "detail")),
}
ERRORS = {
    "invalid_invite": "Проверьте email и роль.",
    "founder_needs_project": "Для основателя укажите номер проекта (например VC-991).",
    "self_block": "Себя заблокировать нельзя.",
    "last_owner": "Нельзя заблокировать последнего владельца фонда.",
    "not_found": "Не найдено.",
}


def _wants_html():
    return "text/html" in request.headers.get("Accept", "")


def _reply(status, payload, code=None):
    """HTML-форма (Accept: text/html) → страница списка с сообщением; иначе JSON."""
    if _wants_html():
        if status < 400:
            return redirect("/admin/users?ok=1", 303)
        return _users_page(error=ERRORS.get(code, "Ошибка."), status=status)
    if code:
        return jsonify({"error": ERRORS.get(code, "Ошибка"), "code": code}), status
    return jsonify(payload), status


def _users_page(error=None, status=200):
    q = (request.args.get("q") or "").strip()[:100]
    state = request.args.get("state", "")
    store = auth.store()
    users = store.list_users(q or None, {"blocked": True, "active": False}.get(state))
    invites = store.list_invites(q or None)
    me = auth.current_user()
    if not _wants_html():
        return jsonify({"users": [dict(u, grants=[{"role": r, "scope": s} for r, s in u["grants"]]) for u in users],
                        "invites": invites}), status
    rows = [dict(u, roles=", ".join("%s (%s)" % (ROLE_LABELS.get(r, r), "все" if s == "*" else s)
                                    for r, s in u["grants"]), is_me=u["id"] == me["id"]) for u in users]
    resp = current_app.make_response((render_template(
        "admin_users.html", csrf_token=auth.csrf_token(), users=rows, invites=invites, q=q, state=state,
        roles=list(ROLE_LABELS.items()), error=error, ok=request.args.get("ok"), role_labels=ROLE_LABELS), status))
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _id(raw):
    """ID из пути: не число → None (404 после проверки прав; общий тест маршрутов подставляет «x»)."""
    return int(raw) if raw.isdigit() else None


def _field(name):
    if request.is_json:
        return str((request.get_json(silent=True) or {}).get(name) or "")
    return request.form.get(name) or ""


@bp.get("/users")
@auth.require_role(OWNER, "*")
def users():  # API-004
    return _users_page()


@bp.post("/users/invite")
@auth.require_role(OWNER, "*")
def invite():  # API-005
    actor = auth.current_user()["email"]
    email, role, scope = _field("email").strip(), _field("role").strip(), _field("scope").strip() or "*"
    try:
        invite_id = auth.invite_user(email, role, scope, actor=actor)
    except ValueError as exc:
        return _reply(400, None, str(exc) if str(exc) in ERRORS else "invalid_invite")
    return _reply(201, {"ok": True, "id": invite_id})


@bp.post("/users/<user_id>/block")
@auth.require_role(OWNER, "*")
def block(user_id):  # API-006
    actor = auth.current_user()["email"]
    store = auth.store()
    user_id = _id(user_id)
    target = store.user_by_id(user_id) if user_id is not None else None
    if target is None:
        return _reply(404, None, "not_found")
    is_owner = ("owner", "*") in store.active_grants(user_id)
    if target["email"].lower() != actor.lower() and is_owner and store.count_active_owners(user_id) == 0:
        auth.audit("block", "users/%s" % user_id, "deny", actor=actor, detail="last_owner")
        return _reply(400, None, "last_owner")
    try:
        auth.block_user(user_id, actor=actor)
    except ValueError:
        return _reply(400, None, "self_block")
    return _reply(200, {"ok": True})


@bp.post("/users/<user_id>/unblock")
@auth.require_role(OWNER, "*")
def unblock(user_id):  # API-006
    if _id(user_id) is None or not auth.unblock_user(_id(user_id), actor=auth.current_user()["email"]):
        return _reply(404, None, "not_found")
    return _reply(200, {"ok": True})


@bp.post("/invites/<invite_id>/revoke")
@auth.require_role(OWNER, "*")
def revoke(invite_id):  # API-007
    if _id(invite_id) is None or not auth.revoke_invite(_id(invite_id), actor=auth.current_user()["email"]):
        return _reply(404, None, "not_found")
    return _reply(200, {"ok": True})


@bp.get("/data")
@auth.require_role(OWNER, "*")
def data_index():
    return redirect("/admin/data/users", 302)


@bp.get("/data/<table>")
@auth.require_role(OWNER, "*")
def data(table):  # API-008: только чтение
    if table not in DATA_TABLES:
        return _reply(404, None, "not_found")
    title, columns = DATA_TABLES[table]
    q = (request.args.get("q") or "").strip()[:100]
    rows = auth.store().data_rows(table, columns, q or None)
    if not _wants_html():
        return jsonify({"table": table, "columns": list(columns), "rows": rows})
    resp = current_app.make_response(render_template(
        "admin_data.html", csrf_token=auth.csrf_token(), table=table, title=title, columns=columns, rows=rows, q=q,
        tables=[(t, v[0]) for t, v in DATA_TABLES.items()]))
    resp.headers["Cache-Control"] = "no-store"
    return resp
