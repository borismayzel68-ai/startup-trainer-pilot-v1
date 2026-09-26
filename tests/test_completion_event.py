import json
import os
import unittest
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
    def test_payload_has_canonical_fields_and_redacts_secrets(self):
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
                "question": "Evidence?",
                "answer": "MRN 12345",
                "api_key": "must-not-leak",
                "analysis": {"status": "fact", "quote": "MRN 12345"},
            }],
            "report": sample_report(),
            "coverage": {"patient_id": "must-not-leak"},
            "selected_modules": ["regulatory"],
            "routing_steps": ["jurisdiction"],
            "input_manifest": {"deck": {"present": True}, "deck_text": "raw"},
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
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("MRN 12345", serialized)
        self.assertIn("REDACTED_PATIENT_LEVEL_DATA", serialized)
        self.assertFalse(
            payload["privacy_flags"]["patient_level_data_included"]
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

        def fake_open(request, timeout):
            captured["body"] = json.loads(request.data)
            captured["key"] = request.get_header("Idempotency-key")
            captured["timeout"] = timeout
            return Response()

        payload = {"idempotency_key": "key-1", "event_type": "TRAINER_COMPLETED"}
        with mock.patch.object(
            completionmod, "urlopen", side_effect=fake_open
        ):
            status = post_completion_json(
                "https://example.test/hook", payload, timeout_seconds=3
            )

        self.assertEqual(status, 204)
        self.assertEqual(captured["body"], payload)
        self.assertEqual(captured["key"], "key-1")
        self.assertEqual(captured["timeout"], 3)


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
        state = second.get_json()
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

        self.assertEqual(first.get_json()["event_status"], "failed")
        self.assertEqual(second.get_json()["event_status"], "failed")
        self.assertEqual(second.get_json()["event_attempts"], 1)
        self.assertEqual(post.call_count, 1)

    def test_missing_webhook_is_explicit(self):
        with mock.patch.object(
            appmod, "TRAINER_EVENT_WEBHOOK", ""
        ), mock.patch.object(
            appmod, "final_report", return_value=sample_report()
        ):
            sid = self.start()
            response = self.finish(sid)

        self.assertEqual(
            response.get_json()["event_status"],
            "not_configured",
        )
        self.assertEqual(response.get_json()["event_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
