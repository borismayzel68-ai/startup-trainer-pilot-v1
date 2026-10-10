import io
import json
import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("INVITE_CODE", "PILOT-S1-EXT-001")
os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")

import app as appmod
import completion_event as completionmod
from completion_event import (
    REQUIRED_PAYLOAD_FIELDS,
    build_completion_payload,
    post_completion_json,
)
from session_store import DurableSessionStore


def sample_report():
    return {
        "missing": ["signed pilot protocol"],
        "next_tests": ["confirm retention cohort"],
        "recommendation": "iterate",
    }


class CompletionPayloadTests(unittest.TestCase):
    def test_payload_has_canonical_fields_without_free_text(self):
        patient_text = (
            "Пациент Иванов Иван Иванович, 45 лет, сахарный диабет 2 типа, "
            "01.01.1980, СНИЛС 12345678901, +49 170 1234567, "
            "patient@example.com, ghp_abcdefghijklmnopqrst"
        )
        session = {
            "id": "session-1",
            "project_id": "VC-178",
            "project_name": "Doctum",
            "founder_name": "Test Founder",
            "founder_email": "founder@example.com",
            "jurisdiction": "RF",
            "product_class": "WELLNESS_INFORMATION",
            "mode": "B",
            "created_at": "2026-09-26T10:00:00+00:00",
            "qa": [{
                "module": "traction",
                "slot": "pilot_evidence",
                "at": "2026-09-26T10:15:00+00:00",
                "question": patient_text,
                "answer": patient_text,
                "api_key": "must-not-leak",
                "analysis": {
                    "status": "fact",
                    "quote": patient_text,
                    "contradiction": patient_text,
                },
            }, {
                "module": patient_text,
                "slot": patient_text,
                "at": patient_text,
                "answer": patient_text,
                "analysis": {"status": patient_text},
            }],
            "report": {
                "missing": [patient_text],
                "next_tests": [patient_text],
                "recommendation": "iterate",
                "note": "OpenAI key sk-proj-abcdefghijklmnop",
                "patient_note": patient_text,
            },
            "coverage": {"patient_id": patient_text},
            "selected_modules": ["regulatory"],
            "routing_steps": ["jurisdiction"],
            "input_manifest": {
                "deck": {
                    "present": True,
                    "filename": patient_text,
                    "raw_text": "alternate raw deck text",
                    "mime_type": "application/pdf",
                    "size_bytes": 123,
                    "sha256": "abcdef123456",
                },
                "deck_text": "raw",
            },
            "model": "test-model",
        }
        payload = build_completion_payload(
            session,
            "s1-ext-pilot-v1.4-completion-event",
            "2026-09-26T10:30:00+00:00",
        )

        self.assertEqual(set(payload), set(REQUIRED_PAYLOAD_FIELDS))
        self.assertEqual(payload["event_type"], "TRAINER_COMPLETED")
        self.assertEqual(
            payload["idempotency_key"],
            "trainer-completed:session-1",
        )
        self.assertEqual(
            payload["event_id"],
            str(completionmod.uuid.uuid5(
                completionmod.uuid.NAMESPACE_URL,
                "trainer-completed:session-1",
            )),
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            patient_text,
            "must-not-leak",
            "sk-proj-abcdefghijklmnop",
            "alternate raw deck text",
            "Иванов",
            "patient@example.com",
            "ghp_abcdefghijklmnopqrst",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(payload["questions_answers"], [{
            "module": "traction",
            "slot": "pilot_evidence",
            "status": "fact",
            "at": "2026-09-26T10:15:00+00:00",
        }, {
            "module": None,
            "slot": None,
            "status": "unknown",
            "at": None,
        }])
        self.assertEqual(payload["missing_documents"], [])
        self.assertEqual(payload["next_meeting_questions"], [])
        self.assertEqual(payload["founder_report"], {
            "recommendation": "iterate",
            "missing_document_count": 1,
            "next_test_count": 1,
        })
        self.assertNotIn(
            "filename",
            payload["run_manifest"]["input_manifest"]["deck"],
        )
        self.assertEqual(
            payload["privacy_flags"],
            {
                "api_key_included": False,
                "patient_level_data_included": False,
                "raw_deck_included": False,
                "free_text_answers_included": False,
                "free_text_report_included": False,
            },
        )

    def test_post_requires_https(self):
        with self.assertRaises(ValueError):
            post_completion_json(
                "http://example.test/hook",
                {"idempotency_key": "key"},
            )

    def test_post_sends_json_with_idempotency_header(self):
        captured = {}

        class Response:
            status = 204

            def getcode(self):
                return self.status

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Opener:
            def open(self, request, timeout):
                captured["body"] = json.loads(request.data)
                captured["key"] = request.get_header("Idempotency-key")
                captured["timeout"] = timeout
                return Response()

        payload = {"idempotency_key": "key-1", "event_type": "TRAINER_COMPLETED"}
        with mock.patch.object(
            completionmod, "build_opener", return_value=Opener()
        ) as build:
            status = post_completion_json(
                "https://example.test/hook", payload, timeout_seconds=3
            )

        self.assertEqual(status, 204)
        self.assertEqual(captured["body"], payload)
        self.assertEqual(captured["key"], "key-1")
        self.assertEqual(captured["timeout"], 3)
        self.assertIsInstance(
            build.call_args.args[0],
            completionmod._NoRedirect,
        )

    def test_redirect_response_is_not_accepted(self):
        class Response:
            status = 302

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        opener = mock.Mock()
        opener.open.return_value = Response()
        with mock.patch.object(
            completionmod, "build_opener", return_value=opener
        ), self.assertRaises(RuntimeError):
            post_completion_json(
                "https://example.test/hook",
                {"idempotency_key": "key-1"},
            )


class CompletionEmissionTests(unittest.TestCase):
    def setUp(self):
        appmod.SESSIONS.clear()
        appmod.app.testing = True
        self.client = appmod.app.test_client()

    def start(self):
        response = self.client.post("/api/start", data={
            "invite_code": "PILOT-S1-EXT-001",
            "project_id": "VC-178",
            "project_name": "Doctum",
            "founder_name": "Test Founder",
            "founder_email": "founder@example.com",
            "jurisdiction": "RF",
            "intended_use": "laboratory history",
            "product_class": "WELLNESS_INFORMATION",
            "mode": "B",
            "consent": "true",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["id"]

    def finish(self, sid):
        return self.client.post(
            f"/api/session/{sid}/answer",
            json={"answer": "стоп"},
        )

    def test_completion_is_enqueued_once(self):
        with mock.patch.object(
            appmod, "final_report", return_value=sample_report()
        ):
            sid = self.start()
            first = self.finish(sid)
            second = self.finish(sid)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        public_state = second.get_json()
        state = appmod.SESSIONS.get(sid)
        self.assertNotIn("event_status", public_state)
        self.assertNotIn("event_id", public_state)
        self.assertNotIn("idempotency_key", public_state)
        self.assertNotIn("event_error", public_state)
        self.assertEqual(state["event_status"], "queued")
        self.assertEqual(state["event_attempts"], 0)
        self.assertEqual(appmod.SESSIONS.count_completion_outbox(), 1)
        queued = appmod.SESSIONS.get_completion_outbox(
            f"trainer-completed:{sid}"
        )
        self.assertEqual(queued["payload"]["session_id"], sid)
        self.assertEqual(
            queued["payload"]["idempotency_key"],
            f"trainer-completed:{sid}",
        )

    def test_request_does_not_call_network(self):
        with mock.patch.object(
            appmod, "final_report", return_value=sample_report()
        ), mock.patch.object(
            completionmod, "post_completion_json"
        ) as post:
            sid = self.start()
            first = self.finish(sid)
            second = self.finish(sid)

        state = appmod.SESSIONS.get(sid)
        self.assertEqual(state["event_status"], "queued")
        self.assertEqual(state["event_attempts"], 0)
        self.assertNotIn("event_status", first.get_json())
        self.assertNotIn("event_status", second.get_json())
        post.assert_not_called()

    def test_missing_webhook_does_not_lose_event(self):
        with mock.patch.object(
            appmod, "TRAINER_EVENT_WEBHOOK", ""
        ), mock.patch.object(
            appmod, "final_report", return_value=sample_report()
        ):
            sid = self.start()
            response = self.finish(sid)

        state = appmod.SESSIONS.get(sid)
        self.assertEqual(state["event_status"], "queued")
        self.assertEqual(state["event_attempts"], 0)
        self.assertEqual(appmod.SESSIONS.count_completion_outbox(), 1)
        self.assertNotIn("event_status", response.get_json())

    def test_parallel_completion_enqueues_once(self):
        barrier = threading.Barrier(2)

        def report(session):
            barrier.wait(timeout=5)
            return sample_report()

        def finish_in_thread(sid):
            # Тот же браузер (cookie сессии владельца), но отдельный клиент на поток:
            # с 31b85af /answer требует, чтобы sid был в browser session.
            cookie = self.client.get_cookie("session")
            with appmod.app.test_client() as client:
                client.set_cookie("session", cookie.value)
                return client.post(
                    f"/api/session/{sid}/answer",
                    json={"answer": "стоп"},
                ).status_code

        with mock.patch.object(
            appmod, "final_report", side_effect=report
        ):
            sid = self.start()
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(
                    lambda _: finish_in_thread(sid),
                    range(2),
                ))

        self.assertEqual(statuses, [200, 200])
        self.assertEqual(appmod.SESSIONS.count_completion_outbox(), 1)
        state = appmod.SESSIONS.get(sid)
        self.assertEqual(state["event_status"], "queued")
        self.assertEqual(state["event_attempts"], 0)

    def test_payload_build_failure_is_persisted(self):
        with mock.patch.object(
            appmod, "TRAINER_EVENT_WEBHOOK",
            "https://hooks.example.test/a8",
        ), mock.patch.object(
            appmod, "final_report", return_value=sample_report()
        ), mock.patch.object(
            appmod,
            "build_completion_payload",
            side_effect=ValueError("invalid payload"),
        ):
            sid = self.start()
            response = self.finish(sid)

        self.assertEqual(response.status_code, 200)
        state = appmod.SESSIONS.get(sid)
        self.assertEqual(state["event_status"], "failed")
        self.assertIn("invalid payload", state["event_error"])
        self.assertEqual(appmod.SESSIONS.count_completion_outbox(), 0)

    def test_queued_state_and_payload_are_persisted_together(self):
        with mock.patch.object(
            appmod, "final_report", return_value=sample_report()
        ):
            sid = self.start()
            self.finish(sid)

        state = appmod.SESSIONS.get(sid)
        queued = appmod.SESSIONS.get_completion_outbox(
            f"trainer-completed:{sid}"
        )
        self.assertEqual(state["event_status"], "queued")
        self.assertEqual(
            queued["event_id"], state["event_id"],
        )


class CompletionReceiverTests(unittest.TestCase):
    def setUp(self):
        appmod.SESSIONS.clear()
        appmod.app.testing = True
        self.previous_secret = appmod.COMPLETION_RECEIVER_SECRET
        appmod.COMPLETION_RECEIVER_SECRET = "test-receiver-secret"

    def tearDown(self):
        appmod.COMPLETION_RECEIVER_SECRET = self.previous_secret

    def payload(self, session_id="11111111-1111-4111-8111-111111111111"):
        key = f"trainer-completed:{session_id}"
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
        appmod.SESSIONS.save({
            "id": session_id,
            "project_id": "VC-178",
            "created_at": appmod.now(),
            "updated_at": appmod.now(),
            "event_status": "queued",
            "event_id": event_id,
            "idempotency_key": key,
        })
        return {
            "event_type": "TRAINER_COMPLETED",
            "idempotency_key": key,
            "event_id": event_id,
            "session_id": session_id,
            "questions_answers": [{"answer": "must not be stored"}],
        }

    def post(self, payload, secret="test-receiver-secret"):
        with appmod.app.test_client() as client:
            return client.post(
                "/api/completion-events",
                json=payload,
                headers={
                    "Idempotency-Key": payload["idempotency_key"],
                    "X-Completion-Secret": secret,
                },
            )

    def complete(self, payload, attempt_token, secret="test-receiver-secret"):
        body = dict(payload)
        body["attempt_token"] = attempt_token
        with appmod.app.test_client() as client:
            return client.post(
                "/api/completion-events/complete",
                json=body,
                headers={
                    "Idempotency-Key": payload["idempotency_key"],
                    "X-Completion-Secret": secret,
                },
            )

    def test_duplicate_delivery_is_a_no_op(self):
        payload = self.payload()
        first = self.post(payload)
        second = self.post(payload)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.get_json()["status"], "accepted")
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["code"], "in_progress")
        completed = self.complete(
            payload, first.get_json()["attempt_token"]
        )
        self.assertEqual(completed.status_code, 200)
        third = self.post(payload)
        self.assertEqual(third.status_code, 200)
        self.assertEqual(third.get_json()["status"], "duplicate")
        self.assertEqual(appmod.SESSIONS.count_completion_receipts(), 1)
        receipt = appmod.SESSIONS.get_completion_receipt(
            payload["idempotency_key"]
        )
        self.assertEqual(set(receipt), {
            "idempotency_key", "event_id", "session_id", "state",
            "attempt_token", "lease_until", "accepted_at",
            "completed_at", "updated_at",
        })
        self.assertEqual(receipt["state"], "completed")
        self.assertNotIn("must not be stored", json.dumps(receipt))

    def test_parallel_duplicate_delivery_is_recorded_once(self):
        payload = self.payload(
            "22222222-2222-4222-8222-222222222222"
        )
        barrier = threading.Barrier(8)

        def send(_):
            barrier.wait(timeout=5)
            response = self.post(payload)
            body = response.get_json()
            return response.status_code, body.get("status") or body.get("code")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(send, range(8)))

        self.assertEqual(sum(status == "accepted" for _, status in results), 1)
        self.assertEqual(sum(status == "in_progress" for _, status in results), 7)
        self.assertEqual(appmod.SESSIONS.count_completion_receipts(), 1)

    def test_receipt_survives_store_restart(self):
        handle, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        try:
            url = f"sqlite:///{path}"
            first = DurableSessionStore(url)
            session_id = "33333333-3333-4333-8333-333333333333"
            key = f"trainer-completed:{session_id}"
            event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
            first.save({
                "id": session_id, "project_id": "VC-178",
                "created_at": appmod.now(), "updated_at": appmod.now(),
                "event_status": "queued",
            })
            accepted = first.begin_completion_delivery(
                key, event_id, session_id,
            )
            self.assertEqual(accepted["status"], "accepted")
            completed = first.complete_completion_delivery(
                key, event_id, session_id, accepted["attempt_token"],
            )
            self.assertEqual(completed["status"], "completed")
            restarted = DurableSessionStore(url)
            duplicate = restarted.begin_completion_delivery(
                key, event_id, session_id,
            )
            self.assertEqual(duplicate["status"], "duplicate")
            self.assertEqual(restarted.count_completion_receipts(), 1)
        finally:
            os.unlink(path)

    def test_receiver_rejects_mismatched_header(self):
        payload = self.payload()
        with appmod.app.test_client() as client:
            response = client.post(
                "/api/completion-events",
                json=payload,
                headers={
                    "Idempotency-Key": "wrong",
                    "X-Completion-Secret": "test-receiver-secret",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "idempotency_key_invalid")
        self.assertEqual(appmod.SESSIONS.count_completion_receipts(), 0)

    def test_receiver_requires_secret(self):
        response = self.post(self.payload(), secret="wrong")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "receiver_unauthorized")
        self.assertEqual(appmod.SESSIONS.count_completion_receipts(), 0)

    def test_receiver_handles_non_ascii_wrong_secret(self):
        response = self.post(self.payload(), secret="неверный-пароль")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "receiver_unauthorized")

    def test_stale_completion_token_is_rejected(self):
        payload = self.payload()
        first = self.post(payload)
        stale = self.complete(payload, str(uuid.uuid4()))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["code"], "stale_attempt")
        good = self.complete(payload, first.get_json()["attempt_token"])
        self.assertEqual(good.status_code, 200)

    def test_deleted_session_cannot_enter_gate(self):
        payload = self.payload()
        appmod.SESSIONS.delete(payload["session_id"])
        response = self.post(payload)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.get_json()["code"], "session_deleted")
        self.assertEqual(appmod.SESSIONS.count_completion_receipts(), 0)

    def test_receiver_rejects_patient_text_as_session_id(self):
        payload = self.payload()
        payload["session_id"] = "ФИО пациента, дата рождения"
        response = self.post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "session_id_invalid")
        self.assertEqual(appmod.SESSIONS.count_completion_receipts(), 0)

    def test_receiver_rejects_noncanonical_event_id(self):
        payload = self.payload()
        payload["event_id"] = str(uuid.uuid4())
        response = self.post(payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "event_id_invalid")
        self.assertEqual(appmod.SESSIONS.count_completion_receipts(), 0)

    def test_receiver_rejects_oversized_payload(self):
        payload = self.payload()
        payload["padding"] = "x" * appmod.MAX_COMPLETION_EVENT_BYTES
        response = self.post(payload)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["code"], "event_too_large")
        self.assertEqual(appmod.SESSIONS.count_completion_receipts(), 0)

    def test_chunked_oversized_payload_is_read_with_a_hard_limit(self):
        payload = self.payload()
        raw = json.dumps({
            **payload,
            "padding": "x" * appmod.MAX_COMPLETION_EVENT_BYTES,
        }).encode("utf-8")
        with appmod.app.test_client() as client:
            response = client.open(
                "/api/completion-events",
                method="POST",
                input_stream=io.BytesIO(raw),
                content_type="application/json",
                headers={
                    "Idempotency-Key": payload["idempotency_key"],
                    "X-Completion-Secret": "test-receiver-secret",
                },
                environ_overrides={
                    "CONTENT_LENGTH": "",
                    "wsgi.input_terminated": True,
                },
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["code"], "event_too_large")

    def test_receiver_is_closed_when_secret_is_not_configured(self):
        appmod.COMPLETION_RECEIVER_SECRET = ""
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "receiver_not_configured")
        self.assertEqual(appmod.SESSIONS.count_completion_receipts(), 0)


if __name__ == "__main__":
    unittest.main()
