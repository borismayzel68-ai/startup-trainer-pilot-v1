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
    assert rep["bad_link_ids"] == ["VC-005"] and "VC-005" in rep["blocked_ids"]
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
    assert set(plan) == {"VC-001", "VC-002", "VC-003", "IM-006"}
    assert plan["VC-002"]["status"] == "DD" and plan["IM-006"]["status"] == "INVESTED"
    assert plan["VC-001"]["drive"] == [["SYNbookDDD444", "file"], ["SYNfolderAAA111", "folder"]]
    for blocked in ("VC-005", "VC-028", "VC-085", "VC-103", "VC-104", "VC-170", "VC-176", "VC-182"):
        assert blocked in rep["blocked_ids"]


def test_missing_columns(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ImportFormatError):
        analyze({"books": str(p)})


HEADER = "VC ID,Название,Зона,Этап,Ссылка"
ROW = "VC-901,Synth Crlf,00 — INBOX / НОВЫЕ,,https://drive.google.com/file/d/SYNcrlf901/view"


@pytest.mark.parametrize("enc,nl", [("utf-8-sig", "\r\n"), ("utf-8", "\r\n"), ("utf-8-sig", "\n")])
def test_bom_and_crlf(tmp_path, enc, nl):
    p = tmp_path / "plan.csv"
    p.write_bytes((HEADER + nl + ROW + nl).encode(enc))
    rep = analyze({"migration_plan": str(p)})
    assert rep["rows_by_source"]["migration_plan"] == 1
    item = rep["to_apply"][0]
    assert item["vc_id"] == "VC-901" and item["name"] == "Synth Crlf" and item["status"] == "NOT_SCREENED"
    assert item["drive"] == [["SYNcrlf901", "file"]] and not rep["bad_links"]


@pytest.mark.parametrize("source,header", [("migration_plan", HEADER), ("books", "VC ID,Название"),
                                           ("core_dashboard", "VC ID,Название,SCREENING")])
def test_header_only_file(tmp_path, source, header):
    p = tmp_path / "empty.csv"
    p.write_text(header + "\r\n", encoding="utf-8-sig")
    rep = analyze({source: str(p)})
    assert rep["rows_by_source"][source] == 0
    assert rep["to_apply"] == [] and rep["blocked_ids"] == [] and rep["unique_ids"] == 0
    assert "DRY-RUN" in render_markdown(rep)


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
        self.assertEqual(res.created, 4)
        self.assertEqual(res.documents_added, 4)
        after = self.counts()
        self.assertEqual(after, (before[0] + 4, before[1] + 4, before[2] + 4))
        reasons = {r[0] for r in self.conn.execute(
            "SELECT h.reason FROM deal_status_history h JOIN startups s ON s.id=h.startup_id "
            "WHERE s.source='import' AND s.vc_id = ANY(%s)", ([p["vc_id"] for p in self.rep["to_apply"]],))}
        self.assertEqual(reasons, {"import"})
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM startups WHERE vc_id IN ('VC-028','VC-085','VC-103','VC-104','VC-170','VC-176','VC-182')"
        ).fetchone()[0], 0)
        res2 = apply(self.conn, self.rep)
        self.assertEqual((res2.created, res2.status_changed, res2.documents_added, res2.unchanged), (0, 0, 0, 4))
        self.assertEqual(self.counts(), after)

    def test_existing_non_import_startup_is_conflict_not_overwritten(self):
        from dealflow.status import create_startup
        manual = next(p for p in self.rep["to_apply"] if p["status"] != "PASS")
        create_startup(self.conn, manual["vc_id"], "Synth Manual", source="manual", status="PASS",
                       reason="manual")
        res = apply(self.conn, self.rep)
        self.assertEqual(res.created, 3)
        self.assertEqual([c["vc_id"] for c in res.conflicts], [manual["vc_id"]])
        cur = self.conn.execute("SELECT current_status::text FROM startups WHERE vc_id=%s",
                                (manual["vc_id"],)).fetchone()[0]
        self.assertEqual(cur, "PASS")
        self.assertIn("КОНФЛИКТ " + manual["vc_id"], render_markdown(self.rep, res))
