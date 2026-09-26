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

_BLOCKED_KEYS = re.compile(
    r"(api[_-]?key|authorization|password|secret|token|deck_text|"
    r"patient[_-]?id|mrn|medical[_-]?record|date[_-]?of[_-]?birth|"
    r"snils|снилс|фио[_-]?пациента)",
    re.IGNORECASE,
)
_PATIENT_TEXT = re.compile(
    r"\b(mrn|patient\s*id|date\s*of\s*birth|dob|ssn)\b|"
    r"фио|дата рождения|снилс|полис\s+омс|истори[яи]\s+болезни|"
    r"медицинск(?:ая|ой)\s+карт(?:а|ы)|диагноз\s+пациент|"
    r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b",
    re.IGNORECASE,
)
_SECRET_TEXT = re.compile(
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
    re.IGNORECASE,
)


def _safe_value(value):
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if not _BLOCKED_KEYS.search(str(key))
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, str) and _PATIENT_TEXT.search(value):
        return "[REDACTED_PATIENT_LEVEL_DATA]"
    if isinstance(value, str) and _SECRET_TEXT.search(value):
        return "[REDACTED_SECRET]"
    return value


def _evidence_delta(questions_answers):
    rows = []
    for item in questions_answers:
        analysis = item.get("analysis") or {}
        rows.append(
            {
                "module": item.get("module"),
                "slot": item.get("slot"),
                "status": analysis.get("status"),
                "quote": analysis.get("quote"),
                "contradiction": analysis.get("contradiction"),
                "source": "founder_answer",
                "at": item.get("at"),
            }
        )
    return rows


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

    questions_answers = _safe_value(session.get("qa") or [])
    founder_report = _safe_value(session.get("report") or {})
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
            "coverage": _safe_value(session.get("coverage") or {}),
            "stop_reason": session.get("stop_reason"),
            "selected_modules": list(session.get("selected_modules") or []),
        },
        "missing_documents": list(founder_report.get("missing") or []),
        "next_meeting_questions": list(
            founder_report.get("next_tests") or []
        ),
        "privacy_flags": {
            "api_key_included": False,
            "patient_level_data_included": False,
            "raw_deck_included": False,
        },
        "run_manifest": {
            "input_manifest": {
                "deck": {
                    key: _safe_value(value)
                    for key, value in (
                        (session.get("input_manifest") or {})
                        .get("deck", {})
                        .items()
                    )
                    if key in {
                        "present", "filename", "mime_type",
                        "size_bytes", "sha256",
                    }
                }
            },
            "routing_steps": list(session.get("routing_steps") or []),
            "selected_modules": list(
                session.get("selected_modules") or []
            ),
            "question_count": len(questions_answers),
            "model": session.get("model"),
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
