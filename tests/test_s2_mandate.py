"""S2 · шлюз мандата фонда (#44). Данные синтетические. Правила — из investment_policy.yaml."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_s1_intake as base  # noqa: E402
import test_tg1_human_gate as tg1  # noqa: E402
from s2_mandate import engine as E  # noqa: E402
from s2_mandate.blueprint import bp as s2_bp  # noqa: E402

M = 1_000_000
GOOD = {"check_rub": 8 * M, "instrument": "convertible_loan", "sector": "medtech", "innovative": True}
POLICY = E.load_policy()


def ev(**kw):
    return E.evaluate({**GOOD, **kw}, POLICY)["result"]


class EngineTests(unittest.TestCase):
    def test_check_boundaries(self):
        self.assertEqual(ev(check_rub=5_900_000), E.NO_FIT)
        self.assertEqual(ev(check_rub=6 * M), E.FIT)
        self.assertEqual(ev(check_rub=10 * M), E.FIT)
        self.assertEqual(ev(check_rub=10_100_000), E.NO_FIT)

    def test_each_instrument(self):
        self.assertEqual(ev(instrument="convertible_loan"), E.FIT)
        for name, kind in POLICY["instruments"].items():
            if kind == "exception":
                self.assertEqual(ev(instrument=name), E.EXCEPTION, name)
            if kind == "no_fit":
                self.assertEqual(ev(instrument=name), E.NO_FIT, name)
        self.assertEqual(ev(instrument="equity"), E.EXCEPTION)
        self.assertEqual(ev(instrument="loan"), E.EXCEPTION)
        self.assertEqual(ev(instrument="safe"), E.EXCEPTION)
        self.assertEqual(ev(instrument="grant"), E.NO_FIT)
        self.assertEqual(ev(instrument="unheard_of"), E.EXCEPTION)

    def test_sector(self):
        for s in ("life_sciences", "medtech", "biotech"):
            self.assertEqual(ev(sector=s), E.FIT)
        self.assertEqual(ev(sector="fintech"), E.NO_FIT)
        self.assertEqual(ev(innovative=False), E.NO_FIT)

    def test_no_fit_beats_exception(self):
        self.assertEqual(ev(instrument="equity", sector="fintech"), E.NO_FIT)
        self.assertEqual(ev(instrument="equity", check_rub=11 * M), E.NO_FIT)

    def test_rules_come_from_config_not_code(self):
        d = Path(tempfile.mkdtemp()) / "p.yaml"
        d.write_text("check_rub: {min: 1000, max: 2000}\ninstruments: {equity: preferred}\nsectors: [agri]\n",
                     encoding="utf-8")
        p = E.load_policy(str(d))
        ok = {"check_rub": 1500, "instrument": "equity", "sector": "agri", "innovative": True}
        self.assertEqual(E.evaluate(ok, p)["result"], E.FIT)
        self.assertEqual(E.evaluate({**ok, "sector": "medtech"}, p)["result"], E.NO_FIT)
        src = (Path(E.__file__)).read_text(encoding="utf-8")
        for hardcoded in ("6000000", "10000000", "medtech", "convertible_loan"):
            self.assertNotIn(hardcoded, src)

    def test_validate_input(self):
        self.assertEqual(E.validate_input(GOOD), [])
        self.assertEqual(E.validate_input({**GOOD, "sector": ""}), ["sector"])
        self.assertIn("check_rub", E.validate_input({**GOOD, "check_rub": -1}))
        self.assertIn("innovative", E.validate_input({**GOOD, "innovative": "yes"}))
        for bad in (["equity"], {"a": 1}, 5):
            self.assertEqual(E.validate_input({**GOOD, "instrument": bad}), ["instrument"])
            self.assertEqual(E.validate_input({**GOOD, "sector": bad}), ["sector"])
        for bad in (float("nan"), float("inf"), float("-inf")):
            self.assertEqual(E.validate_input({**GOOD, "check_rub": bad}), ["check_rub"])


class S2HttpTests(tg1.Tg1Base):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(s2_bp)

    def create(self, **kw):
        r = self.c.post("/s2/checks", headers=base.ADMIN, json={**GOOD, **kw})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()

    def test_fit_and_no_fit_need_no_human(self):
        self.assertTrue(self.create()["cleared_for_meeting"])
        r = self.create(sector="fintech")
        self.assertEqual(r["result"], E.NO_FIT)
        self.assertFalse(r["cleared_for_meeting"])
        self.assertEqual(self.c.post("/s2/checks/" + r["id"] + "/decision", headers=tg1.ALICE,
                                     json={"decision": "approve"}).status_code, 409)

    def test_missing_field_returns_422(self):
        r = self.c.post("/s2/checks", headers=base.ADMIN, json={**GOOD, "sector": ""})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["missing"], ["sector"])
        self.assertEqual(self.c.post("/s2/checks", json=GOOD).status_code, 401)

    def test_bad_types_and_non_finite_return_422_not_500(self):
        for kw in ({"instrument": ["equity"]}, {"instrument": {"a": 1}}, {"sector": ["medtech"]}):
            r = self.c.post("/s2/checks", headers=base.ADMIN, json={**GOOD, **kw})
            self.assertEqual(r.status_code, 422, kw)
        for lit in ("NaN", "Infinity", "-Infinity"):
            raw = '{"check_rub": %s, "instrument": "equity", "sector": "medtech", "innovative": true}' % lit
            r = self.c.post("/s2/checks", headers=base.ADMIN, data=raw, content_type="application/json")
            self.assertEqual(r.status_code, 422, lit)
            self.assertEqual(r.get_json()["missing"], ["check_rub"])

    def test_check_is_created_ready_for_review_not_received(self):
        cid = self.create()["id"]
        self.assertEqual(self.app.extensions["s1_intake"].store.get(cid)["status"], "ready_for_review")

    def test_second_decision_is_409_and_store_is_atomic(self):
        c = self.create(instrument="equity")
        url = "/s2/checks/" + c["id"] + "/decision"
        self.assertEqual(self.c.post(url, headers=tg1.ALICE, json={"decision": "approve"}).status_code, 200)
        self.assertEqual(self.c.post(url, headers=tg1.BOB, json={"decision": "reject"}).status_code, 409)
        # гонка: проверка _decision уже прошла, но запись второго подписанта отбивается UNIQUE в журнале
        store = self.app.extensions["s1_intake"].store
        self.assertTrue(store.log_decision_once("race", "alice", "h", "s2_exception_approved"))
        self.assertFalse(store.log_decision_once("race", "bob", "h", "s2_exception_rejected"))
        self.assertEqual(len(store.approvals("race")), 1)

    def test_exception_not_cleared_without_human_signature(self):
        c = self.create(instrument="equity")
        self.assertEqual(c["result"], E.EXCEPTION)
        self.assertFalse(c["cleared_for_meeting"])
        url = "/s2/checks/" + c["id"] + "/decision"
        body = {"decision": "approve"}
        self.assertEqual(self.c.post(url, json=body).status_code, 401)
        self.assertEqual(self.c.post(url, headers=base.ADMIN, json=body).status_code, 403)  # admin ≠ человек
        got = self.c.get("/s2/checks/" + c["id"], headers=base.ADMIN).get_json()
        self.assertFalse(got["cleared_for_meeting"])
        self.assertIsNone(got["human_decision"])

    def test_exception_cleared_only_by_personal_approve(self):
        c = self.create(instrument="equity")
        url = "/s2/checks/" + c["id"] + "/decision"
        self.assertEqual(self.c.post(url, headers=tg1.ALICE, json={"decision": "maybe"}).status_code, 400)
        r = self.c.post(url, headers=tg1.ALICE, json={"decision": "approve"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["cleared_for_meeting"])
        self.assertEqual(r.get_json()["human_decision"]["by"], "alice")
        self.assertEqual(self.c.post(url, headers=tg1.BOB, json={"decision": "reject"}).status_code, 409)

    def test_exception_rejected_by_human_stays_blocked(self):
        c = self.create(instrument="loan")
        r = self.c.post("/s2/checks/" + c["id"] + "/decision", headers=tg1.BOB, json={"decision": "reject"})
        self.assertEqual(r.get_json()["human_decision"]["decision"], "rejected")
        self.assertFalse(r.get_json()["cleared_for_meeting"])


@pytest.fixture
def rbac_client():
    """Реальный вход MOS-1 (сессия + гранты), RBAC включён маркером auth_enforced."""
    os.environ.setdefault("OPENAI_API_KEY", "test-key")
    os.environ.setdefault("SESSION_DATABASE_URL", "sqlite:///:memory:")
    import app as appmod
    from auth_store import AuthStore
    fresh, old = AuthStore("sqlite:///:memory:"), appmod.app.extensions["auth_store"]
    appmod.app.extensions["auth_store"] = fresh

    def client(email=None, role=None):
        c = appmod.app.test_client()
        if email:
            os.environ["AUTH_ALLOWLIST"] = os.environ.get("AUTH_ALLOWLIST", "") + ",%s:%s" % (email, role)
            user = fresh.upsert_user(email, "Synthetic")
            fresh.ensure_grant(user["id"], role, "*")
            with c.session_transaction() as s:
                s["uid"] = user["id"]
                s["csrf"] = "csrf-" + email
            c.hdr = {"X-CSRF-Token": "csrf-" + email}
        return c
    yield client
    appmod.app.extensions["auth_store"] = old


@pytest.mark.auth_enforced
def test_rbac_create_and_decide(rbac_client):
    assert rbac_client().post("/s2/checks", json=GOOD).status_code in (401, 403)  # без входа и роли
    assert (lambda c: c.post("/s2/checks", json=GOOD, headers=c.hdr))(rbac_client("inv@example.test", "investor")).status_code in (401, 403)
    staff = rbac_client("s2staff@example.test", "fund_staff")
    r = staff.post("/s2/checks", json={**GOOD, "instrument": "equity"}, headers=staff.hdr)
    assert r.status_code == 201, r.get_json()
    cid = r.get_json()["id"]
    url = "/s2/checks/" + cid + "/decision"
    assert staff.post(url, json={"decision": "approve"}, headers=staff.hdr).status_code == 403
    owner = rbac_client("s2owner@example.test", "owner")  # сессия без персонального Bearer решение не подписывает
    assert owner.post(url, json={"decision": "approve"}, headers=owner.hdr).status_code == 403
    assert rbac_client().post(url, json={"decision": "approve"}).status_code in (401, 403)
    assert not owner.get("/s2/checks/" + cid).get_json()["cleared_for_meeting"]


if __name__ == "__main__":
    unittest.main()
