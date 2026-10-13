"""MOS-6 · pre-deploy: миграции, меняющие данные, — только с точкой восстановления. Данные синтетические."""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import predeploy as pd  # noqa: E402

PG_URL = os.environ.get("HQ018_PG_URL", "")


class Classify(unittest.TestCase):
    def test_current_migrations_are_additive(self):
        self.assertEqual(pd.destructive_migrations(), [])

    def test_additive(self):
        self.assertFalse(pd.is_destructive(
            "CREATE TABLE IF NOT EXISTS a (id INT REFERENCES b(id) ON DELETE CASCADE); -- DROP TABLE x\n"
            "ALTER TABLE a ADD COLUMN IF NOT EXISTS c TEXT;"))

    def test_destructive(self):
        for s in ("DROP TABLE a", "TRUNCATE a", "DELETE FROM a", "update a set b=1",
                  "ALTER TABLE a DROP COLUMN b", "ALTER TABLE a ALTER COLUMN b TYPE TEXT",
                  "ALTER TABLE a RENAME TO c", "INSERT INTO a VALUES (1) ON CONFLICT (id) DO UPDATE SET b=2"):
            self.assertTrue(pd.is_destructive(s), s)

    def test_directory_scan(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / "001.sql").write_text("CREATE TABLE t (id INT);")
            (p / "002.sql").write_text("DROP TABLE t;")
            self.assertEqual(pd.destructive_migrations(p), ["002.sql"])


class Gate(unittest.TestCase):
    ENV = {"DATABASE_URL": "postgresql://u:p@db.invalid/x"}

    def run_main(self, env, snap):
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(pd, "destructive_migrations", return_value=["002.sql"]), \
             mock.patch.object(pd, "snapshot", **snap) as s, \
             mock.patch.object(pd.subprocess, "call", return_value=0) as call:
            with self.assertRaises(SystemExit) as cm:
                pd.main()
        return cm.exception.code, s, call

    def test_backup_failure_blocks_migration(self):
        code, _, call = self.run_main(self.ENV, {"side_effect": RuntimeError("нет доступа")})
        self.assertIn("деплой прерван", str(code))
        call.assert_not_called()

    def test_backup_ok_runs_migration(self):
        code, s, call = self.run_main(self.ENV, {"return_value": "pre_mig_x"})
        self.assertEqual(code, 0)
        s.assert_called_once()
        call.assert_called_once()

    def test_pitr_confirmed_skips_snapshot(self):
        code, s, call = self.run_main({**self.ENV, "PITR_CONFIRMED": "1"}, {"return_value": "x"})
        self.assertEqual(code, 0)
        s.assert_not_called()
        call.assert_called_once()


@unittest.skipUnless(PG_URL.startswith("postgresql://"), "HQ018_PG_URL не задан")
class SnapshotPg(unittest.TestCase):
    def test_snapshot_copies_rows(self):
        import psycopg
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute("DROP TABLE IF EXISTS mos6_snap_t")
            c.execute("CREATE TABLE mos6_snap_t (id INT)")
            c.execute("INSERT INTO mos6_snap_t VALUES (1),(2)")
            schema = pd.snapshot(PG_URL)
            try:
                self.assertEqual(c.execute(f'SELECT count(*) FROM "{schema}".mos6_snap_t').fetchone()[0], 2)
            finally:
                c.execute(f'DROP SCHEMA "{schema}" CASCADE')
                c.execute("DROP TABLE mos6_snap_t")


if __name__ == "__main__":
    unittest.main()
