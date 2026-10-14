"""MOS-3: импорт dealflow из CSV. Только синтетика из tests/fixtures/import, сеть не используется."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

from dealflow.importer import ImportFormatError, analyze, apply, extract_drive_id, render_markdown

PG_URL = os.environ.get("HQ018_PG_URL", "")
ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures" / "import"
PATHS = {"migration_plan": str(FX / "migration_plan.csv"), "core_dashboard": str(FX / "core_dashboard.csv"),
         "books": str(FX / "books.csv")}


@pytest.mark.parametrize("link,expected", [
    ("https://drive.google.com/file/d/AbC_123-x/view?usp=sharing", ("AbC_123-x", "file")),
    ("https://drive.google.com/drive/folders/FoLd-9_z?usp=sharing", ("FoLd-9_z", "folder")),
    ("https://drive.google.com/drive/u/0/folders/FoLd-9_z", ("FoLd-9_z", "folder")),
    ("https://docs.google.com/spreadsheets/d/SHEET_1/edit#gid=0", ("SHEET_1", "file")),
    ("https://drive.google.com/open?id=OPEN-1", ("OPEN-1", "file")),
    ("https://example.test/file/d/abc", None),
    ("not-a-link", None),
    ("", None),
])
def test_extract_drive_id(link, expected):
    assert extract_drive_id(link) == expected


def test_report_lists_duplicates_and_conflicts():
    rep = analyze(PATHS)
    assert rep["rows_by_source"] == {"migration_plan": 10, "core_dashboard": 7, "books": 4}
    dups = {d["vc_id"] for d in rep["duplicates"]}
    assert dups == {"VC-028", "VC-170", "VC-182"}
    confl = {c["vc_id"]: c for c in rep["conflicts"]}
    assert "names" in confl["VC-085"] and "statuses" in confl["VC-103"]
    assert len(rep["rows_without_id"]) == 1
    assert rep["unknown_zones"] == ["99 — SYNTHETIC ZONE"]
    assert [b["vc_id"] for b in rep["bad_links"]] == ["VC-005"]
    assert rep["no_status_source"] == ["VC-176", "VC-182"]
    w = rep["watchlist"]
    assert w["VC-028"] == "ДУБЛЬ" and w["VC-085"] == "КОНФЛИКТ" and w["VC-103"] == "КОНФЛИКТ"
    assert w["VC-104"] == "требует проверки" and w["VC-170"] == "ДУБЛЬ"
    assert w["VC-176"] == "требует проверки" and w["VC-182"].startswith("ДУБЛЬ")
    md = render_markdown(rep)
    assert "VC-028" in md and "DRY-RUN" in md


def test_clean_ids_and_statuses():
    rep = analyze(PATHS)
    plan = {p["vc_id"]: p for p in rep["to_apply"]}
    assert set(plan) == {"VC-001", "VC-002", "VC-003", "VC-005", "IM-006"}
    assert plan["VC-002"]["status"] == "DD" and plan["IM-006"]["status"] == "INVESTED"
    assert plan["VC-001"]["drive"] == [["SYNbookDDD444", "file"], ["SYNfolderAAA111", "folder"]]
    for blocked in ("VC-028", "VC-085", "VC-103", "VC-104", "VC-170", "VC-176", "VC-182"):
        assert blocked in rep["blocked_ids"]


def test_missing_columns(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ImportFormatError):
        analyze({"books": str(p)})


def _cli(*args, env=None):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "import_dealflow.py"), *args],
                          capture_output=True, text=True, env=env)


def test_cli_dry_run_needs_no_db_and_report_outside_repo(tmp_path):
    out = tmp_path / "r.json"
    r = _cli("--migration-plan", PATHS["migration_plan"], "--core-dashboard", PATHS["core_dashboard"],
             "--books", PATHS["books"], "--report", str(out))
    assert r.returncode == 0, r.stderr
    assert "DRY-RUN" in r.stdout
    assert json.loads(out.read_text(encoding="utf-8"))["duplicates"]
    bad = _cli("--books", PATHS["books"], "--report", str(ROOT / "import_report.md"))
    assert bad.returncode != 0 and not (ROOT / "import_report.md").exists()


def test_cli_apply_requires_db_url():
    env = {k: v for k, v in os.environ.items() if k not in ("SESSION_DATABASE_URL", "DATABASE_URL")}
    r = _cli("--books", PATHS["books"], "--apply", env=env)
    assert r.returncode == 2


@unittest.skipUnless(PG_URL.startswith("postgresql://"), "HQ018_PG_URL не задан")
class ImportPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, SESSION_DATABASE_URL=PG_URL)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "hq018_migrate.py")], env=env, check=True)

    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(PG_URL)
        self.rep = analyze(PATHS)
        # изоляция: итоговые ID фикстур не должны существовать до теста
        ids = [p["vc_id"] for p in self.rep["to_apply"]]
        if self.conn.execute("SELECT count(*) FROM startups WHERE vc_id = ANY(%s)", (ids,)).fetchone()[0]:
            self.skipTest("ID фикстур уже есть в БД")

    def tearDown(self):
        self.conn.rollback()  # apply коммитит только если вызывающий; здесь откатываем всё
        self.conn.close()

    def counts(self):
        return tuple(self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("startups", "deal_status_history", "documents"))

    def test_dry_run_does_not_touch_db(self):
        before = self.counts()
        r = _cli("--migration-plan", PATHS["migration_plan"], "--core-dashboard", PATHS["core_dashboard"],
                 "--books", PATHS["books"], env=dict(os.environ, SESSION_DATABASE_URL=PG_URL))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.counts(), before)

    def test_apply_idempotent_and_skips_blocked(self):
        before = self.counts()
        res = apply(self.conn, self.rep)
        self.assertEqual(res.created, 5)
        self.assertEqual(res.documents_added, 4)
        after = self.counts()
        self.assertEqual(after, (before[0] + 5, before[1] + 5, before[2] + 4))
        reasons = {r[0] for r in self.conn.execute(
            "SELECT h.reason FROM deal_status_history h JOIN startups s ON s.id=h.startup_id "
            "WHERE s.source='import' AND s.vc_id = ANY(%s)", ([p["vc_id"] for p in self.rep["to_apply"]],))}
        self.assertEqual(reasons, {"import"})
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM startups WHERE vc_id IN ('VC-028','VC-085','VC-103','VC-104','VC-170','VC-176','VC-182')"
        ).fetchone()[0], 0)
        res2 = apply(self.conn, self.rep)
        self.assertEqual((res2.created, res2.status_changed, res2.documents_added, res2.unchanged), (0, 0, 0, 5))
        self.assertEqual(self.counts(), after)
