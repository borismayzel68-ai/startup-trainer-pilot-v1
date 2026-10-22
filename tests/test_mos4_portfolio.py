"""MOS-4 (#55): RAG Д-68, доступ по ролям, отсутствие LP-полей. Только синтетика, без сети."""
import os
import re
import subprocess
import sys
import unittest
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio import rag, service
from portfolio.access import Actor
from portfolio.rag import RED, YELLOW, GREEN, Thresholds, evaluate, runway_color

ROOT = Path(__file__).resolve().parent.parent
PG_URL = os.environ.get("HQ018_PG_URL", "")
TODAY = date(2026, 10, 22)


@pytest.mark.parametrize("runway,color", [
    ("8.9", RED), ("9", YELLOW), ("9.1", YELLOW), ("12", YELLOW), ("12.1", GREEN), (0, RED), (30, GREEN)])
def test_runway_boundaries(runway, color):
    assert runway_color(runway)[0] == color


@pytest.mark.parametrize("red,yellow", [(5.9, 12), (9, 18.1), (6, 5.9), (19, 20), (12, 12), (12, 9)])
def test_thresholds_out_of_range_rejected(red, yellow):
    with pytest.raises(rag.InvalidThresholds):
        Thresholds(red, yellow)


def test_custom_thresholds_apply():
    th = Thresholds(6, 18)
    assert runway_color("5.9", th)[0] == RED
    assert runway_color("6", th)[0] == YELLOW
    assert runway_color("18", th)[0] == YELLOW
    assert runway_color("18.1", th)[0] == GREEN


@pytest.mark.parametrize("burn,flag", [("119", False), ("120", False), ("121", True), ("81", False), ("80", False), ("79", True)])
def test_burn_deviation_boundaries(burn, flag):
    # plan 100: отклонение 19%/20%/21% вверх (119/120/121) и вниз (81/80/79)
    assert evaluate(15, Decimal(burn), Decimal("100"), TODAY, TODAY).burn_flag is flag


def test_burn_flag_does_not_change_color():
    r = evaluate(15, 200, 100, TODAY, TODAY)
    assert r.burn_flag and r.color == GREEN


def test_burn_plan_missing_or_zero_no_flag():
    assert not evaluate(15, 100, None, TODAY, TODAY).burn_flag
    assert not evaluate(15, 100, 0, TODAY, TODAY).burn_flag


@pytest.mark.parametrize("days,overdue", [(13, False), (14, False), (15, True)])
def test_update_overdue_boundaries(days, overdue):
    r = evaluate(15, 100, 100, TODAY - timedelta(days=days), TODAY)
    assert r.update_overdue is overdue and r.update_days == days


def test_never_updated_is_overdue_and_explained():
    r = evaluate(10, 100, 100, None, TODAY)
    assert r.update_overdue and "ни разу" in r.update_reason


def test_deterministic_and_explained():
    a = evaluate("8.9", 130, 100, TODAY - timedelta(days=20), TODAY)
    assert a == evaluate("8.9", 130, 100, TODAY - timedelta(days=20), TODAY)
    assert all(a.explanation()) and "красный" in a.color_reason


# ---- 6 синтетических компаний ----
def _snap(runway, burn, plan, upd_days, **kw):
    return {"snapshot_date": TODAY, "runway_months": Decimal(str(runway)), "burn": Decimal(burn), "plan_burn": Decimal(plan),
            "last_investor_update": TODAY - timedelta(days=upd_days), "cost": Decimal("1000"), "ownership_pct": Decimal("10"),
            "valuation_basis": "SYN-method", "valuation_date": TODAY, "round": "Seed", **kw}


SYN = {
    "SYN-001": ("fund", _snap("8.9", "100", "100", 3)),
    "SYN-002": ("fund", _snap("9", "119", "100", 13)),
    "SYN-003": ("fund", _snap("12", "120", "100", 14)),
    "SYN-004": ("fund", _snap("12.1", "121", "100", 15)),
    "SYN-005": ("partner_nominee", _snap("10", "100", "100", 1)),
    "SYN-006": ("partner_nominee", _snap("20", "100", "100", 1, loan_income=Decimal("5"))),
}


class FakeRepo:
    def companies(self):
        return [{"vc_id": k, "name": "Synth " + k, "holding_vehicle": v[0], "thresholds": rag.DEFAULT_THRESHOLDS}
                for k, v in SYN.items()]

    def snapshots(self, vc_id):
        return [SYN[vc_id][1]]


ACTORS = {
    "owner": Actor("owner"), "staff": Actor("fund_staff"),
    "inv_all": Actor("investor", frozenset({"*"})), "inv_one": Actor("investor", frozenset({"SYN-002"})),
    "inv_none": Actor("investor"), "founder": Actor("founder", frozenset({"SYN-001"})), "expert": Actor("expert"),
}


def test_six_companies_colors_and_flags():
    got = {r["vc_id"]: (r["rag"]["color"], r["rag"]["burn_flag"], r["rag"]["update_overdue"])
           for r in service.list_view(FakeRepo(), ACTORS["owner"], TODAY)}
    assert got == {"SYN-001": (RED, False, False), "SYN-002": (YELLOW, False, False), "SYN-003": (YELLOW, False, False),
                   "SYN-004": (GREEN, True, True), "SYN-005": (YELLOW, False, False), "SYN-006": (GREEN, False, False)}


@pytest.mark.parametrize("who,n", [("owner", 6), ("staff", 6), ("inv_all", 6), ("inv_one", 1), ("inv_none", 0)])
def test_list_access(who, n):
    assert len(service.list_view(FakeRepo(), ACTORS[who], TODAY)) == n


@pytest.mark.parametrize("who", ["founder", "expert"])
def test_founder_and_others_see_no_portfolio(who):
    with pytest.raises(service.Forbidden):
        service.list_view(FakeRepo(), ACTORS[who], TODAY)
    for vc in SYN:
        with pytest.raises(service.Forbidden):
            service.card_view(FakeRepo(), ACTORS[who], vc, TODAY)


def test_no_actor_forbidden():
    with pytest.raises(service.Forbidden):
        service.list_view(FakeRepo(), None, TODAY)


def test_investor_scope_card():
    service.card_view(FakeRepo(), ACTORS["inv_one"], "SYN-002", TODAY)
    with pytest.raises(service.Forbidden):
        service.card_view(FakeRepo(), ACTORS["inv_one"], "SYN-001", TODAY)


def test_card_explains_and_marks_nominee():
    c = service.card_view(FakeRepo(), ACTORS["owner"], "SYN-005", TODAY)
    assert c["holding_note"] == "деньги фонда, договор на партнёра"
    assert len(c["rag"]["explanation"]) == 3 and c["history"]
    assert service.card_view(FakeRepo(), ACTORS["owner"], "SYN-001", TODAY)["holding_note"] is None
    with pytest.raises(LookupError):
        service.card_view(FakeRepo(), ACTORS["owner"], "SYN-999", TODAY)


LP = re.compile(r"tvpi|dpi|rvpi|management_fee|carry|\bfee\b", re.I)


def test_no_lp_fields_in_views_code_and_migration():
    c = service.card_view(FakeRepo(), ACTORS["owner"], "SYN-006", TODAY)
    assert not LP.search(" ".join(c) + " ".join(service.list_view(FakeRepo(), ACTORS["owner"], TODAY)[0]))
    # в самих модулях и миграции LP-имён нет (упоминание «TVPI…» в комментарии миграции — только запрет)
    for f in (ROOT / "portfolio").glob("*.py"):
        assert not LP.search(f.read_text(encoding="utf-8")), f
    sql = "\n".join(l for l in (ROOT / "db/migrations/004_portfolio_passport.sql").read_text(encoding="utf-8").splitlines()
                    if not l.lstrip().startswith("--"))
    assert not LP.search(sql)


def test_http_default_is_forbidden_and_with_actor_works():
    os.environ.setdefault("OPENAI_API_KEY", "test-key")
    from app import app
    from portfolio import blueprint
    cl = app.test_client()
    blueprint.configure(lambda: None, FakeRepo)
    assert cl.get("/portfolio").status_code == 403
    blueprint.configure(lambda: ACTORS["inv_one"], FakeRepo)
    try:
        assert [r["vc_id"] for r in cl.get("/portfolio").get_json()] == ["SYN-002"]
        assert cl.get("/portfolio/SYN-001").status_code == 403
        assert cl.get("/portfolio/SYN-002").status_code == 200
    finally:
        blueprint.configure(lambda: None, lambda: None)


@unittest.skipUnless(PG_URL.startswith("postgresql://"), "HQ018_PG_URL не задан")
class PortfolioPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, SESSION_DATABASE_URL=PG_URL)
        for _ in range(2):
            subprocess.run([sys.executable, str(ROOT / "scripts" / "hq018_migrate.py")], env=env, check=True)

    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(PG_URL)

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_snapshots_roundtrip_append_only_and_no_lp_columns(self):
        import psycopg
        from dealflow.status import create_startup
        vc = "SYN-" + uuid.uuid4().hex[:8]
        sid = create_startup(self.conn, vc, "Synth PG", status="INVESTED")
        cid = self.conn.execute("INSERT INTO portfolio_companies (startup_id, holding_vehicle) VALUES (%s,'partner_nominee') "
                                "RETURNING id", (sid,)).fetchone()[0]
        for d, rw in ((date(2026, 9, 1), 8), (date(2026, 10, 1), 13)):
            self.conn.execute("INSERT INTO portfolio_passport_snapshots (company_id, snapshot_date, runway_months, burn, plan_burn,"
                              " last_investor_update) VALUES (%s,%s,%s,100,100,%s)", (cid, d, rw, d))
        card = service.card_view(service.PgRepo(self.conn), Actor("owner"), vc, date(2026, 10, 5))
        self.assertEqual(card["rag"]["color"], GREEN)
        self.assertEqual(len(card["history"]), 2)
        self.assertEqual(card["holding_note"], "деньги фонда, договор на партнёра")
        with self.assertRaises(psycopg.errors.RaiseException):
            with self.conn.transaction():
                self.conn.execute("DELETE FROM portfolio_passport_snapshots WHERE company_id=%s", (cid,))
        cols = {r[0] for r in self.conn.execute("SELECT column_name FROM information_schema.columns "
                                                "WHERE table_name IN ('portfolio_companies','portfolio_passport_snapshots')")}
        self.assertFalse([c for c in cols if LP.search(c)])
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.transaction():
                self.conn.execute("UPDATE portfolio_companies SET runway_red_below=5 WHERE id=%s", (cid,))
