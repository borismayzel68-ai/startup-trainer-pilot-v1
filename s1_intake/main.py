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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Flask, Response, current_app, jsonify, request

from .config import Settings, get_settings, load_methodology
from .dialogue import DialogueError, build_session, ready_for_gate
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
            cached = job["data"].get("parsed_docs")
            if cached:  # rerun после правки промптов — не качаем файлы заново
                docs = [ParsedDoc.model_validate(d) for d in cached]
            else:
                docs = [fetch_and_parse(f, s, st.http_client) for f in sub.files]
                store.update(job_id, data_patch={"parsed_docs": [d.model_dump() for d in docs]})

            llm = st.llm_factory()
            res = evaluate(job_id, sub, docs, llm, s, m)
            pack = generate_pack(res, m, s.fund_name)
            pack["llm_calls"] = list(getattr(llm, "calls", []))  # TG-1: модель, temperature, хеш промпта
            # HQ-020: сессия диалога раздела 1; пока стадия не подтверждена — в Human Gate не идёт
            session = build_session(json.loads(res.scores.model_dump_json()), m, job["data"].get("dialogue"))
            pack["dialogue"] = session
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
    store.update(job_id, status="sent")
    return True, "sent", 200


def _approver() -> str | None:
    """Персональная подпись: Bearer-токен конкретного человека из S1_APPROVERS."""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    for who, tok in _state().settings.approvers.items():
        if token and hmac.compare_digest(token, tok):
            return who
    return None


# ------------------------------------------------------------------ auth ----
def _admin_ok() -> bool:
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
    if not _admin_ok():
        return _err("unauthorized", 401)
    limit = min(int(request.args.get("limit", 50)), 500)
    return jsonify(_state().store.list(limit))


@bp.get("/jobs/<job_id>")
def get_job(job_id):
    if not _admin_ok():
        return _err("unauthorized", 401)
    job = _state().store.get(job_id)
    if not job:
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
    if not job or key not in job["data"]:
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


@bp.post("/jobs/<job_id>/dialogue/stage")
def answer_stage(job_id):
    """Ответ основателя на первый вопрос раздела 1 (стадия) → пересчёт тем же ядром yaml + s1_intake."""
    if not _admin_ok():
        return _err("unauthorized", 401)
    st = _state()
    body = request.get_json(silent=True) or {}
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
    if not st.store.reset_for_rerun(job_id):
        return _err("job still in progress", 409)
    st.pool.submit(_process_job, current_app._get_current_object(), job_id)
    return jsonify({"ok": True, "stage": stage})


@bp.post("/jobs/<job_id>/rerun")
def rerun(job_id):
    if not _admin_ok():
        return _err("unauthorized", 401)
    st = _state()
    if not st.store.reset_for_rerun(job_id):
        return _err("job not found or still in progress", 409)
    st.pool.submit(_process_job, current_app._get_current_object(), job_id)
    return jsonify({"ok": True})
