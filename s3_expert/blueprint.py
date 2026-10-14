"""S3 · БП-07: Flask-blueprint /s3 на существующем приложении. Новых сервисов нет.

Подпись владельца — тот же механизм, что в S1/TG-1: персональный Bearer-токен из S1_APPROVERS и журнал s1_approvals
(outcome='s3_signed', report_sha256 = хеш содержимого брифа). Второго механизма подписи нет.
G-12: пока нет записи s3_signed с хешем текущего содержимого, бриф «ждёт подписи» и не выдаётся.
G-4: в модуле нет функции внешней отправки (ни email, ни webhook, ни HTTP-клиента); выдача — ответ внутри системы.
Подпись система не ставит: только POST /s3/briefs/<id>/sign с токеном человека.
"""
from __future__ import annotations

import os
import uuid

from flask import Blueprint, Response, jsonify, request

from s1_intake.main import _admin_ok, _approver, _err, _state

from . import engine as E

bp = Blueprint("s3_expert", __name__, url_prefix="/s3")
SIGNED = "s3_signed"


def _owners() -> set[str]:
    """S3_OWNERS=имя1,имя2 — кто из подписантов S1 вправе подписывать брифы S3. Пусто → любой персональный подписант."""
    return {x.strip() for x in os.environ.get("S3_OWNERS", "").split(",") if x.strip()}


def _signer() -> str | None:
    who = _approver()
    owners = _owners()
    return who if who and (not owners or who in owners) else None


def _load(brief_id: str):
    job = _state().store.get(brief_id)
    s3 = (job or {}).get("data", {}).get("s3")
    return (job, s3) if s3 else (None, None)


def _signature(brief_id: str, brief: dict) -> dict | None:
    """Действующая подпись = запись s3_signed с хешем именно этого содержимого."""
    digest = E.brief_sha256(brief)
    return next((a for a in reversed(_state().store.approvals(brief_id))
                 if a["outcome"] == SIGNED and a["report_sha256"] == digest), None)


def _with_sign(brief_id: str, brief: dict) -> dict:
    sig = _signature(brief_id, brief)
    sign = {"state": E.STATE_SIGNED, "by": sig["approver"], "at": sig["approved_at"]} if sig else \
        {"state": E.STATE_AWAITING, "by": None, "at": None}
    return {**brief, "sign": sign}


@bp.post("/briefs")
def create_brief():
    """Вход: question · bounds · materials · cutoff. Пустое поле → 422 «вернуть пользователю», бриф не создаётся."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("bad payload", 400)
    missing = E.validate_input(body)
    if missing:
        return jsonify({"status": "returned", "missing": missing,
                        "message": "Заполните поля и отправьте запрос заново: " + ", ".join(missing)}), 422
    brief = E.build_brief(body)
    store = _state().store
    brief_id, _ = store.create_or_get("s3-" + uuid.uuid4().hex, "S3 brief", "", {"s3": {"brief": brief}})
    # статус ready_for_review: не 'received', чтобы S1 не подхватил запись как свою заявку при рестарте
    store.update(brief_id, status="ready_for_review")
    return jsonify({"id": brief_id, "state": E.STATE_AWAITING, "outcome": brief["outcome"], "class": brief["class"],
                    "issued": False}), 201


@bp.get("/briefs/<brief_id>")
def get_status(brief_id):
    """Только состояние: содержимое не выдаётся, пока нет подписи."""
    if not (_admin_ok() or _signer()):
        return _err("unauthorized", 401)
    job, s3 = _load(brief_id)
    if not s3:
        return _err("not found", 404)
    full = _with_sign(brief_id, s3["brief"])
    return jsonify({"id": brief_id, "state": full["sign"]["state"], "issued": full["sign"]["state"] == E.STATE_SIGNED,
                    "signed_by": full["sign"]["by"], "signed_at": full["sign"]["at"]})


@bp.get("/briefs/<brief_id>/review")
def review(brief_id):
    """Черновик для подписанта (видит только персональный подписант). Это не выдача: помечен как черновик."""
    if not _signer():
        return _err("personal signer required", 403 if (_admin_ok() or _approver()) else 401)
    job, s3 = _load(brief_id)
    if not s3:
        return _err("not found", 404)
    full = _with_sign(brief_id, s3["brief"])
    return jsonify({**full, "draft": full["sign"]["state"] != E.STATE_SIGNED, "sha256": E.brief_sha256(s3["brief"])})


@bp.post("/briefs/<brief_id>/sign")
def sign(brief_id):
    """«Проверил, дата»: подписывает человек. Тело: {"checked": true}. Дата — серверная (журнал)."""
    who = _signer()
    if not who:  # admin-токен подписью не является
        return _err("personal owner signature required", 403 if (_admin_ok() or _approver()) else 401)
    job, s3 = _load(brief_id)
    if not s3:
        return _err("not found", 404)
    if (request.get_json(silent=True) or {}).get("checked") is not True:
        return _err("checked=true required: подпись означает «проверил»", 400)
    brief = s3["brief"]
    if _signature(brief_id, brief):
        return _err("already signed", 409)
    _state().store.log_approval(brief_id, who, E.brief_sha256(brief), SIGNED)
    sig = _signature(brief_id, brief)
    return jsonify({"ok": True, "state": E.STATE_SIGNED, "signed_by": sig["approver"], "signed_at": sig["approved_at"]})


def _issued(brief_id: str):
    if not (_admin_ok() or _signer()):
        return None, _err("unauthorized", 401)
    job, s3 = _load(brief_id)
    if not s3:
        return None, _err("not found", 404)
    full = _with_sign(brief_id, s3["brief"])
    if full["sign"]["state"] != E.STATE_SIGNED:  # G-12
        return None, (jsonify({"error": "бриф ждёт подписи владельца и не выдаётся", "state": E.STATE_AWAITING}), 403)
    return full, None


@bp.get("/briefs/<brief_id>/brief.json")
def issue_json(brief_id):
    full, err = _issued(brief_id)
    return err or Response(E.to_json(full), mimetype="application/json")


@bp.get("/briefs/<brief_id>/brief.md")
def issue_md(brief_id):
    full, err = _issued(brief_id)
    return err or Response(E.to_markdown(full), mimetype="text/markdown; charset=utf-8")


_SIGN_PAGE = """<!doctype html><meta charset="utf-8"><title>S3 · подпись владельца</title>
<style>body{font:15px/1.5 system-ui;max-width:760px;margin:2em auto;padding:0 1em}pre{white-space:pre-wrap;background:#f5f5f5;padding:1em}
.draft{color:#a00;font-weight:600}</style>
<h1>Подпись владельца · бриф S3</h1>
<p class="draft">ЧЕРНОВИК — бриф не выдан, пока вы не подпишете.</p>
<label>Личный токен подписанта <input id="tok" type="password" autocomplete="off"></label>
<button id="load">Показать бриф</button>
<pre id="brief">—</pre>
<label><input type="checkbox" id="chk"> Проверил</label>
<button id="sign" disabled>Подписать</button>
<p id="msg"></p>
<script>
const id = location.pathname.split('/').slice(-2)[0];
const H = () => ({'Authorization': 'Bearer ' + document.getElementById('tok').value, 'Content-Type': 'application/json'});
const msg = t => document.getElementById('msg').textContent = t;
document.getElementById('load').onclick = async () => {
  const r = await fetch('/s3/briefs/' + id + '/review', {headers: H()});
  const j = await r.json();
  if (!r.ok) return msg(j.error || r.status);
  document.getElementById('brief').textContent = JSON.stringify(j, null, 2);
  document.getElementById('sign').disabled = !j.draft;
  msg(j.draft ? 'Ждёт подписи' : 'Уже подписан: ' + j.sign.by + ', ' + j.sign.at);
};
document.getElementById('sign').onclick = async () => {
  const r = await fetch('/s3/briefs/' + id + '/sign', {method: 'POST', headers: H(),
    body: JSON.stringify({checked: document.getElementById('chk').checked})});
  const j = await r.json();
  msg(r.ok ? 'Проверил: ' + j.signed_by + ', ' + j.signed_at : (j.error || r.status));
};
</script>"""


@bp.get("/briefs/<brief_id>/sign-screen")
def sign_screen(brief_id):
    """Экран подписи «проверил, дата». Страница статическая и содержимого брифа не несёт — оно грузится с токеном."""
    return Response(_SIGN_PAGE, mimetype="text/html")
