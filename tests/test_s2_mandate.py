"""S2 · шлюз мандата фонда (#44). Данные синтетические. Правила — из investment_policy.yaml."""
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_s1_intake as base  # noqa: E402
import test_tg1_human_gate as tg1  # noqa: E402
from s2_mandate import engine as E  # noqa: E402
from s2_mandate.blueprint import bp as s2_bp  # noqa: E402
from s6_perimeter import engine as S6  # noqa: E402

M = 1_000_000
GOOD = {"check_rub": 8 * M, "instrument": "convertible_loan", "sector": "medtech", "innovative": True}
POLICY = E.load_policy()


def s6_fields(check_rub=8 * M, instrument="convertible_loan"):
    """Синтетический пройденный периметр S6 под заявку S2 (сумма траншей = чек)."""
    return {
        "legal_entity": {"value": {"name": "Synth Bio", "form": "ООО"}, "evidence": "SYN-DOC-1"},
        "deal_form": {"value": instrument, "evidence": "SYN-DOC-2"},
        "check_rub": {"value": check_rub, "evidence": "SYN-DOC-3"},
        "covenants": {"value": [{"type": "information_rights"}], "evidence": "SYN-DOC-4"},
        "tranches": {"value": [{"amount_rub": check_rub}], "evidence": "SYN-DOC-5"},
    }


def make_gate(store, fields=None, release=None, **kw):
    """Фикстура: шлюз S6 в хранилище (как /s6/gates, но без входа). Возвращает gate_id."""
    fields = fields if fields is not None else s6_fields(**kw)
    rec = {"company_id": "synthetic", "input": fields, **S6.evaluate(fields, S6.load_policy()), "created_by": "fixture"}
    gid, _ = store.create_or_get("s6-" + os.urandom(8).hex(), "S6 perimeter gate", "", {"s6": {"gate": rec, **(
        {"release": release} if release else {})}}, status="ready_for_review")
    return gid


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

    @property
    def store(self):
        return self.app.extensions["s1_intake"].store

    def gate(self, **kw):
        return make_gate(self.store, **kw)

    def create(self, **kw):
        body = {**GOOD, **kw}
        gid = self.gate(check_rub=body["check_rub"], instrument=body["instrument"])
        r = self.c.post("/s2/checks", headers=base.ADMIN, json={**body, "s6_gate_id": gid})
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

    def post(self, gate_id=None, **kw):
        body = {**GOOD, **kw}
        if gate_id is not None:
            body["s6_gate_id"] = gate_id
        return self.c.post("/s2/checks", headers=base.ADMIN, json=body)

    def test_s6_gate_required_admin_does_not_bypass(self):
        for gid in ("", 5, None):
            self.assertEqual(self.c.post("/s2/checks", headers=base.ADMIN, json={**GOOD, "s6_gate_id": gid}).status_code, 409)
        r = self.post()
        self.assertEqual(r.status_code, 409)
        self.assertIn("s6_gate_id", r.get_json()["error"])
        r = self.post("s6-nonexistent")
        self.assertEqual(r.status_code, 409)
        self.assertIn("не найден", r.get_json()["error"])
        self.assertEqual(len(self._s2_records()), 0)  # записей S2 нет (list() data не отдаёт — читаем через get)

    def test_s6_gate_needs_evidence_is_409_no_record(self):
        bad = s6_fields()
        del bad["covenants"]
        gid = make_gate(self.store, fields=bad)
        r = self.post(gid)
        self.assertEqual(r.status_code, 409)
        self.assertIn("covenants", r.get_json()["error"])
        self.assertNotIn("s2_check_id", self.store.get(gid)["data"]["s6"])
        # result в хранилище подделан — допуска всё равно нет (admit пересчитывает)
        s6 = self.store.get(gid)["data"]["s6"]
        self.store.update(gid, data_patch={"s6": {**s6, "gate": {**s6["gate"], "result": S6.PASS}}})
        self.assertEqual(self.post(gid).status_code, 409)

    def test_s6_passed_gate_is_201_and_linked(self):
        gid = self.gate()
        r = self.post(gid)
        self.assertEqual(r.status_code, 201, r.get_json())
        body = r.get_json()
        self.assertEqual(body["s6_gate"], gid)
        self.assertEqual(self.store.get(gid)["data"]["s6"]["s2_check_id"], body["id"])

    def test_s6_gate_is_single_use(self):
        gid = self.gate()
        self.assertEqual(self.post(gid).status_code, 201)
        r = self.post(gid)
        self.assertEqual(r.status_code, 409)
        self.assertIn("уже использован", r.get_json()["error"])

    def test_s6_owner_release_with_matching_sha_admits(self):
        bad = s6_fields()
        del bad["tranches"]
        gid = make_gate(self.store, fields=bad)
        rec = self.store.get(gid)["data"]["s6"]["gate"]
        wrong = {"by": "owner@example.test", "reason": "r", "sha256": "0" * 64}
        s6 = self.store.get(gid)["data"]["s6"]
        self.store.update(gid, data_patch={"s6": {**s6, "release": wrong}})
        self.assertEqual(self.post(gid).status_code, 409)  # sha256 не совпадает
        ok = {**wrong, "sha256": S6.sha256(rec)}
        self.store.update(gid, data_patch={"s6": {**s6, "release": ok}})
        self.assertEqual(self.post(gid).status_code, 201)

    def test_s6_foreign_or_changed_gate_is_409(self):
        gid = self.gate(check_rub=7 * M)  # шлюз другой заявки: чек не совпадает
        r = self.post(gid, check_rub=8 * M)
        self.assertEqual(r.status_code, 409)
        self.assertIn("не совпадает", r.get_json()["error"])
        self.assertEqual(self.post(self.gate(instrument="equity")).status_code, 409)  # форма сделки другая
        # данные шлюза изменены после снятия owner: release привязан к старому хешу
        bad = s6_fields()
        del bad["tranches"]
        g2 = make_gate(self.store, fields=bad)
        s6 = self.store.get(g2)["data"]["s6"]
        release = {"by": "owner@example.test", "reason": "r", "sha256": S6.sha256(s6["gate"])}
        changed = {**s6["gate"], "input": {**s6["gate"]["input"], "check_rub": {"value": 9 * M, "evidence": "SYN"}}}
        self.store.update(g2, data_patch={"s6": {"gate": changed, "release": release}})
        self.assertEqual(self.post(g2, check_rub=9 * M).status_code, 409)
        # не шлюз S6 (id заявки S1/S2) — тоже 409
        c = self.create()
        self.assertEqual(self.post(c["id"]).status_code, 409)

    def test_forward_to_s2_still_works(self):
        from s6_perimeter.blueprint import forward_to_s2
        with self.app.app_context():
            out, status = forward_to_s2(self.gate(), "medtech", True)
        self.assertEqual(status, 201)
        self.assertEqual(out["result"], E.FIT)

    # --- атомарный захват шлюза S6 (ревью #91): гонки ---
    def _race(self, *calls):
        """Запускает вызовы одновременно; claim() каждого ждёт остальных на барьере — все прошли проверки
        «шлюз свободен» и состязаются ровно за захват."""
        import threading
        from s6_perimeter import claim as CLAIM
        real, barrier, out = CLAIM.claim, threading.Barrier(len(calls), timeout=10), [None] * len(calls)

        def gated(*a, **kw):
            barrier.wait()
            return real(*a, **kw)

        def run(i, fn):
            with self.app.app_context():
                out[i] = fn()

        with mock.patch.object(CLAIM, "claim", gated):
            ts = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
            [t.start() for t in ts]
            [t.join(30) for t in ts]
        return out

    def _s2_records(self):
        full = (self.store.get(j["id"]) for j in self.store.list(limit=1000, s3="all"))  # list() отдаёт без data
        return [j for j in full if (j["data"] or {}).get("s2")]

    def test_parallel_post_same_gate_one_record_one_409(self):
        gid = self.gate()
        post = lambda: self.app.test_client().post("/s2/checks", headers=base.ADMIN, json={**GOOD, "s6_gate_id": gid}).status_code
        codes = self._race(post, post)
        self.assertEqual(sorted(codes), [201, 409])
        self.assertEqual(len(self._s2_records()), 1)
        self.assertEqual(self.store.get(gid)["data"]["s6"]["s2_check_id"], self._s2_records()[0]["id"])
        self.assertNotIn("s2_claim", self.store.get(gid)["data"]["s6"])

    def test_parallel_many_posts_exactly_one_record(self):
        gid = self.gate()
        post = lambda: self.app.test_client().post("/s2/checks", headers=base.ADMIN, json={**GOOD, "s6_gate_id": gid}).status_code
        codes = self._race(*[post] * 6)
        self.assertEqual(sorted(codes), [201] + [409] * 5)
        self.assertEqual(len(self._s2_records()), 1)

    def test_race_forward_to_s2_and_post_one_record(self):
        from s6_perimeter.blueprint import GateUsed, forward_to_s2
        gid = self.gate()

        def post():
            return self.app.test_client().post("/s2/checks", headers=base.ADMIN, json={**GOOD, "s6_gate_id": gid}).status_code

        def fwd():
            try:
                return forward_to_s2(gid, "medtech", True)[1]
            except GateUsed:
                return 409

        codes = self._race(post, fwd)
        self.assertEqual(sorted(codes), [201, 409])
        self.assertEqual(len(self._s2_records()), 1)
        self.assertEqual(self.store.get(gid)["data"]["s6"]["s2_check_id"], self._s2_records()[0]["id"])

    def test_forward_repeat_is_409_like_post(self):
        from s6_perimeter.blueprint import GateUsed, forward_to_s2
        gid = self.gate()
        with self.app.app_context():
            self.assertEqual(forward_to_s2(gid, "medtech", True)[1], 201)
            with self.assertRaises(GateUsed):
                forward_to_s2(gid, "medtech", True)
        self.assertEqual(self.post(gid).status_code, 409)
        self.assertEqual(len(self._s2_records()), 1)

    def test_failed_record_creation_rolls_back_claim(self):
        gid = self.gate()
        with mock.patch.object(type(self.store), "create_or_get", side_effect=RuntimeError("db down")):
            self.assertEqual(self.post(gid).status_code, 500)
        self.assertNotIn("s2_claim", self.store.get(gid)["data"]["s6"])
        self.assertEqual(len(self._s2_records()), 0)
        self.assertEqual(self.post(gid).status_code, 201)  # шлюз снова свободен

    def test_release_does_not_clobber_s2_link(self):
        from s6_perimeter import claim as CLAIM
        gid = self.gate()
        self.assertEqual(self.post(gid).status_code, 201)
        link = self.store.get(gid)["data"]["s6"]["s2_check_id"]
        self.assertTrue(CLAIM.set_release(self.store, gid, {"by": "o", "reason": "r", "sha256": "x"}))
        s6 = self.store.get(gid)["data"]["s6"]
        self.assertEqual(s6["s2_check_id"], link)  # ссылка S2 на месте
        self.assertFalse(CLAIM.set_release(self.store, gid, {"by": "o2", "reason": "r", "sha256": "y"}))  # второй раз — нет
        self.assertEqual(self.store.get(gid)["data"]["s6"]["release"]["by"], "o")

    def test_legacy_s2_record_without_s6_gate_reads_as_before(self):
        rec = {"input": {k: GOOD[k] for k in E.FIELDS}, **E.evaluate(GOOD, E.load_policy())}  # без ключа s6_gate
        cid, _ = self.store.create_or_get("s2-legacy", "S2 mandate check", "", {"s2": {"check": rec}},
                                          status="ready_for_review")
        r = self.c.get("/s2/checks/" + cid, headers=base.ADMIN)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["id"], cid)
        self.assertNotIn("s6_gate", body)
        self.assertTrue(body["cleared_for_meeting"])

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
    gate_body = {"company_id": "synthetic", "fields": s6_fields(instrument="equity")}
    assert rbac_client().post("/s2/checks", json=GOOD).status_code in (401, 403)  # без входа и роли
    assert (lambda c: c.post("/s2/checks", json=GOOD, headers=c.hdr))(rbac_client("inv@example.test", "investor")).status_code in (401, 403)
    staff = rbac_client("s2staff@example.test", "fund_staff")
    assert staff.post("/s2/checks", json={**GOOD, "instrument": "equity"}, headers=staff.hdr).status_code == 409  # без S6
    gid = staff.post("/s6/gates", json=gate_body, headers=staff.hdr).get_json()["id"]
    r = staff.post("/s2/checks", json={**GOOD, "instrument": "equity", "s6_gate_id": gid}, headers=staff.hdr)
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
