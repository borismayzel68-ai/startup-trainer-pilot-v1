"""HQ-018 · E-1: тесты против живого Postgres (идемпотентность по tranid, роли, очередь).
Запускаются только при HQ018_PG_URL=postgresql://... (иначе skip). Данные синтетические."""
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

PG_URL = os.environ.get("HQ018_PG_URL", "")
ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(PG_URL.startswith("postgresql://"), "HQ018_PG_URL не задан")
class Hq018PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, SESSION_DATABASE_URL=PG_URL)
        for script in ("hq018_migrate.py", "hq018_seed.py"):
            subprocess.run([sys.executable, str(ROOT / "scripts" / script)], env=env, check=True)

    def test_existing_tables_created_in_postgres(self):
        import psycopg
        with psycopg.connect(PG_URL) as c:
            names = {r[0] for r in c.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        for t in ("trainer_sessions", "s1_readiness_jobs", "users", "roles", "role_grants", "jobs"):
            self.assertIn(t, names)

    def test_tranid_idempotent_in_s1_store(self):
        from s1_intake.store import ReadinessJobStore
        store = ReadinessJobStore(PG_URL)
        tranid = "synthetic-" + uuid.uuid4().hex
        a, created_a = store.create_or_get(tranid, "Synth", "f@example.test", {})
        b, created_b = store.create_or_get(tranid, "Synth", "f@example.test", {})
        self.assertTrue(created_a)
        self.assertFalse(created_b)
        self.assertEqual(a, b)

    def test_tranid_idempotent_in_jobs_queue(self):
        import psycopg
        tranid = "synthetic-" + uuid.uuid4().hex
        sql = ("INSERT INTO jobs (kind, dedup_key, payload) VALUES ('tilda_intake', %s, %s) "
               "ON CONFLICT (kind, dedup_key) DO NOTHING")
        with psycopg.connect(PG_URL) as c:
            r1 = c.execute(sql, (tranid, json.dumps({"tranid": tranid}))).rowcount
            r2 = c.execute(sql, (tranid, json.dumps({"tranid": tranid}))).rowcount
            n = c.execute("SELECT count(*) FROM jobs WHERE dedup_key=%s", (tranid,)).fetchone()[0]
        self.assertEqual((r1, r2, n), (1, 0, 1))

    def test_jobs_kind_limited_to_two(self):
        import psycopg
        with psycopg.connect(PG_URL) as c:
            with self.assertRaises(psycopg.errors.CheckViolation):
                c.execute("INSERT INTO jobs (kind, dedup_key, payload) VALUES ('dialog', 'x', '{}')")

    def test_roles_only_no_tariff_columns(self):
        import psycopg
        with psycopg.connect(PG_URL) as c:
            roles = {r[0] for r in c.execute("SELECT code FROM roles")}
            cols = {r[0] for r in c.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name IN ('users','role_grants')")}
        self.assertEqual(roles, {"founder", "investor", "expert", "signer", "clinic", "syndicator"})
        self.assertFalse({c for c in cols if "tariff" in c or "subscription" in c or "plan" in c})


if __name__ == "__main__":
    unittest.main()
