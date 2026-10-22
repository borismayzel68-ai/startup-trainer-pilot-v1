"""/portfolio — список и карточка. Актор и репозиторий подключаются через configure(); по умолчанию актора нет → 403.
Сессионной авторизации по ролям в приложении пока нет: подключение резолвера — отдельный шаг (#49)."""
from __future__ import annotations

import json
from datetime import date

from flask import Blueprint, jsonify

from . import service

bp = Blueprint("portfolio", __name__, url_prefix="/portfolio")
_hooks = {"actor": lambda: None, "repo": lambda: None}


def configure(actor_resolver, repo_factory):
    _hooks["actor"], _hooks["repo"] = actor_resolver, repo_factory


def _ok(data):
    return jsonify(json.loads(json.dumps(data, default=str)))


@bp.get("")
def portfolio_list():
    try:
        return _ok(service.list_view(_hooks["repo"](), _hooks["actor"](), date.today()))
    except service.Forbidden as e:
        return jsonify({"error": str(e)}), 403


@bp.get("/<vc_id>")
def portfolio_card(vc_id):
    try:
        return _ok(service.card_view(_hooks["repo"](), _hooks["actor"](), vc_id, date.today()))
    except service.Forbidden as e:
        return jsonify({"error": str(e)}), 403
    except LookupError:
        return jsonify({"error": "not found"}), 404
