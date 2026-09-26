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
import delivery_worker as workermod
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


def complete_make_route(store, body, completed_at=T0):
    """Model Make: gate, idempotent work, confirm, then 2xx."""
    accepted = store.begin_completion_delivery(
        body["idempotency_key"], body["event_id"], body["session_id"],
        accepted_at=iso_z(completed_at),
        lease_until=iso_z(completed_at + timedelta(seconds=60)),
    )
    if accepted["status"] == "accepted":
        completed = store.complete_completion_delivery(
            body["idempotency_key"], body["event_id"],
            body["session_id"], accepted["attempt_token"],
            completed_at=iso_z(completed_at),
        )
        if completed["status"] != "completed":
            raise AssertionError(completed)
    elif accepted["status"] != "duplicate":
        raise RuntimeError(f"gate returned {accepted['status']}")
    return 202


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
            return complete_make_route(self.store, body)

        self.assertTrue(deliver_one(
            self.store,
            "https://hook.eu1.make.com/test",
            post_func=post,
            now_value=T0,
        ))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], payload)
        outbox = self.store.get_completion_outbox(key)
        self.assertEqual(outbox["status"], "delivered")
        self.assertEqual(outbox["payload"], {})
        state = self.store.get(payload["session_id"])
        self.assertEqual(state["event_status"], "sent")
        self.assertEqual(state["event_attempts"], 1)

    def test_timeout_then_retry_is_one_downstream_delivery(self):
        key, payload = seed(self.store)
        downstream = []
        calls = []
        def make_delivery(_url, body, timeout_seconds):
            calls.append(body["event_id"])
            gate_time = T0 + timedelta(seconds=5 * (len(calls) - 1))
            result = self.store.begin_completion_delivery(
                body["idempotency_key"], body["event_id"],
                body["session_id"], accepted_at=iso_z(gate_time),
                lease_until=iso_z(gate_time + timedelta(seconds=1)),
            )
            if result["status"] != "accepted":
                raise RuntimeError(f"gate returned {result['status']}")
            if len(calls) == 1:
                raise RuntimeError("downstream step failed")
            downstream.append(body["event_id"])
            completed = self.store.complete_completion_delivery(
                body["idempotency_key"], body["event_id"],
                body["session_id"], result["attempt_token"],
                completed_at=iso_z(gate_time),
            )
            self.assertEqual(completed["status"], "completed")
            return 202

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

        final = self.store.get_completion_outbox(key)
        self.assertEqual(final["status"], "delivered")
        self.assertEqual(final["attempts"], 2)
        self.assertEqual(calls, [payload["event_id"], payload["event_id"]])
        self.assertEqual(downstream, [payload["event_id"]])
        self.assertEqual(self.store.count_completion_receipts(), 1)

    def test_early_make_2xx_without_completed_receipt_is_retried(self):
        key, payload = seed(self.store)

        def early_response(_url, body, timeout_seconds):
            accepted = self.store.begin_completion_delivery(
                body["idempotency_key"], body["event_id"],
                body["session_id"], accepted_at=iso_z(T0),
                lease_until=iso_z(T0 + timedelta(seconds=60)),
            )
            self.assertEqual(accepted["status"], "accepted")
            return 200

        self.assertTrue(deliver_one(
            self.store,
            "https://hook.eu1.make.com/test",
            post_func=early_response,
            now_value=T0,
            retry_base_seconds=5,
        ))
        outbox = self.store.get_completion_outbox(key)
        self.assertEqual(outbox["status"], "retry")
        self.assertEqual(outbox["payload"], payload)
        self.assertIn("before completion receipt", outbox["last_error"])
        self.assertEqual(
            self.store.get_completion_receipt(key)["state"],
            "processing",
        )
        self.assertEqual(
            self.store.get(payload["session_id"])["event_status"],
            "retrying",
        )

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
                sent.append(body["event_id"])
                or complete_make_route(
                    restarted, body, T0 + timedelta(seconds=61)
                )
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
                return complete_make_route(local_store, body)

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
        self.assertIsNone(self.store.get_completion_outbox(
            f"trainer-completed:{payload['session_id']}"
        ))

    def test_delete_and_retention_remove_all_technical_rows(self):
        key, payload = seed(self.store)
        receipt = self.store.begin_completion_delivery(
            key, payload["event_id"], payload["session_id"],
        )
        self.assertEqual(receipt["status"], "accepted")
        self.store.delete(payload["session_id"])
        self.assertEqual(self.store.count_completion_outbox(), 0)
        self.assertEqual(self.store.count_completion_receipts(), 0)

        key, payload = seed(
            self.store, "44444444-4444-4444-8444-444444444444"
        )
        self.store.begin_completion_delivery(
            key, payload["event_id"], payload["session_id"],
        )
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE trainer_sessions SET expires_at=? WHERE id=?",
                ("2000-01-01T00:00:00Z", payload["session_id"]),
            )
            conn.commit()
        self.store.purge_expired()
        self.assertEqual(self.store.count_completion_outbox(), 0)
        self.assertEqual(self.store.count_completion_receipts(), 0)

    def test_stale_session_save_cannot_roll_back_delivery_status(self):
        key, payload = seed(self.store)
        stale = self.store.get(payload["session_id"])
        self.assertTrue(deliver_one(
            self.store,
            "https://hook.eu1.make.com/test",
            post_func=lambda url, body, timeout_seconds: (
                complete_make_route(self.store, body)
            ),
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
        self.assertEqual(final["feedback"], {"rating": 5})

    def test_late_worker_cannot_finish_newer_claim(self):
        key, payload = seed(self.store)
        complete_make_route(self.store, payload)
        first = self.store.claim_next_completion_delivery(
            claimed_at=iso_z(T0),
            stale_before=iso_z(T0 - timedelta(seconds=60)),
        )
        second = self.store.claim_next_completion_delivery(
            claimed_at=iso_z(T0 + timedelta(seconds=61)),
            stale_before=iso_z(T0 + timedelta(seconds=1)),
        )
        self.assertNotEqual(first["claim_token"], second["claim_token"])
        self.assertFalse(self.store.mark_completion_delivery_succeeded(
            key, first["claim_token"], 202, iso_z(T0)
        ))
        self.assertTrue(self.store.mark_completion_delivery_succeeded(
            key, second["claim_token"], 202, iso_z(T0)
        ))

    def test_crashed_last_attempt_becomes_dead(self):
        key, _payload = seed(self.store)
        claimed = self.store.claim_next_completion_delivery(
            claimed_at=iso_z(T0),
            stale_before=iso_z(T0 - timedelta(seconds=60)),
            max_attempts=1,
        )
        self.assertIsNotNone(claimed)
        sent = []
        self.assertFalse(deliver_one(
            self.store,
            "https://hook.eu1.make.com/test",
            post_func=lambda *args, **kwargs: sent.append(True),
            now_value=T0 + timedelta(seconds=61),
            claim_timeout_seconds=60,
            max_attempts=1,
        ))
        final = self.store.get_completion_outbox(key)
        self.assertEqual(final["status"], "dead")
        self.assertEqual(final["payload"], {})
        self.assertEqual(sent, [])

    def test_lowered_attempt_limit_closes_existing_retry(self):
        key, _payload = seed(self.store)
        claimed = self.store.claim_next_completion_delivery(
            claimed_at=iso_z(T0),
            stale_before=iso_z(T0 - timedelta(seconds=60)),
            max_attempts=8,
        )
        self.store.mark_completion_delivery_failed(
            key, claimed["claim_token"], "temporary",
            available_at=iso_z(T0 + timedelta(seconds=5)),
            failed_at=iso_z(T0), terminal=False,
        )
        sent = []
        self.assertFalse(deliver_one(
            self.store,
            "https://hook.eu1.make.com/test",
            post_func=lambda *args, **kwargs: sent.append(True),
            now_value=T0 + timedelta(seconds=5),
            max_attempts=1,
        ))
        final = self.store.get_completion_outbox(key)
        self.assertEqual(final["status"], "dead")
        self.assertEqual(final["payload"], {})
        self.assertEqual(sent, [])

    def test_worker_loop_survives_database_error(self):
        stop = threading.Event()
        calls = []

        def attempt(*_args, **_kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("database restarting")
            stop.set()
            return False

        with mock.patch.object(workermod, "deliver_one", side_effect=attempt):
            purge = mock.Mock(wraps=self.store.purge_expired)
            self.store.purge_expired = purge
            workermod.run_forever(
                self.store, "https://hook.example.test", stop,
                poll_seconds=0,
            )
        self.assertEqual(len(calls), 2)
        purge.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
