import json
import os
import threading
import unittest
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

    def test_completion_is_posted_once(self):
        sent = []
        with mock.patch.object(
            appmod, "TRAINER_EVENT_WEBHOOK",
            "https://hooks.example.test/a8",
        ), mock.patch.object(
            appmod, "final_report", return_value=sample_report()
        ), mock.patch.object(
            appmod,
            "post_completion_json",
            side_effect=lambda url, payload, timeout_seconds: (
                sent.append((url, payload, timeout_seconds)) or 202
            ),
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
        self.assertEqual(state["event_status"], "sent")
        self.assertEqual(state["event_attempts"], 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1]["session_id"], sid)
        self.assertEqual(
            sent[0][1]["idempotency_key"],
            f"trainer-completed:{sid}",
        )

    def test_failed_post_is_not_retried_by_a8(self):
        with mock.patch.object(
            appmod, "TRAINER_EVENT_WEBHOOK",
            "https://hooks.example.test/a8",
        ), mock.patch.object(
            appmod, "final_report", return_value=sample_report()
        ), mock.patch.object(
            appmod,
            "post_completion_json",
            side_effect=RuntimeError("receiver unavailable"),
        ) as post:
            sid = self.start()
            first = self.finish(sid)
            second = self.finish(sid)

        state = appmod.SESSIONS.get(sid)
        self.assertEqual(state["event_status"], "failed")
        self.assertEqual(state["event_attempts"], 1)
        self.assertNotIn("event_status", first.get_json())
        self.assertNotIn("event_status", second.get_json())
        self.assertEqual(post.call_count, 1)

    def test_missing_webhook_is_explicit(self):
        with mock.patch.object(
            appmod, "TRAINER_EVENT_WEBHOOK", ""
        ), mock.patch.object(
            appmod, "final_report", return_value=sample_report()
        ):
            sid = self.start()
            response = self.finish(sid)

        state = appmod.SESSIONS.get(sid)
        self.assertEqual(state["event_status"], "not_configured")
        self.assertEqual(state["event_attempts"], 0)
        self.assertNotIn("event_status", response.get_json())

    def test_parallel_completion_posts_once(self):
        barrier = threading.Barrier(2)
        sent = []

        def report(session):
            barrier.wait(timeout=5)
            return sample_report()

        def finish_in_thread(sid):
            with appmod.app.test_client() as client:
                return client.post(
                    f"/api/session/{sid}/answer",
                    json={"answer": "стоп"},
                ).status_code

        with mock.patch.object(
            appmod, "TRAINER_EVENT_WEBHOOK",
            "https://hooks.example.test/a8",
        ), mock.patch.object(
            appmod, "final_report", side_effect=report
        ), mock.patch.object(
            appmod,
            "post_completion_json",
            side_effect=lambda url, payload, timeout_seconds: (
                sent.append(payload) or 202
            ),
        ):
            sid = self.start()
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(
                    lambda _: finish_in_thread(sid),
                    range(2),
                ))

        self.assertEqual(statuses, [200, 200])
        self.assertEqual(len(sent), 1)
        state = appmod.SESSIONS.get(sid)
        self.assertEqual(state["event_status"], "sent")
        self.assertEqual(state["event_attempts"], 1)

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
        ), mock.patch.object(
            appmod, "post_completion_json"
        ) as post:
            sid = self.start()
            response = self.finish(sid)

        self.assertEqual(response.status_code, 200)
        state = appmod.SESSIONS.get(sid)
        self.assertEqual(state["event_status"], "failed")
        self.assertIn("invalid payload", state["event_error"])
        post.assert_not_called()

    def test_emitting_is_persisted_before_network_call(self):
        observed = {}

        def post(url, payload, timeout_seconds):
            stored = appmod.SESSIONS.get(payload["session_id"])
            observed["status"] = stored["event_status"]
            observed["event_id"] = stored["event_id"]
            return 202

        with mock.patch.object(
            appmod, "TRAINER_EVENT_WEBHOOK",
            "https://hooks.example.test/a8",
        ), mock.patch.object(
            appmod, "final_report", return_value=sample_report()
        ), mock.patch.object(
            appmod, "post_completion_json", side_effect=post
        ):
            sid = self.start()
            self.finish(sid)

        self.assertEqual(observed["status"], "emitting")
        self.assertEqual(
            observed["event_id"],
            appmod.SESSIONS.get(sid)["event_id"],
        )


if __name__ == "__main__":
    unittest.main()
