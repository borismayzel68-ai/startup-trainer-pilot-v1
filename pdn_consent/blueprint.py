"""Страница согласия 152-ФЗ: /consent.

Чекбокс на странице не отмечен. Без него заявка S1 и вход инвестора не принимаются (400).
С чекбоксом пишется строка журнала: email, версия текста, время UTC, IP.
«Отозвать согласие» ставит revoked_at. Наружу ничего не отправляется (Human Gate).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session

import auth
from pdn_consent import TEXT_VERSION

bp = Blueprint("pdn_consent", __name__)

TEXT_PATH = Path(__file__).resolve().parent / "text_v0-draft.txt"
PROJECT_PLACEHOLDER = "[Название проекта]"
EMAIL_PLACEHOLDER = "[email]"
FLOWS = {"s1_intake", "investor_login"}
_YES = {"yes", "true", "1", "on", "да"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def project_name() -> str:
    """Плейсхолдер из CONSENT_PROJECT_NAME. Пустое значение не заменяется выдуманным названием."""
    return os.environ.get("CONSENT_PROJECT_NAME", "").strip() or PROJECT_PLACEHOLDER


def contact_email() -> str:
    """Плейсхолдер из CONSENT_CONTACT_EMAIL. Пустое значение не заменяется выдуманным адресом."""
    return os.environ.get("CONSENT_CONTACT_EMAIL", "").strip() or EMAIL_PLACEHOLDER


def render_notice() -> str:
    raw = TEXT_PATH.read_text(encoding="utf-8").strip()
    return raw.replace("{project_name}", project_name()).replace("{contact_email}", contact_email())


def _store():
    return current_app.extensions["pdn_store"]


def _wants_json() -> bool:
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def _page(error=None, status=200, saved=None):
    body = render_template(
        "consent.html",
        csrf_token=auth.csrf_token(),
        notice=render_notice(),
        text_version=TEXT_VERSION,
        error=error,
        saved=saved,
        project_name=project_name(),
        contact_email=contact_email(),
    )
    return body, status


def _fail(message: str, code: str, status: int = 400):
    if _wants_json():
        return jsonify({"error": message, "code": code}), status
    return _page(error=message, status=status)


def _clean_email(raw) -> str | None:
    email = str(raw or "").strip().lower()
    if not email or len(email) > 320 or not _EMAIL.match(email):
        return None
    return email


def _consent_given() -> bool:
    return str(request.form.get("consent") or "").strip().lower() in _YES


_SESSION_EMAILS = "pdn_emails"
_MAX_SESSION_EMAILS = 5


def _remember_email(email: str) -> None:
    """Email'ы, для которых согласие отмечено в этой сессии браузера (не больше 5)."""
    seen = [e for e in session.get(_SESSION_EMAILS, []) if e != email]
    session[_SESSION_EMAILS] = (seen + [email])[-_MAX_SESSION_EMAILS:]


def _may_revoke(email: str) -> bool:
    """Отзыв — только своего согласия: email вошедшего пользователя или отмеченный в этой же сессии браузера.
    Иначе любой посетитель /consent мог бы отозвать чужое согласие (журнал 152-ФЗ и вход жертвы)."""
    user = auth.current_user() if "auth_store" in current_app.extensions else None
    if user and user["email"].lower() == email:
        return True
    return email in session.get(_SESSION_EMAILS, [])


@bp.get("/consent")
@auth.public
def page():
    return _page()


@bp.post("/consent")
@auth.public
def submit():
    if not auth._csrf_ok():
        return _fail("Сессия устарела, откройте страницу ещё раз", "csrf")
    email = _clean_email(request.form.get("email"))
    if not email:
        return _fail("Укажите email", "email_required")
    flow = str(request.form.get("flow") or "").strip()
    if flow not in FLOWS:
        return _fail("Неизвестная форма", "flow_invalid")
    if not _consent_given():
        return _fail("Нужно согласие на обработку персональных данных", "consent_required")
    row = _store().record(email, TEXT_VERSION, auth.client_ip(), flow)
    _remember_email(email)
    if flow == "investor_login":
        session["pdn_consent_id"] = row["id"]
        if not _wants_json():
            return redirect("/auth/login")
    payload = {"ok": True, **row, "forwarded": False}
    if _wants_json():
        return jsonify(payload)
    return _page(saved=payload)


@bp.post("/consent/revoke")
@auth.public
def revoke():
    if not auth._csrf_ok():
        return _fail("Сессия устарела, откройте страницу ещё раз", "csrf")
    email = _clean_email(request.form.get("email"))
    if not email:
        return _fail("Укажите email", "email_required")
    if not _may_revoke(email):  # проверка стенда 30.10: иначе аноним отзывал согласие любого email
        return _fail("Отозвать здесь можно только своё согласие: войдите или отметьте согласие в этом браузере. Иначе — письмом на адрес из текста согласия",
                     "consent_not_yours", 403)
    rows = _store().revoke(email)
    if not rows:
        return _fail("Нет действующего согласия", "consent_not_found", 404)
    payload = {"ok": True, "revoked": rows}
    if _wants_json():
        return jsonify(payload)
    return _page(saved={"revoked_at": rows[0]["revoked_at"], "user_email": email})
