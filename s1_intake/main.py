"""S1 · БП-01 «Стартап до раунда»: Flask-blueprint приёма заявок с Tilda.

Поток: Tilda → POST /s1/webhooks/tilda (мгновенный 200 "ok") → фоновый поток:
скачивание и парсинг файлов → evaluator (классификация, правила, скоринг, Марк/Анна,
синтез) → formatter (Readiness Pack) → статус ready_for_review → письмо аналитику →
POST /s1/jobs/<id>/approve → Founder Readiness Report фаундеру.

Подключение в app.py:  from s1_intake.main import init_s1;  init_s1(app)
Если S1-переменные не заданы, тренажёр работает как раньше, а вебхук отвечает 503.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Flask, Response, current_app, g, jsonify, request

from .config import Settings, get_settings, load_methodology
from .dialogue import (DialogueError, add_r2_item, attach_file, build_session, expire_overdue, r2_candidates,
                       ready_for_gate, resume_r2)
from . import exit_intro, links
from .vertical import classify_vertical
from .dialogue import answer as dialogue_answer
from .evaluator import evaluate
from .formatter import generate_pack, html_to_pdf
from .llm import LLMClient
from .models import ParsedDoc, Submission
from .notify import ping_webhook, send_email, smtp_configured
from .parsers import fetch_and_parse
from .store import ReadinessJobStore
from .tilda import is_test_ping, parse_payload

log = logging.getLogger("s1_intake")
bp = Blueprint("s1_intake", __name__, url_prefix="/s1")
MAX_BODY = 1_000_000
STALE_PROCESSING = timedelta(minutes=20)   # дольше любой реальной обработки


@dataclass
class S1State:
    settings: Settings
    methodology: dict
    store: ReadinessJobStore
    pool: ThreadPoolExecutor
    enabled: bool
    llm_factory: object
    http_client: object = None


def _state() -> S1State:
    return current_app.extensions["s1_intake"]


# ------------------------------------------------------------------ init ----
def init_s1(app: Flask, settings: Settings | None = None, llm_factory=None, http_client=None) -> S1State:
    s = settings or get_settings()
    m = load_methodology(str(s.methodology_path))
    problems = s.problems()
    for p in problems:
        log.warning("S1 CONFIG: %s", p)
    state = S1State(
        settings=s, methodology=m,
        store=ReadinessJobStore(s.database_url, s.retention_days),
        pool=ThreadPoolExecutor(max_workers=2, thread_name_prefix="s1-job"),
        enabled=not problems or s.llm_provider == "mock",
        llm_factory=llm_factory or (lambda: LLMClient(s)),
        http_client=http_client,
    )
    app.extensions["s1_intake"] = state
    app.register_blueprint(bp)
    if state.enabled:  # рестарт посреди обработки — доделать (с атомарным claim)
        cutoff = _iso(datetime.now(timezone.utc) - STALE_PROCESSING)
        for job_id in state.store.recoverable(cutoff):
            state.pool.submit(_process_job, app, job_id, cutoff)
    return state


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- worker ----
def _process_job(app: Flask, job_id: str, stale_before: str | None = None) -> None:
    with app.app_context():
        st = _state()
        s, m, store = st.settings, st.methodology, st.store
        if not store.claim(job_id, stale_before):
            return  # уже обрабатывается другим воркером или завершена
        try:
            job = store.get(job_id)
            sub = Submission.model_validate(job["data"]["submission"])
            # HQ-020: вертикаль LS/HT до оценки (решение PM 11.10)
            vert, why = classify_vertical(sub, m)
            override = (job["data"].get("vertical_decision") or {}).get("decision")
            if override:
                vert = override
            if vert == "out":
                store.update(job_id, status="out_of_scope", data_patch={"vertical": {
                    "result": "out", "reason": why, "message": m["vertical_s1"]["out_of_scope_message"]}})
                return
            if vert == "manual_review":
                store.update(job_id, status="manual_review", data_patch={"vertical": {
                    "result": "manual_review", "reason": why}})
                return
            cached = job["data"].get("parsed_docs")
            if cached:  # rerun после правки промптов — не качаем файлы заново
                docs = [ParsedDoc.model_validate(d) for d in cached]
            else:
                docs = [fetch_and_parse(f, s, st.http_client) for f in sub.files]
                store.update(job_id, data_patch={"parsed_docs": [d.model_dump() for d in docs]})

            llm = st.llm_factory()
            signoffs = {so["slot"]: so for so in store.fact_signoffs(job_id)}  # HQ-035: подписи факта
            res = evaluate(job_id, sub, docs, llm, s, m, verified_slots=signoffs)
            pack = generate_pack(res, m, s.fund_name)
            pack["llm_calls"] = list(getattr(llm, "calls", []))  # TG-1: модель, temperature, хеш промпта
            prov = {"model": res.model, "code_sha": s.code_sha,  # A14: модель, хеш промпта, SHA кода
                    "prompt_sha256": sorted({c.get("prompt_sha256") for c in pack["llm_calls"] if c.get("prompt_sha256")})}
            pack["provenance"] = prov
            pack["readiness_pack"]["provenance"] = prov
            # HQ-020: сессия диалога раздела 1; пока стадия не подтверждена — в Human Gate не идёт
            scores_d = json.loads(res.scores.model_dump_json())
            session = build_session(scores_d, m, job["data"].get("dialogue"), sub.product_class)
            if not session["r2_items"] and not session["r2_not_requested"]:
                for cand in r2_candidates(scores_d, m):  # §4.1, лимит 5, лишнее — «не запрашивалось»
                    add_r2_item(session, cand["item"], cand["reason"])
            pack["dialogue"] = session
            for b in pack["pack_blocks"]:
                if b["n"] == 10:  # блок 10: не дослано и почему
                    b["items"] = [{"item": x["item"], "reason": x.get("close_reason") or x["state"]}
                                  for x in session["r2_items"] if x["state"] != "закрыт файлом"] + \
                                 list(session["r2_not_requested"])
            gate_ok, _ = ready_for_gate(session)
            store.update(job_id, data_patch=pack, status="ready_for_review" if gate_ok else "awaiting_founder",
                         verdict=res.scores.verdict, readiness=res.scores.readiness)
            # HQ-020 (решение PM): бесплатная часть уходит автоматически, без approve.
            # Канал доставки основателю брифом не задан (решение PM): часть готова сразу, без approve,
            # GET /s1/jobs/<id>/free-part; в вебхук уходят только метаданные.
            ping_webhook(s, {"event": "s1.free_part_ready", "job_id": job_id})
            log.info("S1 job %s ready: %s %s/100", job_id, res.scores.verdict, res.scores.readiness)

            ping_webhook(s, {"event": "s1.ready_for_review", "job_id": job_id,
                             "verdict": res.scores.verdict, "readiness": res.scores.readiness})
            _email_analysts(s, job_id, sub, res.scores, pack)
            # TG-1: автоотправки фаундеру нет. Только POST /s1/jobs/<id>/approve с персональной подписью.
        except Exception as e:  # noqa: BLE001
            log.exception("S1 job %s failed", job_id)
            store.update(job_id, status="failed", error=f"{e.__class__.__name__}: {str(e)[:500]}")
            ping_webhook(s, {"event": "s1.job_failed", "job_id": job_id, "error": e.__class__.__name__})


def _email_analysts(s: Settings, job_id: str, sub: Submission, scores, pack: dict) -> None:
    if not (s.analyst_emails and smtp_configured(s)):
        return
    atts = []
    for name, key in (("analyst_brief.pdf", "analyst_brief_html"), ("founder_report_draft.pdf", "founder_report_html")):
        pdf = html_to_pdf(pack[key])
        if pdf:
            atts.append((name, pdf))
    try:
        send_email(s, s.analyst_emails, f"[S1] {scores.verdict} · {scores.readiness}/100 · {sub.project_name}",
                   pack["analyst_brief_html"], atts)
    except Exception:  # noqa: BLE001 — уведомление не должно валить заявку
        log.exception("S1 analyst email failed for %s", job_id)


def _report_sha256(job: dict) -> str:
    return hashlib.sha256(job["data"].get("founder_report_html", "").encode()).hexdigest()


def _deliver_founder_report(job_id: str, approver: str) -> tuple[bool, str, int]:
    """Только из approve. Пишет журнал подписи. Повторная отправка того же отчёта блокируется."""
    st = _state()
    s, store, job = st.settings, st.store, st.store.get(job_id)
    if not job:
        return False, "not found", 404
    digest = _report_sha256(job)
    if job["status"] == "sent" or store.was_sent(job_id, digest):
        store.log_approval(job_id, approver, digest, "blocked_duplicate")
        return False, "report already sent", 409
    gate_ok, why = ready_for_gate(job["data"].get("dialogue") or {})
    if not gate_ok:  # HQ-020: стадия не подтверждена — полный отчёт в Human Gate не идёт
        store.log_approval(job_id, approver, digest, "blocked_stage", why)
        return False, why, 409
    if (job["data"].get("fund_call") or {}).get("status") != "call_done":  # A12 (решение PM 11.10)
        store.log_approval(job_id, approver, digest, "blocked_call", "нет записи итога созвона с фондом")
        return False, "созвон с фондом не записан: нужен статус call_done с итогом", 409
    if job["status"] != "ready_for_review":
        return False, f"job status is {job['status']}", 409
    if not job["founder_email"]:
        store.log_approval(job_id, approver, digest, "failed", "no founder email")
        return False, "no founder email", 409
    if not smtp_configured(s):
        store.log_approval(job_id, approver, digest, "failed", "SMTP not configured")
        return False, "SMTP not configured", 409
    if not store.begin_send(job_id):  # параллельный approve уже отправляет
        store.log_approval(job_id, approver, digest, "blocked_duplicate", "concurrent approve")
        return False, "report already being sent", 409
    try:
        store.log_approval(job_id, approver, digest, "sent")  # UNIQUE (job, sha256) WHERE sent
    except Exception:  # noqa: BLE001
        store.update(job_id, status="ready_for_review")
        return False, "report already sent", 409
    html = job["data"]["founder_report_html"]
    pdf = html_to_pdf(html)
    try:
        send_email(s, [job["founder_email"]], f"{s.fund_name}: ваш Founder Readiness Report", html,
                   [("founder_readiness_report.pdf", pdf)] if pdf else [])
    except Exception as e:  # noqa: BLE001 — запись «sent» остаётся: лучше не отправить, чем отправить дважды
        store.update(job_id, status="failed", error=f"send failed: {e.__class__.__name__}")
        store.log_approval(job_id, approver, digest, "send_error", e.__class__.__name__)
        return False, "send failed", 502
    # блок 11 Pack: «проверил, дата» — кто подписал, когда, хеш отчёта (из журнала s1_approvals)
    rec = next((a for a in reversed(store.approvals(job_id)) if a["outcome"] == "sent"), None)
    blocks = job["data"].get("pack_blocks") or []
    for b in blocks:
        if b["n"] == 11 and rec:
            b["signed"] = {"by": rec["approver"], "at": rec["approved_at"], "report_sha256": rec["report_sha256"]}
    patch = {"pack_blocks": blocks}
    if not job["data"].get("fund_timer"):  # HQ-028: окно фонда 21 день стартует с подписи на Human Gate
        patch["fund_timer"] = exit_intro.start_fund_window(st.methodology["working"]["hq028"], approver)
    store.update(job_id, status="sent", data_patch=patch)
    return True, "sent", 200


def _approver() -> str | None:
    """Персональная подпись: Bearer-токен конкретного человека из S1_APPROVERS."""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    for who, tok in _state().settings.approvers.items():
        if token and hmac.compare_digest(token, tok):
            return who
    return None


def _s3_viewer() -> str | None:
    """HQ-062: роль S3 — персональный подписант из S3_OWNERS (пусто → любой подписант S1).
    Временная проверка до auth.current_user() из #61 (MOS-1): заменить только эту функцию."""
    who = _approver()
    owners = {x.strip() for x in os.environ.get("S3_OWNERS", "").split(",") if x.strip()}
    return who if who and (not owners or who in owners) else None


# ------------------------------------------------------------------ auth ----
def _admin_ok() -> bool:
    if g.get("rbac_session_ok"):  # MOS-1 (#52): сессия прошла require_role (роль + scope), маршрут её роль допускает
        return True
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    expected = _state().settings.admin_token
    return bool(expected) and len(expected) >= 24 and hmac.compare_digest(token, expected)


def _webhook_key_ok(payload: dict) -> bool:
    s = _state().settings
    if not s.tilda_webhook_key:
        return False
    got = request.headers.get(s.tilda_webhook_key_name) or str(payload.get(s.tilda_webhook_key_name, ""))
    return hmac.compare_digest(got, s.tilda_webhook_key)


def _err(msg: str, code: int):
    return jsonify({"error": msg}), code


# ---------------------------------------------------------------- routes ----
@bp.post("/webhooks/tilda")
def tilda_webhook():
    st = _state()
    if (request.content_length or 0) > MAX_BODY:
        return _err("payload too large", 413)
    if request.is_json:
        payload = request.get_json(silent=True)
    else:  # Tilda по умолчанию шлёт form-urlencoded
        payload = {k: (v if len(v) > 1 else v[0]) for k, v in request.form.lists()}
    if not isinstance(payload, dict):
        return _err("bad payload", 400)

    if is_test_ping(payload):  # проверка подключения из админки Tilda
        return Response("ok", mimetype="text/plain")
    if not st.enabled:
        return _err("s1 intake disabled: configuration incomplete", 503)
    if not _webhook_key_ok(payload):
        return _err("forbidden", 403)

    payload.pop(st.settings.tilda_webhook_key_name, None)  # ключ не сохраняем
    sub = parse_payload(payload, st.methodology)
    job_id, created = st.store.create_or_get(
        sub.submission_id, sub.project_name, sub.founder_email,
        {"submission": json.loads(sub.model_dump_json())},
    )
    if created:
        if sub.consent:
            st.pool.submit(_process_job, current_app._get_current_object(), job_id)
        else:
            st.store.update(job_id, status="rejected", error="no consent to data processing")
    return Response("ok", mimetype="text/plain")  # Tilda ждёт быстрый 200


@bp.get("/jobs")
def list_jobs():
    limit = min(int(request.args.get("limit", 50)), 500)
    if _admin_ok():  # HQ-062: админ видит S1, записи S3 — нет
        return jsonify(_state().store.list(limit, s3="exclude"))
    if _s3_viewer():  # роль S3 видит только S3
        return jsonify(_state().store.list(limit, s3="only"))
    return _err("forbidden" if _approver() else "unauthorized", 403 if _approver() else 401)


@bp.get("/jobs/<job_id>")
def get_job(job_id):
    if not _admin_ok():
        return _err("unauthorized", 401)
    job = _state().store.get(job_id)
    if not job or job["data"].get("s3"):  # S3 — только через /s3/* (HQ-062)
        return _err("not found", 404)
    data = job.pop("data")
    brief = (data.get("readiness_pack") or {}).get("internal_analyst_brief") or {}
    if brief:
        job["summary"] = {
            "verdict": brief["scores"]["verdict"], "readiness": brief["scores"]["readiness"],
            "gates": brief["scores"]["verdict_gates"], "critical_gaps": brief["scores"]["critical_gaps"],
            "executive_summary": brief["synthesis"]["executive_summary"],
            "revaluation_condition": brief["synthesis"]["revaluation_condition"],
            "modules": brief["routing"]["modules"],
        }
    return jsonify(job)


_ARTIFACTS = {
    "founder-report.html": ("founder_report_html", "html"),
    "founder-report.pdf": ("founder_report_html", "pdf"),
    "analyst-brief.html": ("analyst_brief_html", "html"),
    "analyst-brief.pdf": ("analyst_brief_html", "pdf"),
    "readiness-pack.json": ("readiness_pack", "json"),
}


@bp.get("/jobs/<job_id>/<artifact>")
def get_artifact(job_id, artifact):
    if not _admin_ok():
        return _err("unauthorized", 401)
    if artifact not in _ARTIFACTS:
        return _err("not found", 404)
    job = _state().store.get(job_id)
    key, kind = _ARTIFACTS[artifact]
    if not job or job["data"].get("s3") or key not in job["data"]:
        return _err("not found", 404)
    value = job["data"][key]
    if kind == "json":
        return jsonify(value)
    if kind == "html":
        return Response(value, mimetype="text/html")
    pdf = html_to_pdf(value)
    if not pdf:
        return _err("PDF renderer unavailable on this host; use the .html artifact", 501)
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{artifact}"'})


@bp.post("/jobs/<job_id>/approve")
def approve(job_id):
    who = _approver()
    if not who:  # общий admin-токен подписью не является
        return _err("personal approver signature required", 403 if _admin_ok() else 401)
    ok, msg, code = _deliver_founder_report(job_id, who)
    return (jsonify({"ok": True, "status": "sent", "approved_by": who}) if ok else _err(msg, code))


@bp.get("/jobs/<job_id>/approvals")
def approvals(job_id):
    if not (_admin_ok() or _approver()):
        return _err("unauthorized", 401)
    return jsonify(_state().store.approvals(job_id))


@bp.get("/jobs/<job_id>/dialogue")
def get_dialogue(job_id):
    if not _admin_ok():
        return _err("unauthorized", 401)
    job = _state().store.get(job_id)
    if not job or "dialogue" not in job["data"]:
        return _err("not found", 404)
    return jsonify(job["data"]["dialogue"])


@bp.get("/jobs/<job_id>/free-part")
def get_free_part(job_id):
    """Бесплатная часть S1 — без approve (решение PM для HQ-020)."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    job = _state().store.get(job_id)
    if not job or "free_part" not in job["data"]:
        return _err("not found", 404)
    return jsonify(job["data"]["free_part"])


def _apply_stage(job_id: str, body: dict):
    st = _state()
    rule = st.methodology["ls_stage_rule"]
    known = set(rule["pre_registration_stages"]) | set(rule["post_registration_stages"])
    stage = str(body.get("stage") or "").strip().lower()
    if stage not in known:
        return _err(f"stage must be one of {sorted(known)}", 400)
    job = st.store.get(job_id)
    if not job or "dialogue" not in job["data"]:
        return _err("not found", 404)
    session = job["data"]["dialogue"]
    try:
        dialogue_answer(session, "stage", str(body.get("answer") or ""), stage)
    except DialogueError as e:
        return _err(str(e), 409)
    sub = dict(job["data"]["submission"], ls_stage=stage)
    st.store.update(job_id, data_patch={"dialogue": session, "submission": sub})
    return _requeue(job_id) or jsonify({"ok": True, "stage": stage})


@bp.post("/jobs/<job_id>/dialogue/stage")
def answer_stage(job_id):
    """Ответ основателя на первый вопрос раздела 1 (стадия) → пересчёт тем же ядром yaml + s1_intake."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    return _apply_stage(job_id, request.get_json(silent=True) or {})


def _requeue(job_id: str):
    st = _state()
    if not st.store.reset_for_rerun(job_id):
        return _err("job still in progress", 409)
    st.pool.submit(_process_job, current_app._get_current_object(), job_id)
    return None


def _apply_answer(job_id: str, body: dict):
    st = _state()
    qid, text = str(body.get("qid") or ""), str(body.get("answer") or "").strip()
    job = st.store.get(job_id)
    if not job or "dialogue" not in job["data"]:
        return _err("not found", 404)
    session = job["data"]["dialogue"]
    q = next((x for r in session["queue"].values() for x in r if x["qid"] == qid), None)
    if not q or not text or qid == "stage":
        return _err("unknown question or empty answer (stage — отдельно)", 400)
    if session.get("stage_pending"):
        return _err("сначала вопрос о стадии (Q0.4)", 409)
    dialogue_answer(session, qid, text)
    sub = dict(job["data"]["submission"])
    sub["answers"] = list(sub.get("answers", [])) + [
        {"field": f"dlg_{qid}", "block": "dialogue", "label": q["question"], "value": text}]
    st.store.update(job_id, data_patch={"dialogue": session, "submission": sub})
    return _requeue(job_id) or jsonify({"ok": True})


@bp.post("/jobs/<job_id>/dialogue/answer")
def dialogue_answer_text(job_id):
    """Ответ основателя вносит оператор (пилот). Пересчёт тем же ядром; без файла — не выше E1."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    return _apply_answer(job_id, request.get_json(silent=True) or {})


@bp.post("/jobs/<job_id>/dialogue/r2/expire")
def dialogue_r2_expire(job_id):
    """Срок досылки (рабочее: 14 дней): просроченные пункты → «истёк»."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    st = _state()
    job = st.store.get(job_id)
    if not job or "dialogue" not in job["data"]:
        return _err("not found", 404)
    session = job["data"]["dialogue"]
    expired = expire_overdue(session)
    st.store.update(job_id, data_patch={"dialogue": session})
    return jsonify({"ok": True, "expired": expired})


@bp.post("/jobs/<job_id>/dialogue/r2/resume")
def dialogue_r2_resume(job_id):
    if not _admin_ok():
        return _err("unauthorized", 401)
    st = _state()
    job = st.store.get(job_id)
    if not job or "dialogue" not in job["data"]:
        return _err("not found", 404)
    session = job["data"]["dialogue"]
    try:
        resume_r2(session, str((request.get_json(silent=True) or {}).get("item") or ""))
    except DialogueError as e:
        return _err(str(e), 409)
    st.store.update(job_id, data_patch={"dialogue": session})
    return jsonify({"ok": True})


@bp.post("/jobs/<job_id>/dialogue/r2/file")
def dialogue_r2_file(job_id):
    """Р2: файл к пункту «дошлю позже» → пересчёт после каждого файла (TEC-10)."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    st = _state()
    body = request.get_json(silent=True) or {}
    item, url = str(body.get("item") or ""), str(body.get("url") or "")
    job = st.store.get(job_id)
    if not job or "dialogue" not in job["data"] or not url:
        return _err("not found or no url", 404 if not job else 400)
    session = job["data"]["dialogue"]
    try:
        attach_file(session, item, url)
    except DialogueError as e:
        return _err(str(e), 409)
    sub = dict(job["data"]["submission"])
    sub["files"] = list(sub.get("files", [])) + [{"kind": "evidence", "url": url}]
    st.store.update(job_id, data_patch={"dialogue": session, "submission": sub, "parsed_docs": None})
    return _requeue(job_id) or jsonify({"ok": True, "rescoring": True})


@bp.post("/jobs/<job_id>/call")
def fund_call(job_id):
    """A12: итог созвона с фондом (без календаря). Без него approve не проходит."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    st = _state()
    body = request.get_json(silent=True) or {}
    outcome = str(body.get("outcome") or "").strip()
    if not outcome:
        return _err("outcome required", 400)
    job = st.store.get(job_id)
    if not job:
        return _err("not found", 404)
    st.store.update(job_id, data_patch={"fund_call": {
        "status": "call_done", "outcome": outcome, "at": _iso(datetime.now(timezone.utc))}})
    return jsonify({"ok": True, "status": "call_done"})


@bp.post("/jobs/<job_id>/vertical")
def vertical_decision(job_id):
    """Ручная проверка пограничного случая: человек решает in/out с причиной."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    st = _state()
    body = request.get_json(silent=True) or {}
    decision, reason = body.get("decision"), str(body.get("reason") or "").strip()
    if decision not in ("in", "out") or not reason:
        return _err("decision in|out and reason required", 400)
    job = st.store.get(job_id)
    if not job or job["status"] != "manual_review":
        return _err("job not in manual_review", 409)
    st.store.update(job_id, data_patch={"vertical_decision": {"decision": decision, "reason": reason}})
    return _requeue(job_id) or jsonify({"ok": True})


def _free_token(job_id: str, exp: int, purpose: str = "free") -> str:
    return links.sign(_state().settings.free_link_secret, purpose, job_id, exp)


def _link_check(ident: str, purpose: str):
    """HQ-028: единая проверка ссылки (подделка 403, просрочка 410)."""
    code, why = links.verify(_state().settings.free_link_secret, purpose, ident,
                             request.args.get("exp", "0"), request.args.get("sig", ""))
    return None if code == 200 else _err(why, code)


def _issue_link(ident: str, purpose: str, prefix: str):
    s = _state().settings
    if not s.free_link_secret or len(s.free_link_secret) < 24:
        return _err("link secret not configured (S1_FREE_LINK_SECRET)", 503)
    return jsonify(links.make(s.free_link_secret, purpose, ident, s.free_link_ttl_s, prefix))


@bp.post("/jobs/<job_id>/dialogue-link")
def dialogue_link(job_id):
    """PM 11.10: основатель видит вопросы Р1/Р2 на своей странице диалога по ссылке со сроком; писем нет."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    return _issue_link(job_id, "dialogue", "/s1/dialogue")


@bp.get("/dialogue/<job_id>")
def founder_dialogue(job_id):
    bad = _link_check(job_id, "dialogue")
    if bad:
        return bad
    job = _state().store.get(job_id)
    if not job or "dialogue" not in job["data"]:
        return _err("not found", 404)
    s = job["data"]["dialogue"]
    done = {a["qid"] for a in s["answers"]}
    rounds = ["Р0"] + (["Р1"] if s["round"] != "Р0" or not s.get("stage_pending") else [])
    questions = [{"qid": q["qid"], "question": q["question"]} for r in rounds for q in s["queue"].get(r, [])
                 if q["qid"] not in done]
    if s.get("stage_pending"):
        questions = [q for q in questions if q["qid"] == "stage"]
    items = [{"item": x["item"], "state": x["state"], "due": x.get("due")} for x in s["r2_items"]]
    return jsonify({"round": s["round"], "questions": questions, "r2_items": items})


@bp.post("/dialogue/<job_id>")
def founder_dialogue_answer(job_id):
    bad = _link_check(job_id, "dialogue")
    if bad:
        return bad
    body = request.get_json(silent=True) or {}
    return _apply_stage(job_id, body) if body.get("qid") == "stage" else _apply_answer(job_id, body)


@bp.post("/jobs/<job_id>/free-link")
def free_link(job_id):
    """Ограниченная по времени ссылка на бесплатную часть для страницы результата / кабинета."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    return _issue_link(job_id, "free", "/s1/free")


@bp.get("/free/<job_id>")
def free_public(job_id):
    bad = _link_check(job_id, "free")
    if bad:
        return bad
    job = _state().store.get(job_id)
    if not job or "free_part" not in job["data"]:
        return _err("not found", 404)
    fp = job["data"]["free_part"]
    return jsonify({k: fp[k] for k in ("label", "score", "scale", "stage", "gaps")})


# ================================================================ HQ-028 ====
def _hq028() -> dict:
    return _state().methodology["working"]["hq028"]


def _job_or_404(job_id: str):
    job = _state().store.get(job_id)
    return job


@bp.post("/cabinet/<job_id>/consent")
def cabinet_consent(job_id):
    """Кабинет основателя (та же ссылка со сроком, что страница диалога): дать согласие на передачу."""
    bad = _link_check(job_id, "dialogue")
    if bad:
        return bad
    body = request.get_json(silent=True) or {}
    scope, recipient = body.get("scope"), str(body.get("recipient") or "").strip()
    if scope not in ("teaser", "report") or not recipient:
        return _err("scope teaser|report and recipient required", 400)
    if not _job_or_404(job_id):
        return _err("not found", 404)
    cid = _state().store.grant_consent(job_id, "founder", scope, recipient)
    return jsonify({"ok": True, "consent_id": cid})


@bp.post("/cabinet/<job_id>/revoke")
def cabinet_revoke(job_id):
    """Кнопка «отозвать»: все (или для одного получателя) согласия отзываются; ссылки инвесторам
    перестают открываться сразу — проверка журнала идёт при каждом открытии."""
    bad = _link_check(job_id, "dialogue")
    if bad:
        return bad
    recipient = (request.get_json(silent=True) or {}).get("recipient")
    n = _state().store.revoke_consents(job_id, "founder", recipient)
    return jsonify({"ok": True, "revoked": n})


@bp.get("/cabinet/<job_id>/consents")
def cabinet_consents(job_id):
    bad = _link_check(job_id, "dialogue")
    if bad:
        return bad
    return jsonify(_state().store.consents(job_id))


@bp.get("/jobs/<job_id>/consents")
def admin_consents(job_id):
    if not _admin_ok():
        return _err("unauthorized", 401)
    return jsonify(_state().store.consents(job_id))


@bp.get("/jobs/<job_id>/fund-timer")
def fund_timer_status(job_id):
    """Срабатывание таймера = только смена статуса (fund_window: expired). Ничего не отправляется."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    job = _job_or_404(job_id)
    if not job:
        return _err("not found", 404)
    if not job["data"].get("fund_timer"):
        return jsonify({"state": "not_started"})
    return jsonify(exit_intro.fire_if_due(_state().store, job, _hq028()))


@bp.post("/fund-timers/sweep")
def fund_timers_sweep():
    """Для планировщика: проверить все окна; истёкшие → статус + задача подписанту. Ничего не отправляется."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    st, fired = _state(), []
    for jid in st.store.timer_jobs():
        job = st.store.get(jid)
        if job and job["data"].get("fund_timer", {}).get("state") != "expired":
            if exit_intro.fire_if_due(st.store, job, _hq028())["state"] == "expired":
                fired.append(jid)
    return jsonify({"fired": fired})


@bp.get("/tasks")
def tasks():
    if not _admin_ok():
        return _err("unauthorized", 401)
    return jsonify(_state().store.tasks(request.args.get("assignee")))


@bp.post("/jobs/<job_id>/teaser")
def teaser_build(job_id):
    if not _admin_ok():
        return _err("unauthorized", 401)
    job = _job_or_404(job_id)
    if not job or "readiness_pack" not in job["data"]:
        return _err("not found", 404)
    cfg = _hq028()
    t = exit_intro.build_teaser(job, cfg)
    overrides = (request.get_json(silent=True) or {}).get("edit") or {}
    for k in ("area", "stage", "raise"):  # человек может поправить текст до подписи
        if k in overrides:
            t[k] = str(overrides[k])
    for k in ("strengths", "risks"):
        if k in overrides:
            t[k] = [{"text": str(x.get("text", "")), "status": x.get("status") if x.get("status") in ("факт", "заявление с документом", "заявление")
                     else "заявление"} for x in overrides[k]][:_hq028()["teaser_items"]]
    t["deid_violations"] = exit_intro.deid_check(t, job, cfg)
    _state().store.update(job_id, data_patch={"teaser": t})
    return jsonify(t)


@bp.post("/jobs/<job_id>/teaser/sign")
def teaser_sign(job_id):
    """Подпись человека перед любой передачей тизера; решение фонда вписывает человек."""
    who = _approver()
    if not who:
        return _err("personal approver signature required", 403 if _admin_ok() else 401)
    job = _job_or_404(job_id)
    t = (job or {}).get("data", {}).get("teaser")
    if not t:
        return _err("teaser not built", 404)
    decision = str((request.get_json(silent=True) or {}).get("fund_decision") or "").strip()
    if decision not in _hq028()["fund_decisions"]:  # только из списка в конфиге, без свободного текста
        return _err(f"fund_decision must be one of {_hq028()['fund_decisions']}", 400)
    t["fund_decision"] = decision
    t["deid_violations"] = exit_intro.deid_check(t, job, _hq028())
    if t["deid_violations"]:
        _state().store.update(job_id, data_patch={"teaser": t})
        return _err("teaser failed de-identification: " + "; ".join(t["deid_violations"]), 409)
    t.update(signed_by=who, signed_at=_iso(datetime.now(timezone.utc)))
    _state().store.update(job_id, data_patch={"teaser": t})
    return jsonify({"ok": True, "signed_by": who})


@bp.post("/jobs/<job_id>/transfers")
def transfer_request(job_id):
    """Заявка на передачу (тизер / отчёт) получателю, включая Rounds. Только запись; дальше — G-4 владельца."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    body = request.get_json(silent=True) or {}
    kind, recipient = body.get("kind"), str(body.get("recipient") or "").strip()
    if kind not in ("teaser", "report") or not recipient:
        return _err("kind teaser|report and recipient required", 400)
    job = _job_or_404(job_id)
    if not job:
        return _err("not found", 404)
    st = _state()
    blockers = exit_intro.transfer_blockers(job, st.store, kind, recipient, _hq028())
    if blockers:
        tid = st.store.add_transfer(job_id, kind, recipient, "blocked", "admin", "; ".join(blockers))
        return jsonify({"ok": False, "transfer_id": tid, "blocked": blockers}), 409
    tid = st.store.add_transfer(job_id, kind, recipient, "awaiting_g4", "admin")
    return jsonify({"ok": True, "transfer_id": tid, "status": "awaiting_g4"})


@bp.post("/transfers/<tid>/g4")
def transfer_g4(tid):
    """G-4: решение владельца (персональная подпись). Перед выпуском — повторная проверка журнала.
    Выпуск = ссылка со сроком для владельца; сама система ничего не отправляет."""
    who = _approver()
    if not who:
        return _err("personal approver signature required", 403 if _admin_ok() else 401)
    st = _state()
    tr = st.store.transfer(tid)
    if not tr:
        return _err("not found", 404)
    if tr["status"] != "awaiting_g4":
        return _err(f"transfer status is {tr['status']}", 409)
    if (request.get_json(silent=True) or {}).get("decision") != "yes":
        st.store.set_transfer(tid, "declined", who, "G-4: нет")
        return jsonify({"ok": True, "status": "declined"})
    job = st.store.get(tr["job_id"])
    blockers = exit_intro.transfer_blockers(job, st.store, tr["kind"], tr["recipient"], _hq028())
    if blockers:
        st.store.set_transfer(tid, "blocked", who, "; ".join(blockers))
        return _err("; ".join(blockers), 409)
    st.store.set_transfer(tid, "released", who, "G-4: да")
    link = _issue_link(tid, "shared", "/s1/shared")
    return link


@bp.get("/shared/<tid>")
def shared_open(tid):
    """Открытие ссылки получателем: срок, затем журнал согласий при КАЖДОМ открытии."""
    bad = _link_check(tid, "shared")
    if bad:
        return bad
    st = _state()
    tr = st.store.transfer(tid)
    if not tr or tr["status"] != "released":
        return _err("not available", 404)
    job = st.store.get(tr["job_id"])
    blockers = exit_intro.transfer_blockers(job, st.store, tr["kind"], tr["recipient"], _hq028())
    if blockers:
        return _err("доступ закрыт: " + "; ".join(blockers), 403)
    disc = _hq028()["disclaimer"]
    if tr["kind"] == "teaser":
        t = job["data"]["teaser"]
        return jsonify({k: t[k] for k in ("area", "stage", "raise", "strengths", "risks", "fund_decision", "disclaimer")})
    html = job["data"]["founder_report_html"]
    return Response(f'<p class="disclaimer"><b>{disc}</b></p>' + html, mimetype="text/html")


@bp.post("/jobs/<job_id>/slots/verify")
def verify_slot(job_id):
    """HQ-035 / канон Р-5: FACT ставит только человек с ролью из списка подписантов — пакетом на слот,
    с основанием. Подпись пишется в журнал s1_fact_signoffs, затем пересчёт."""
    who = _approver()
    if not who:
        return _err("personal signature required", 403 if _admin_ok() else 401)
    st = _state()
    role = st.settings.approver_roles.get(who)
    if role not in st.methodology["working"]["fact_signoff"]["signer_roles"]:
        return _err("signer role not allowed to verify facts", 403)
    body = request.get_json(silent=True) or {}
    slot, basis = str(body.get("slot") or ""), str(body.get("basis") or "").strip()
    job = st.store.get(job_id)
    if not job or "readiness_pack" not in job["data"]:
        return _err("not found", 404)
    brief = job["data"]["readiness_pack"]["internal_analyst_brief"]
    items = {it["key"]: it for b in brief["scores"]["blocks"] for it in b["items"]}
    if slot not in items or not basis:
        return _err("slot from checklist and basis required", 400)
    if items[slot]["status"] != "FACT_CANDIDATE":
        return _err("only FACT_CANDIDATE slot (document ≥ fact_min_level) can be verified", 409)
    rec = st.store.log_fact_signoff(job_id, slot, who, role, basis)
    return _requeue(job_id) or jsonify({"ok": True, **rec})


@bp.get("/jobs/<job_id>/fact-signoffs")
def fact_signoffs(job_id):
    if not _admin_ok():
        return _err("unauthorized", 401)
    return jsonify(_state().store.fact_signoffs(job_id))


@bp.post("/jobs/<job_id>/rerun")
def rerun(job_id):
    if not _admin_ok():
        return _err("unauthorized", 401)
    st = _state()
    if not st.store.reset_for_rerun(job_id):
        return _err("job not found or still in progress", 409)
    st.pool.submit(_process_job, current_app._get_current_object(), job_id)
    return jsonify({"ok": True})
