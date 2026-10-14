"""MOS-2: схема dealflow, переходы, маппинг. Данные синтетические, сеть не используется."""
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

import pytest

from dealflow.status import (STATUSES, TRANSITIONS, InvalidTransition, change_status, create_startup,
                             map_core, map_zone, validate_transition)

PG_URL = os.environ.get("HQ018_PG_URL", "")
ROOT = Path(__file__).resolve().parent.parent

ZONE_CASES = [
    ("00 — INBOX / НОВЫЕ", None, "NOT_SCREENED", False),
    ("10 — ACTIVE DEALFLOW / SCREENING & DD", "screening", "SCREENING", False),
    ("10 — ACTIVE DEALFLOW / SCREENING & DD", "ACTIVE", "ACTIVE", False),
    ("10 — ACTIVE DEALFLOW / SCREENING & DD", "DD", "DD", False),
    ("10 — ACTIVE DEALFLOW / SCREENING & DD", "что-то", "NOT_SCREENED", True),
    ("99 — SYNTHETIC UNKNOWN", None, "NOT_SCREENED", True),
]
CORE_CASES = [
    (dict(vc_id="IM-006"), "INVESTED", False),
    (dict(vc_id="VC-900", screening="NOT YET SCREENED"), "NOT_SCREENED", False),
    (dict(screening="ANALYZED", investment_view="CONDITIONAL", process_action="MONITOR"), "ON_HOLD", False),
    (dict(screening="ANALYZED", investment_view="CONDITIONAL", process_action="REQUEST_EVIDENCE"), "DD", False),
    (dict(screening="ANALYZED", investment_view="CONDITIONAL"), "ACTIVE", False),
    (dict(screening="ANALYZED", investment_view="UNATTRACTIVE", process_action="CLOSE"), "PASS", False),
    (dict(screening="ANALYZED", investment_view="UNATTRACTIVE", process_action="MONITOR"), "ON_HOLD", False),
    (dict(screening="ANALYZED", investment_view="CONDITIONAL", process_action="CLOSE"), "NOT_SCREENED", True),
    (dict(screening="ANALYZED", investment_view="???", process_action="CLOSE"), "NOT_SCREENED", True),
    (dict(screening="WEIRD"), "NOT_SCREENED", True),
]


@pytest.mark.parametrize("zone,stage,status,flag", ZONE_CASES)
def test_map_zone(zone, stage, status, flag):
    m = map_zone(zone, stage)
    assert (m.status, m.needs_review) == (status, flag)


@pytest.mark.parametrize("kw,status,flag", CORE_CASES)
def test_map_core(kw, status, flag):
    m = map_core(**kw)
    assert (m.status, m.needs_review) == (status, flag)


def test_mapping_doc_matches_code():
    doc = (ROOT / "docs" / "STATUS_MAPPING.md").read_text(encoding="utf-8")
    for s in STATUSES:
        assert s in doc
    for zone in ("00 — INBOX / НОВЫЕ", "10 — ACTIVE DEALFLOW / SCREENING & DD"):
        assert zone in doc
    for src, dst in TRANSITIONS.items():
        for d in dst:
            assert d in doc


def test_transition_table():
    assert set(TRANSITIONS) == set(STATUSES)
    validate_transition("NOT_SCREENED", "SCREENING")
    for bad in (("NOT_SCREENED", "INVESTED"), ("INVESTED", "PASS"), ("SCREENING", "IC"), ("X", "PASS")):
        with pytest.raises(InvalidTransition):
            validate_transition(*bad)


@unittest.skipUnless(PG_URL.startswith("postgresql://"), "HQ018_PG_URL не задан")
class Mos2PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, SESSION_DATABASE_URL=PG_URL)
        for _ in range(2):  # повторное применение без ошибок
            subprocess.run([sys.executable, str(ROOT / "scripts" / "hq018_migrate.py")], env=env, check=True)

    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(PG_URL)
        self.vc = "SYN-" + uuid.uuid4().hex[:8]

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_unique_constraints(self):
        import psycopg
        sid = create_startup(self.conn, self.vc, "Synth One")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.conn.transaction():
                create_startup(self.conn, self.vc, "Dup")
        self.conn.execute("INSERT INTO cabinets (startup_id) VALUES (%s)", (sid,))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.conn.transaction():
                self.conn.execute("INSERT INTO cabinets (startup_id) VALUES (%s)", (sid,))
        self.conn.execute("INSERT INTO portfolio_companies (startup_id) VALUES (%s)", (sid,))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.conn.transaction():
                self.conn.execute("INSERT INTO portfolio_companies (startup_id) VALUES (%s)", (sid,))

    def test_transitions_write_history_and_reject_invalid(self):
        sid = create_startup(self.conn, self.vc, "Synth Two")
        change_status(self.conn, sid, "SCREENING", reason="t")
        change_status(self.conn, sid, "ACTIVE")
        with self.assertRaises(InvalidTransition):
            change_status(self.conn, sid, "INVESTED")
        rows = self.conn.execute(
            "SELECT from_status::text, to_status::text FROM deal_status_history "
            "WHERE startup_id=%s ORDER BY id", (sid,)).fetchall()
        self.assertEqual(rows, [(None, "NOT_SCREENED"), ("NOT_SCREENED", "SCREENING"), ("SCREENING", "ACTIVE")])
        cur = self.conn.execute("SELECT current_status::text FROM startups WHERE id=%s", (sid,)).fetchone()[0]
        self.assertEqual(cur, "ACTIVE")

    def test_history_append_only(self):
        import psycopg
        sid = create_startup(self.conn, self.vc, "Synth Three")
        with self.assertRaises(psycopg.errors.RaiseException):
            self.conn.execute("UPDATE deal_status_history SET reason='x' WHERE startup_id=%s", (sid,))

    def test_documents_have_no_content_or_url_columns(self):
        import psycopg
        cols = {r[0] for r in self.conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='documents'")}
        self.assertEqual(cols, {"id", "startup_id", "kind", "title", "drive_file_id", "created_at"})
        sid = create_startup(self.conn, self.vc, "Synth Four")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.conn.execute(
                "INSERT INTO documents (startup_id, kind, title, drive_file_id) "
                "VALUES (%s,'deck','t','https://x.test/a')", (sid,))
