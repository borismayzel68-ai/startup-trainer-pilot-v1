"""/portfolio — список и карточка. Вход и роли — auth (MOS-1): require_role на каждом маршруте, актор строится
из грантов текущего пользователя (auth.current_user). Репозиторий подключается через configure() (по умолчанию —
Postgres по SESSION_DATABASE_URL; без Postgres — 503)."""
from __future__ import annotations

import json
import os
from datetime import date

from flask import Blueprint, jsonify

import auth
from . import service
from .access import Actor, FULL_ROLES, LIST_ROLES

bp = Blueprint("portfolio", __name__, url_prefix="/portfolio")


def _default_repo():
    url = os.environ.get("SESSION_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if not url.startswith("postgres"):
        raise service.Unavailable("портфель требует Postgres")
    import psycopg
    return service.PgRepo(psycopg.connect(url))


_hooks = {"repo": _default_repo}


def configure(repo_factory):
    _hooks["repo"] = repo_factory or _default_repo


def actor_from_user(user) -> Actor | None:
    """Грант owner/fund_staff со scope '*' — весь портфель; investor — объединение его scope (vc_id или '*')."""
    grants = (user or {}).get("grants") or []
    if any(r in FULL_ROLES and s == "*" for r, s in grants):
        return Actor("owner")
    scopes = frozenset(s for r, s in grants if r == "investor")
    return Actor("investor", scopes) if scopes else None


def _ok(data):
    return jsonify(json.loads(json.dumps(data, default=str)))


def _run(fn, *args):
    try:
        repo = _hooks["repo"]()
        return _ok(fn(repo, actor_from_user(auth.current_user()), *args, date.today()))
    except service.Forbidden as e:
        return jsonify({"error": str(e)}), 403
    except service.Unavailable as e:
        return jsonify({"error": str(e)}), 503
    except LookupError:
        return jsonify({"error": "not found"}), 404


@bp.get("")
@auth.require_role(LIST_ROLES)
def portfolio_list():
    return _run(service.list_view)


@bp.get("/<vc_id>")
@auth.require_role(LIST_ROLES)
def portfolio_card(vc_id):
    return _run(service.card_view, vc_id)
