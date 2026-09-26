import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("INVITE_CODE", "PILOT-S1-EXT-001")
os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")

import app as appmod
from delivery_worker import deliver_one
from session_store import DurableSessionStore, iso_z


T0 = datetime(2026, 9, 26, 20, 0, tzinfo=timezone.utc)


def identity(session_id):
    key = f"trainer-completed:{session_id}"
    return key, str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def seed(store, session_id="11111111-1111-4111-8111-111111111111"):
    key, event_id = identity(session_id)
    session = {
        "id": session_id,
        "project_id": "VC-178",
        "created_at": iso_z(T0),
        "updated_at": iso_z(T0),
        "event_status": "not_emitted",
        "event_attempts": 0,
    }
    payload = {
        "event_type": "TRAINER_COMPLETED",
        "idempotency_key": key,
        "event_id": event_id,
        "session_id": session_id,
        "privacy_flags": {
            "patient_level_data_included": False,
            "free_text_answers_included": False,
            "free_text_report_included": False,
        },
    }
    store.save(session)
    if not store.enqueue_completion_event(
        session, payload, queued_at=iso_z(T0)
    ):
        raise AssertionError("failed to seed outbox")
    return key, payload


class DeliveryWorkerTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        self.url = f"sqlite:///{self.path}"
        self.store = DurableSessionStore(self.url)

    def tearDown(self):
        os.unlink(self.path)

    def test_success_marks_outbox_and_session(self):
        key, payload = seed(self.store)
        sent = []

        def post(url, body, timeout_seconds):
            sent.append((url, body, timeout_seconds))
            return 202

        self.assertTrue(deliver_one(
            self.store,
            "https://hook.eu1.make.com/test",
            post_func=post,
            now_value=T0,
        ))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], payload)
        self.assertEqual(self.store.get_completion_outbox(key)["status"], "delivered")
        state = self.store.get(payload["session_id"])
        self.assertEqual(state["event_status"], "sent")
        self.assertEqual(state["event_attempts"], 1)

    def test_timeout_then_retry_is_one_downstream_delivery(self):
        key, payload = seed(self.store)
        downstream = []
        calls = []
        previous_store = appmod.SESSIONS
        previous_secret = appmod.COMPLETION_RECEIVER_SECRET
        appmod.SESSIONS = self.store
        appmod.COMPLETION_RECEIVER_SECRET = "test-secret"

        def make_delivery(_url, body, timeout_seconds):
            calls.append(body["event_id"])
            with appmod.app.test_client() as client:
                response = client.post(
                    "/api/completion-events",
                    json=body,
                    headers={
                        "Idempotency-Key": body["idempotency_key"],
                        "X-Completion-Secret": "test-secret",
                    },
                )
            if response.get_json()["status"] == "accepted":
                downstream.append(body["event_id"])
            if len(calls) == 1:
                raise TimeoutError("response lost after Make accepted")
            return 202

        try:
            self.assertTrue(deliver_one(
                self.store,
                "https://hook.eu1.make.com/test",
                post_func=make_delivery,
                now_value=T0,
                retry_base_seconds=5,
            ))
            first = self.store.get_completion_outbox(key)
            self.assertEqual(first["status"], "retry")
            self.assertTrue(deliver_one(
                self.store,
                "https://hook.eu1.make.com/test",
                post_func=make_delivery,
                now_value=T0 + timedelta(seconds=5),
                retry_base_seconds=5,
            ))
        finally:
            appmod.SESSIONS = previous_store
            appmod.COMPLETION_RECEIVER_SECRET = previous_secret

        final = self.store.get_completion_outbox(key)
        self.assertEqual(final["status"], "delivered")
        self.assertEqual(final["attempts"], 2)
        self.assertEqual(calls, [payload["event_id"], payload["event_id"]])
        self.assertEqual(downstream, [payload["event_id"]])
        self.assertEqual(self.store.count_completion_receipts(), 1)

    def test_stale_claim_is_recovered_after_restart(self):
        key, _payload = seed(self.store)
        claimed = self.store.claim_next_completion_delivery(
            claimed_at=iso_z(T0),
            stale_before=iso_z(T0 - timedelta(seconds=60)),
        )
        self.assertIsNotNone(claimed)
        restarted = DurableSessionStore(self.url)
        sent = []
        self.assertTrue(deliver_one(
            restarted,
            "https://hook.eu1.make.com/test",
            post_func=lambda url, body, timeout_seconds: (
                sent.append(body["event_id"]) or 202
            ),
            now_value=T0 + timedelta(seconds=61),
            claim_timeout_seconds=60,
        ))
        self.assertEqual(len(sent), 1)
        self.assertEqual(restarted.get_completion_outbox(key)["status"], "delivered")

    def test_parallel_workers_claim_one_attempt(self):
        key, _payload = seed(self.store)
        barrier = threading.Barrier(8)
        sent = []
        sent_lock = threading.Lock()

        def run(_):
            local_store = DurableSessionStore(self.url)
            barrier.wait(timeout=5)

            def post(url, body, timeout_seconds):
                with sent_lock:
                    sent.append(body["event_id"])
                return 202

            return deliver_one(
                local_store,
                "https://hook.eu1.make.com/test",
                post_func=post,
                now_value=T0,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(8)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.store.get_completion_outbox(key)["attempts"], 1)

    def test_terminal_failure_stops_after_limit(self):
        key, _payload = seed(self.store)

        def fail(_url, _body, timeout_seconds):
            raise RuntimeError("Make unavailable")

        self.assertTrue(deliver_one(
            self.store,
            "https://hook.eu1.make.com/test",
            post_func=fail,
            now_value=T0,
            max_attempts=1,
        ))
        self.assertEqual(self.store.get_completion_outbox(key)["status"], "dead")
        self.assertFalse(deliver_one(
            self.store,
            "https://hook.eu1.make.com/test",
            post_func=fail,
            now_value=T0 + timedelta(hours=1),
            max_attempts=1,
        ))

    def test_delete_during_delivery_does_not_restore_session(self):
        _key, payload = seed(self.store)

        def post(_url, _body, timeout_seconds):
            self.store.delete(payload["session_id"])
            return 202

        self.assertTrue(deliver_one(
            self.store,
            "https://hook.eu1.make.com/test",
            post_func=post,
            now_value=T0,
        ))
        self.assertIsNone(self.store.get(payload["session_id"]))

    def test_stale_session_save_cannot_roll_back_delivery_status(self):
        key, payload = seed(self.store)
        stale = self.store.get(payload["session_id"])
        self.assertTrue(deliver_one(
            self.store,
            "https://hook.eu1.make.com/test",
            post_func=lambda url, body, timeout_seconds: 202,
            now_value=T0,
        ))
        self.assertEqual(
            self.store.get(payload["session_id"])["event_status"],
            "sent",
        )

        stale["feedback"] = {"rating": 5}
        stale["updated_at"] = iso_z(T0 + timedelta(seconds=1))
        self.store.save(stale)

        final = self.store.get(payload["session_id"])
        self.assertEqual(final["event_status"], "sent")
        self.assertEqual(self.store.get_completion_outbox(key)["status"], "delivered")
        self.assertNotIn("feedback", final)


if __name__ == "__main__":
    unittest.main()
