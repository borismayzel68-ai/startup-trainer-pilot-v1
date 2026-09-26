import json
import re
import uuid
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlparse

EVENT_TYPE = "TRAINER_COMPLETED"
REQUIRED_PAYLOAD_FIELDS = (
    "event_id",
    "idempotency_key",
    "event_type",
    "project_id",
    "project_name",
    "founder_name",
    "founder_email",
    "jurisdiction",
    "product_class",
    "session_id",
    "trainer_version",
    "mode",
    "started_at",
    "completed_at",
    "questions_answers",
    "evidence_delta",
    "founder_report",
    "internal_delta",
    "missing_documents",
    "next_meeting_questions",
    "privacy_flags",
    "run_manifest",
)

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/+\-]{1,160}$")


def _safe_token(value, default=None):
    value = str(value or "")
    return value if _SAFE_TOKEN.fullmatch(value) else default


def _safe_tokens(values):
    return [
        token
        for token in (_safe_token(value) for value in (values or []))
        if token is not None
    ]


def _minimal_questions_answers(rows):
    safe_rows = []
    for item in rows or []:
        analysis = item.get("analysis") or {}
        safe_rows.append(
            {
                "module": _safe_token(item.get("module")),
                "slot": _safe_token(item.get("slot")),
                "status": _safe_token(analysis.get("status"), "unknown"),
                "at": _safe_token(item.get("at")),
            }
        )
    return safe_rows


def _evidence_delta(questions_answers):
    return [
        {
            "module": item.get("module"),
            "slot": item.get("slot"),
            "status": item.get("status"),
            "source": "founder_answer",
            "at": item.get("at"),
        }
        for item in questions_answers
    ]


def _safe_deck_manifest(session):
    deck = (session.get("input_manifest") or {}).get("deck") or {}
    result = {}
    if "present" in deck:
        result["present"] = bool(deck["present"])
    if isinstance(deck.get("size_bytes"), int) and deck["size_bytes"] >= 0:
        result["size_bytes"] = deck["size_bytes"]
    for key in ("mime_type", "sha256"):
        value = _safe_token(deck.get(key))
        if value is not None:
            result[key] = value
    return result


def build_completion_payload(session, trainer_version, completed_at):
    event_id = session.get("event_id") or str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"trainer-completed:{session['id']}",
        )
    )
    idempotency_key = (
        session.get("idempotency_key")
        or f"trainer-completed:{session['id']}"
    )
    session["event_id"] = event_id
    session["idempotency_key"] = idempotency_key
    session["completed_at"] = session.get("completed_at") or completed_at

    raw_questions_answers = session.get("qa") or []
    questions_answers = _minimal_questions_answers(raw_questions_answers)
    raw_report = session.get("report") or {}
    missing_items = raw_report.get("missing") or []
    next_test_items = raw_report.get("next_tests") or []
    if not isinstance(missing_items, list):
        missing_items = []
    if not isinstance(next_test_items, list):
        next_test_items = []
    coverage = session.get("coverage") or {}
    founder_report = {
        "recommendation": _safe_token(
            raw_report.get("recommendation"), "unknown"
        ),
        "missing_document_count": len(missing_items),
        "next_test_count": len(next_test_items),
    }
    payload = {
        "event_id": event_id,
        "idempotency_key": idempotency_key,
        "event_type": EVENT_TYPE,
        "project_id": session["project_id"],
        "project_name": session["project_name"],
        "founder_name": session["founder_name"],
        "founder_email": session["founder_email"],
        "jurisdiction": session["jurisdiction"],
        "product_class": session["product_class"],
        "session_id": session["id"],
        "trainer_version": trainer_version,
        "mode": session["mode"],
        "started_at": session["created_at"],
        "completed_at": session["completed_at"],
        "questions_answers": questions_answers,
        "evidence_delta": _evidence_delta(questions_answers),
        "founder_report": founder_report,
        "internal_delta": {
            "coverage_slot_count": (
                len(coverage) if isinstance(coverage, dict) else 0
            ),
            "stop_reason": _safe_token(
                session.get("stop_reason"), "unknown"
            ),
            "selected_modules": _safe_tokens(
                session.get("selected_modules")
            ),
            "missing_document_count": len(missing_items),
            "next_meeting_question_count": len(next_test_items),
        },
        "missing_documents": [],
        "next_meeting_questions": [],
        "privacy_flags": {
            "api_key_included": False,
            "patient_level_data_included": False,
            "raw_deck_included": False,
            "free_text_answers_included": False,
            "free_text_report_included": False,
        },
        "run_manifest": {
            "input_manifest": {
                "deck": _safe_deck_manifest(session)
            },
            "routing_steps": _safe_tokens(
                session.get("routing_steps")
            ),
            "selected_modules": _safe_tokens(
                session.get("selected_modules")
            ),
            "question_count": len(raw_questions_answers),
            "model": _safe_token(session.get("model")),
        },
    }
    missing = [key for key in REQUIRED_PAYLOAD_FIELDS if key not in payload]
    if missing:
        raise ValueError(f"completion payload missing fields: {missing}")
    return payload



class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_completion_json(webhook_url, payload, timeout_seconds=10):
    parsed = urlparse(str(webhook_url or ""))
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("TRAINER_EVENT_WEBHOOK must be an HTTPS URL")
    body = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    request = Request(
        webhook_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Idempotency-Key": payload["idempotency_key"],
            "User-Agent": "startup-trainer-completion/1",
        },
    )
    opener = build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout_seconds) as response:
        status = getattr(response, "status", None)
        if status is None:
            status = response.getcode()
        status = int(status)
    if status < 200 or status >= 300:
        raise RuntimeError(f"completion webhook returned HTTP {status}")
    return status
