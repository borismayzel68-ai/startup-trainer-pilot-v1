"""MOS-5 (#56): вид фонда — шлюзы, Go/Pause/Stop, доступ, LP-поля, стандарт. Данные синтетические, сеть не используется.
Тесты с БД запускаются только при HQ018_PG_URL=postgresql://... (в CI — Postgres 16)."""
import os
import re
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

import pytest

from fund_view import service as S
from fund_view.gates import GateBlocked, check_transition, evaluate, failed_gates

PG_URL = os.environ.get("HQ018_PG_URL", "")
ROOT = Path(__file__).resolve().parent.parent
LP_TERMS = re.compile(r"tvpi|dpi|rvpi|management[_ ]?fee|carry", re.I)

FULL = {"thesis": "Синтетический тезис", "buyer": "Synth Pharma", "buyer_when": "2028",
        "asf_engagement": "FIXED"}


def test_gates_logic():
    assert evaluate(None) == {"thesis": False, "buyer": False, "engagement": False}
    assert failed_gates(FULL) == []
    assert failed_gates({**FULL, "thesis": "  "}) == ["thesis"]
    assert failed_gates({**FULL, "buyer_when": ""}) == ["buyer"]  # «кто» без «когда» — не проходит
    assert failed_gates({**FULL, "asf_engagement": "NOT_DECIDED"}) == ["engagement"]
    assert failed_gates({**FULL, "asf_engagement": "DECLINED"}) == ["engagement"]


def test_gate_blocks_dd_and_ic_only():
    for to in ("DD", "IC"):
        with pytest.raises(GateBlocked) as e:
            check_transition(to, None)
        assert e.value.failed == ["thesis", "buyer", "engagement"]
        check_transition(to, FULL)
    for to in ("ACTIVE", "PASS", "ON_HOLD", "SCREENING"):
        check_transition(to, None)


def test_standard_missing_is_marked(tmp_path):
    r = S.standard_checklist(tmp_path / "nope.md")
    assert r == {"loaded": False, "label": "стандарт не загружен", "items": []}


def test_standard_read_verbatim(tmp_path):
    f = tmp_path / "STD.md"
    f.write_text("# Раздел 1\n- [ ] Пункт А\n- обычный список\n## Раздел 2\n- [x] Пункт Б\n", encoding="utf-8")
    r = S.standard_checklist(f)
    assert r["loaded"] is True
    assert r["items"] == [{"section": "Раздел 1", "text": "Пункт А"}, {"section": "Раздел 2", "text": "Пункт Б"}]


def test_repo_without_standard_file_reports_not_loaded():
    if (ROOT / "docs" / "standards" / "ARTUR_STANDARD.md").exists():
        pytest.skip("стандарт загружен")
    assert S.standard_checklist()["label"] == "стандарт не загружен"


def test_no_lp_fields_in_code_and_schema():
    files = [ROOT / "db" / "migrations" / "004_fund_view.sql", *(ROOT / "fund_view").glob("*.py")]
    for f in files:
        text = f.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^\s*(--|#).*$|\"\"\".*?\"\"\"", "", text, flags=re.S)  # комментарии/докстринги
        assert not LP_TERMS.search(text), f"LP-метрика в {f.name}"
    assert not LP_TERMS.search(" ".join(S.COLUMNS))


def _client():
    import app as appmod
    return appmod.app.test_client()


def test_noindex_headers_and_robots():
    c = _client()
    r = c.get("/fund/startups/SYN-X/snapshot")
    assert "noindex" in r.headers["X-Robots-Tag"]
    assert "Disallow: /" in c.get("/robots.txt").get_data(as_text=True)


def test_no_db_gives_503_not_data(monkeypatch):
    monkeypatch.setattr("fund_view.blueprint.connect", lambda: None)
    assert _client().get("/fund/startups/SYN-X/snapshot").status_code == 503


@unittest.skipUnless(PG_URL.startswith("postgresql://"), "HQ018_PG_URL не задан")
class FundViewPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, SESSION_DATABASE_URL=PG_URL)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "hq018_migrate.py")], env=env, check=True)

    def setUp(self):
        import psycopg
        self.tag = uuid.uuid4().hex[:8]
        self.vc = "SYN-" + self.tag
        self.conn = psycopg.connect(PG_URL)
        self.tokens = {}
        parts = []
        for role in ("owner", "fund_staff", "investor"):
            email = f"{role}-{self.tag}@synthetic.test"
            uid = self.conn.execute("INSERT INTO users (email, display_name, is_synthetic) VALUES (%s,%s,TRUE) "
                                    "RETURNING id", (email, role)).fetchone()[0]
            self.conn.execute("INSERT INTO role_grants (user_id, role_code) VALUES (%s,%s)", (uid, role))
            self.tokens[role] = f"tok-{role}-{self.tag}"
            parts.append(f"{self.tokens[role]}={email}")
        self.tokens["stranger"] = "tok-not-in-allowlist"
        self.conn.commit()
        os.environ["FUND_TOKENS"] = ";".join(parts)
        os.environ["SESSION_DATABASE_URL"] = PG_URL
        from dealflow.status import create_startup
        self.sid = create_startup(self.conn, self.vc, "Synth Fund One", status="ACTIVE")
        self.conn.commit()
        self.c = _client()

    def tearDown(self):
        os.environ.pop("FUND_TOKENS", None)
        self.conn.close()

    def h(self, role):
        return {"Authorization": f"Bearer {self.tokens[role]}"}

    def url(self, tail):
        return f"/fund/startups/{self.vc}/{tail}"

    def fill_snapshot(self, engagement=True):
        r = self.c.put(self.url("snapshot"), headers=self.h("fund_staff"),
                       json={"thesis": "Синтетический тезис", "buyer": "Synth Pharma", "buyer_when": "2028"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        if engagement:
            r = self.c.post(self.url("engagement"), headers=self.h("owner"), json={"value": "FIXED"})
            self.assertEqual(r.status_code, 200)

    # --- доступ
    def test_access(self):
        self.assertEqual(self.c.get(self.url("snapshot")).status_code, 401)
        self.assertEqual(self.c.get(self.url("snapshot"), headers=self.h("stranger")).status_code, 401)
        self.assertEqual(self.c.get(self.url("snapshot"), headers=self.h("investor")).status_code, 403)
        self.assertEqual(self.c.put(self.url("snapshot"), headers=self.h("investor"), json={"thesis": "x"}
                                    ).status_code, 403)
        self.assertEqual(self.c.post(self.url("ic-decision"), headers=self.h("investor"), json={}).status_code, 403)
        for role in ("owner", "fund_staff"):
            r = self.c.get(self.url("snapshot"), headers=self.h(role))
            self.assertEqual(r.status_code, 200)
            self.assertIn("noindex", r.headers["X-Robots-Tag"])
            self.assertFalse(LP_TERMS.search(r.get_data(as_text=True)))
        html = self.c.get(self.url("snapshot.html"), headers=self.h("owner"))
        self.assertIn("noindex", html.get_data(as_text=True))

    # --- шлюзы
    def test_gates_block_transition_on_server(self):
        r = self.c.post(self.url("transition"), headers=self.h("fund_staff"), json={"to_status": "DD"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["failed_gates"], ["thesis", "buyer", "engagement"])
        self.fill_snapshot(engagement=False)
        r = self.c.post(self.url("transition"), headers=self.h("fund_staff"), json={"to_status": "DD"})
        self.assertEqual(r.get_json()["failed_gates"], ["engagement"])
        self.fill_snapshot()
        r = self.c.post(self.url("transition"), headers=self.h("fund_staff"), json={"to_status": "DD"})
        self.assertEqual(r.status_code, 200)
        r = self.c.post(self.url("transition"), headers=self.h("fund_staff"), json={"to_status": "IC"})
        self.assertEqual(r.status_code, 200)

    def test_direct_change_status_also_blocked(self):
        from dealflow.status import change_status
        with self.assertRaises(GateBlocked):
            change_status(self.conn, self.sid, "DD")
        self.conn.rollback()

    def test_engagement_only_owner(self):
        r = self.c.post(self.url("engagement"), headers=self.h("fund_staff"), json={"value": "FIXED"})
        self.assertEqual(r.status_code, 403)

    # --- FACT только по подписи
    def test_fact_only_by_owner_signature_and_reset_on_edit(self):
        self.fill_snapshot()
        r = self.c.put(self.url("snapshot"), headers=self.h("owner"), json={"evidence_status": "fact"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.post(self.url("sign-fact"), headers=self.h("fund_staff")).status_code, 403)
        r = self.c.post(self.url("sign-fact"), headers=self.h("owner"))
        self.assertEqual(r.get_json()["snapshot"]["evidence_status"], "fact")
        r = self.c.put(self.url("snapshot"), headers=self.h("fund_staff"), json={"key_risks": "новый риск"})
        self.assertEqual(r.get_json()["snapshot"]["evidence_status"], "hypothesis")

    def test_default_evidence_is_hypothesis(self):
        self.fill_snapshot(engagement=False)
        r = self.c.get(self.url("snapshot"), headers=self.h("owner"))
        self.assertEqual(r.get_json()["snapshot"]["evidence_status"], "hypothesis")

    # --- Go / Pause / Stop
    def _decide(self, decision, quarter="2026-Q4", reason="синтетическая причина", role="owner"):
        return self.c.post(self.url("ic-decision"), headers=self.h(role),
                           json={"quarter": quarter, "decision": decision, "reason": reason})

    def test_go_pause_stop_write_history_and_audit(self):
        r = self._decide("GO")
        self.assertEqual((r.status_code, r.get_json()["to_status"]), (200, "ACTIVE"))
        r = self._decide("PAUSE", "2027-Q1", role="fund_staff")
        self.assertEqual(r.get_json()["to_status"], "ON_HOLD")
        r = self._decide("GO", "2027-Q2")  # из ON_HOLD возвращает в ACTIVE
        self.assertEqual(r.get_json()["to_status"], "ACTIVE")
        r = self._decide("STOP", "2027-Q3")
        self.assertEqual(r.get_json()["to_status"], "PASS")
        decisions = self.conn.execute("SELECT decision FROM ic_decisions WHERE startup_id=%s ORDER BY id",
                                      (self.sid,)).fetchall()
        self.assertEqual([d[0] for d in decisions], ["GO", "PAUSE", "GO", "STOP"])
        hist = self.conn.execute("SELECT to_status::text FROM deal_status_history WHERE startup_id=%s ORDER BY id",
                                 (self.sid,)).fetchall()
        self.assertEqual([h[0] for h in hist], ["ACTIVE", "ON_HOLD", "ACTIVE", "PASS"])
        n = self.conn.execute("SELECT count(*) FROM audit_log WHERE action='ic.decision' AND entity_id=%s",
                              (self.sid,)).fetchone()[0]
        self.assertEqual(n, 4)

    def test_decision_validation(self):
        for body in ({"quarter": "2026-Q5", "decision": "GO", "reason": "x"},
                     {"quarter": "2026-Q4", "decision": "MAYBE", "reason": "x"},
                     {"quarter": "2026-Q4", "decision": "GO", "reason": " "}):
            r = self.c.post(self.url("ic-decision"), headers=self.h("owner"), json=body)
            self.assertEqual(r.status_code, 400)
        n = self.conn.execute("SELECT count(*) FROM ic_decisions WHERE startup_id=%s", (self.sid,)).fetchone()[0]
        self.assertEqual(n, 0)

    def test_decision_log_append_only(self):
        import psycopg
        self._decide("GO")
        with self.assertRaises(psycopg.errors.RaiseException):
            self.conn.execute("UPDATE ic_decisions SET reason='x' WHERE startup_id=%s", (self.sid,))
        self.conn.rollback()
        with self.assertRaises(psycopg.errors.RaiseException):
            self.conn.execute("DELETE FROM audit_log WHERE entity_id=%s", (self.sid,))
        self.conn.rollback()
