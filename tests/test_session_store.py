import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from session_store import DurableSessionStore

def sample_session(session_id="session-1"):
    return {
        "id": session_id,
        "project_id": "VC-178",
        "created_at": "2026-09-26T07:00:00Z",
        "updated_at": "2026-09-26T07:10:00Z",
        "input_manifest": {
            "deck": {
                "present": True,
                "filename": "doctum.pdf",
                "sha256": "abc",
            }
        },
        "qa": [{"question":"Q","answer":"A"}],
        "report": {"recommendation":"iterate"},
        "feedback": {"rating":5},
        "event_status": "not_emitted",
    }

class DurableSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "sessions.db"
        self.url = "sqlite:///" + path.as_posix()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_session_survives_store_restart_with_all_a7_fields(self):
        first = DurableSessionStore(self.url, retention_days=30)
        original = sample_session()
        first.save(original)

        restarted = DurableSessionStore(self.url, retention_days=30)
        loaded = restarted.get(original["id"])

        self.assertEqual(loaded["project_id"], "VC-178")
        self.assertEqual(loaded["input_manifest"]["deck"]["filename"], "doctum.pdf")
        self.assertEqual(loaded["qa"][0]["answer"], "A")
        self.assertEqual(loaded["report"]["recommendation"], "iterate")
        self.assertEqual(loaded["feedback"]["rating"], 5)
        self.assertEqual(loaded["event_status"], "not_emitted")
        self.assertTrue(loaded["expires_at"].endswith("Z"))

    def test_session_survives_separate_process_restart(self):
        writer = (
            "import sys;"
            "from session_store import DurableSessionStore;"
            "s={'id':'restart-1','project_id':'VC-178',"
            "'created_at':'2026-09-26T07:00:00Z',"
            "'updated_at':'2026-09-26T07:00:00Z'};"
            "DurableSessionStore(sys.argv[1]).save(s)"
        )
        reader = (
            "import sys;"
            "from session_store import DurableSessionStore;"
            "s=DurableSessionStore(sys.argv[1]).get('restart-1');"
            "assert s and s['project_id']=='VC-178'"
        )
        subprocess.run([sys.executable,"-c",writer,self.url],check=True)
        subprocess.run([sys.executable,"-c",reader,self.url],check=True)

    def test_delete_removes_persisted_session(self):
        store = DurableSessionStore(self.url)
        store.save(sample_session())
        self.assertTrue(store.delete("session-1"))
        self.assertIsNone(store.get("session-1"))
        restarted = DurableSessionStore(self.url)
        self.assertIsNone(restarted.get("session-1"))

    def test_expired_session_is_removed_on_read(self):
        store = DurableSessionStore(self.url)
        session = sample_session()
        session["expires_at"] = "2020-01-01T00:00:00Z"
        store.save(session)
        self.assertIsNone(store.get("session-1"))
        self.assertEqual(store.values(), [])

    def test_completion_event_claim_is_atomic(self):
        store = DurableSessionStore(self.url)
        session = sample_session("claim-1")
        store.save(session)
        first = dict(session)
        second = dict(session)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                store.claim_completion_event,
                (first, second),
            ))

        self.assertEqual(sorted(results), [False, True])
        loaded = store.get("claim-1")
        self.assertEqual(loaded["event_status"], "emitting")

        stale = sample_session("claim-1")
        store.save(stale)
        self.assertEqual(
            store.get("claim-1")["event_status"],
            "emitting",
        )

    def test_upsert_preserves_single_row(self):
        store = DurableSessionStore(self.url)
        session = sample_session()
        store.save(session)
        session["qa"].append({"question":"Q2","answer":"A2"})
        session["updated_at"] = "2026-09-26T07:20:00Z"
        store.save(session)
        self.assertEqual(len(store.values()), 1)
        self.assertEqual(len(store.get("session-1")["qa"]), 2)

    def test_postgres_schema_initialization_takes_advisory_lock_first(self):
        class FakeCursor:
            rowcount = 1

        class FakeConnection:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=None):
                self.calls.append((" ".join(sql.split()), params))
                return FakeCursor()

            def commit(self):
                self.calls.append(("COMMIT", None))

            def close(self):
                pass

        conn = FakeConnection()
        store = DurableSessionStore.__new__(DurableSessionStore)
        store.database_url = "postgresql://example.invalid/test"
        store.retention_days = 30
        store.backend = "postgres"
        store._lock = threading.RLock()
        store._memory_connection = None
        store._connect = lambda: conn

        store.initialize()

        self.assertEqual(
            conn.calls[0][0],
            "SELECT pg_advisory_xact_lock(%s)",
        )
        self.assertEqual(conn.calls[0][1], (8675309007,))
        self.assertIn("CREATE TABLE IF NOT EXISTS trainer_sessions", conn.calls[1][0])
        self.assertIn("CREATE TABLE IF NOT EXISTS completion_receipts", conn.calls[2][0])

if __name__ == "__main__":
    unittest.main()
