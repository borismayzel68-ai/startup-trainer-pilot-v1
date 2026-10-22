"""S2 · шлюз мандата фонда (#44). Данные синтетические. Правила — из investment_policy.yaml."""
import sys
import tempfile
import unittest
from pathlib import Path

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
        self.assertEqual(ev(check_rub=5.9 * M), E.NO_FIT)
        self.assertEqual(ev(check_rub=6 * M), E.FIT)
        self.assertEqual(ev(check_rub=10 * M), E.FIT)
        self.assertEqual(ev(check_rub=10.1 * M), E.NO_FIT)

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


if __name__ == "__main__":
    unittest.main()
