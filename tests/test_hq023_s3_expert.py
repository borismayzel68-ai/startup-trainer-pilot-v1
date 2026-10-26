"""HQ-023 · S3 Эксперт (БП-07). Только синтетика. Human Gate: без подписи владельца бриф не выдаётся (G-12);
функции внешней отправки в коде нет (G-4); подпись — механизм S1 (S1_APPROVERS + s1_approvals)."""
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_s1_intake as base  # noqa: E402
import test_tg1_human_gate as tg1  # noqa: E402
from flask import Flask  # noqa: E402
from s3_expert import cases, engine as E  # noqa: E402
from s3_expert.blueprint import bp as s3_bp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GOOD = cases.CASES[0]["input"]


def _inp(question, bounds, materials, cutoff="2026-09-30"):
    return {"question": question, "bounds": bounds, "cutoff": cutoff, "materials": materials}


def _mat(mid, text, title="Документ (вымышл.)", date="2026-06-01", origin="company"):
    return {"id": mid, "title": title, "date": date, "text": text, "origin": origin}


BOUNDS = "клинические исследования медицинских изделий"


class EngineTests(unittest.TestCase):
    def test_empty_fields_returned_to_user(self):
        for key, label in E.INPUT_FIELDS:
            inp = {**GOOD, key: "" if key != "materials" else []}
            self.assertEqual(E.validate_input(inp), [label])
        self.assertEqual(E.validate_input({**GOOD, "cutoff": "вчера"}), ["дата среза"])
        with self.assertRaises(ValueError):
            E.build_brief({**GOOD, "question": "  "})

    def test_out_of_vertical_refused(self):
        b = E.build_brief(_inp("Какой тариф подписки у SaaS-платформы для маркетплейса?", "тарифы подписки SaaS",
                               [_mat("D1", "Тариф подписки составляет 50 евро в месяц.")]))
        self.assertEqual((b["outcome"], b["class"]), ("ОТКАЗ", None))
        self.assertIn("вне вертикали LS", b["conclusion"])

    def test_two_questions_refused_with_narrowing(self):
        b = E.build_brief(_inp("Какой класс риска у прибора? И сколько пациентов в исследовании?", BOUNDS,
                               [_mat("D1", "Прибор прошёл пилотное клиническое исследование.")]))
        self.assertIn("не один узкий вопрос", b["conclusion"])
        self.assertTrue(2 <= len(b["suggested_questions"]) <= 3)

    def test_medical_and_valuation_requests_refused(self):
        for q in ("Безопасен ли препарат и какая дозировка подходит пациенту?",
                  "Какова оценка компании для клинического раунда?"):
            b = E.build_brief(_inp(q, "клинические данные препаратов", [_mat("D1", "Препарат в первой фазе исследования.")]))
            self.assertEqual((b["outcome"], b["class"]), ("ОТКАЗ", None), q)

    def test_system_never_sets_fact(self):
        for c in cases.CASES:
            b = E.build_brief(c["input"])
            self.assertNotEqual(b["class"], E.FACT)
            self.assertIn(b["class"], (None, *E.ENGINE_CLASSES))
            self.assertTrue(all(x["status"] in E.CLAIM_STATUSES for x in b["all_claims"]))
            self.assertFalse([e for e in b["evidence"] if e["status"].lower() == "факт"])

    def test_fact_without_source_trap_syn04_syn05(self):
        for i in (3, 4):
            b = E.build_brief(cases.CASES[i]["input"])
            self.assertEqual(b["class"], "ПРОБЕЛ")
            self.assertTrue(b["unsourced_fact_flags"])
            self.assertIn("не приняты как доказанные", b["conclusion"])

    def test_strong_claim_ok_when_third_party_source_present(self):
        b = E.build_brief(_inp("Достаточно ли клинического исследования для следующего этапа?", BOUNDS, [
            _mat("D1", "Эффект в клиническом исследовании подтверждён."),
            _mat("D2", "Протокол исследования утверждён.", title="Протокол исследования (вымышл.)", origin="third_party")]))
        self.assertEqual(b["unsourced_fact_flags"], [])

    def test_cutoff_excludes_later_source(self):
        b = E.build_brief(_inp("Достаточно ли клинического исследования для следующего этапа?", BOUNDS, [
            _mat("D1", "Клиническое исследование завершено по протоколу.", title="Протокол (вымышл.)", date="2026-10-05")]))
        self.assertEqual(b["class"], "ПРОБЕЛ")
        self.assertIn("позже даты среза", b["evidence"][0]["note"])

    def test_structured_conflict_names_both_sources(self):
        b = E.build_brief(cases.CASES[6]["input"])  # SYN-07: 12 клиентов ↔ 2 договора
        self.assertEqual(b["class"], "ПРОТИВОРЕЧИЕ")
        self.assertIn("«12»", b["conclusion"])
        self.assertIn("«2»", b["conclusion"])

    def test_missing_required_document_is_gap(self):
        b = E.build_brief(cases.CASES[2]["input"])  # SYN-03: нет лицензионного договора
        self.assertEqual((b["outcome"], b["class"]), ("ПРОБЕЛ", "ПРОБЕЛ"))
        self.assertIn("лицензионный договор", b["missing_docs"])
        self.assertIn("лицензионный договор", b["next_step_human"])

    def test_brief_has_bp07_columns_and_md_json(self):
        b = E.build_brief(GOOD)
        for k in ("question", "checklist", "conclusion", "class", "evidence", "next_step_human", "cutoff"):
            self.assertTrue(b[k], k)
        self.assertEqual(json.loads(E.to_json(b))["question"], b["question"])
        md = E.to_markdown(b)
        for part in ("Вопрос", "Чеклист проверки", "Доказательность", "Дата среза", "Следующий шаг человека", "ждёт подписи"):
            self.assertIn(part, md)

    def test_sha_ignores_sign_block_only(self):
        b = E.build_brief(GOOD)
        self.assertEqual(E.brief_sha256(b), E.brief_sha256({**b, "sign": {"state": "подписан", "by": "x", "at": "y"}}))
        self.assertNotEqual(E.brief_sha256(b), E.brief_sha256({**b, "conclusion": "иначе"}))


class AcceptanceTests(unittest.TestCase):
    def test_threshold(self):
        run = cases.run_acceptance()
        s = run["summary"]
        self.assertEqual(s["total"], 10)
        self.assertGreaterEqual(s["passed"], 8)
        self.assertEqual(s["missed_traps"], 0)
        self.assertEqual(s["safety_violations"], 0)
        self.assertTrue(s["syn10_pass"])
        self.assertTrue(s["threshold_met"])
        self.assertEqual([r["id"] for r in run["results"]], [f"S3-SYN-{i:02d}" for i in range(1, 11)])

    def test_artifacts_md_json_per_case(self):
        import tempfile
        d = Path(tempfile.mkdtemp())
        cases.write_artifacts(d)
        for i in range(1, 11):
            self.assertTrue((d / f"S3-SYN-{i:02d}.md").exists() and (d / f"S3-SYN-{i:02d}.json").exists())
        self.assertTrue((d / "summary.md").exists())

    def test_judge_catches_broken_engine(self):
        """Отрицательный контроль: если сломать правило, приёмка обязана упасть."""
        with mock.patch.object(E, "_mark_conflicts", lambda c: None):
            self.assertFalse(cases.run_acceptance()["summary"]["threshold_met"])
        with mock.patch.object(E, "FACT_MARKERS", ()):
            self.assertGreater(cases.run_acceptance()["summary"]["missed_traps"], 0)
        with mock.patch.object(E, "FORBIDDEN", ()), mock.patch.object(E, "BROAD_MARKERS", ()):
            self.assertFalse(cases.run_acceptance()["summary"]["syn10_pass"])


class S3HttpBase(tg1.Tg1Base):
    def setUp(self):
        super().setUp()
        self.app.register_blueprint(s3_bp)

    def create(self, inp=None):
        r = self.c.post("/s3/briefs", headers=base.ADMIN, json={"payer": "fund", **(inp or GOOD)})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["id"]


class HumanGateTests(S3HttpBase):
    def test_empty_field_returns_422_and_creates_nothing(self):
        n = len(self.c.get("/s1/jobs", headers=base.ADMIN).get_json())
        r = self.c.post("/s3/briefs", headers=base.ADMIN, json={**GOOD, "payer": "fund", "bounds": ""})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["missing"], ["границы компетенции"])
        self.assertEqual(len(self.c.get("/s1/jobs", headers=base.ADMIN).get_json()), n)
        self.assertEqual(self.c.post("/s3/briefs", json={**GOOD, "payer": "fund"}).status_code, 401)

    def test_unsigned_brief_is_awaiting_and_not_issued(self):
        bid = self.create()
        st = self.c.get(f"/s3/briefs/{bid}", headers=base.ADMIN).get_json()
        self.assertEqual((st["state"], st["issued"]), ("ждёт подписи", False))
        for ext in ("json", "md"):
            for h in (base.ADMIN, tg1.ALICE):
                r = self.c.get(f"/s3/briefs/{bid}/brief.{ext}", headers=h)
                self.assertEqual(r.status_code, 403)
                self.assertNotIn(GOOD["question"], r.get_data(as_text=True))
            self.assertEqual(self.c.get(f"/s3/briefs/{bid}/brief.{ext}").status_code, 401)
        self.assertEqual(self.sent, [])

    def test_system_does_not_sign_and_admin_token_cannot(self):
        bid = self.create()
        self.assertEqual(self.c.post(f"/s3/briefs/{bid}/sign", headers=base.ADMIN, json={"checked": True}).status_code, 403)
        self.assertEqual(self.c.post(f"/s3/briefs/{bid}/sign", json={"checked": True}).status_code, 401)
        self.assertEqual(self.c.get(f"/s3/briefs/{bid}/review", headers=base.ADMIN).status_code, 403)
        self.assertEqual(self.state.store.approvals(bid), [])  # создание брифа подписи не пишет
        self.assertEqual(self.c.get(f"/s3/briefs/{bid}", headers=base.ADMIN).get_json()["issued"], False)

    def test_sign_requires_checked(self):
        bid = self.create()
        self.assertEqual(self.c.post(f"/s3/briefs/{bid}/sign", headers=tg1.ALICE, json={}).status_code, 400)
        self.assertEqual(self.c.post(f"/s3/briefs/{bid}/sign", headers=tg1.ALICE, json={"checked": "yes"}).status_code, 400)
        self.assertEqual(self.c.get(f"/s3/briefs/{bid}", headers=base.ADMIN).get_json()["issued"], False)

    def test_review_then_sign_then_issued_with_who_and_when(self):
        bid = self.create()
        rv = self.c.get(f"/s3/briefs/{bid}/review", headers=tg1.ALICE).get_json()
        self.assertTrue(rv["draft"])
        self.assertEqual(rv["sign"]["state"], "ждёт подписи")
        r = self.c.post(f"/s3/briefs/{bid}/sign", headers=tg1.ALICE, json={"checked": True})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["signed_by"], "alice")
        self.assertRegex(r.get_json()["signed_at"], r"^\d{4}-\d\d-\d\dT")
        self.assertEqual(self.c.post(f"/s3/briefs/{bid}/sign", headers=tg1.BOB, json={"checked": True}).status_code, 409)
        md = self.c.get(f"/s3/briefs/{bid}/brief.md", headers=base.ADMIN)
        self.assertEqual(md.status_code, 200)
        self.assertIn("подписан · проверил: alice", md.get_data(as_text=True))
        js = self.c.get(f"/s3/briefs/{bid}/brief.json", headers=tg1.BOB).get_json()
        self.assertEqual((js["sign"]["state"], js["sign"]["by"]), ("подписан", "alice"))
        log = self.state.store.approvals(bid)  # тот же журнал s1_approvals, что у S1
        self.assertEqual([(a["approver"], a["outcome"]) for a in log], [("alice", "s3_signed")])
        self.assertEqual(log[0]["report_sha256"], E.brief_sha256(js))

    def test_tampered_brief_loses_signature(self):
        bid = self.create()
        self.c.post(f"/s3/briefs/{bid}/sign", headers=tg1.ALICE, json={"checked": True})
        job = self.state.store.get(bid)
        s3 = job["data"]["s3"]
        s3["brief"]["conclusion"] = "подменено после подписи"
        self.state.store.update(bid, data_patch={"s3": s3})
        self.assertEqual(self.c.get(f"/s3/briefs/{bid}/brief.md", headers=base.ADMIN).status_code, 403)

    def test_owner_list_restricts_signers(self):
        bid = self.create()
        with mock.patch.dict("os.environ", {"S3_OWNERS": "bob"}):
            self.assertEqual(self.c.post(f"/s3/briefs/{bid}/sign", headers=tg1.ALICE, json={"checked": True}).status_code, 403)
            self.assertEqual(self.c.post(f"/s3/briefs/{bid}/sign", headers=tg1.BOB, json={"checked": True}).status_code, 200)

    def test_refusal_brief_also_waits_for_signature(self):
        bid = self.create(cases.CASES[9]["input"])  # SYN-10, ОТКАЗ
        self.assertEqual(self.c.get(f"/s3/briefs/{bid}/brief.md", headers=base.ADMIN).status_code, 403)

    def test_s1_approve_cannot_send_s3_brief(self):
        bid = self.create()
        r = self.c.post(f"/s1/jobs/{bid}/approve", headers=tg1.ALICE)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.sent, [])

    def test_sign_screen_is_static_without_brief_content(self):
        bid = self.create()
        r = self.c.get(f"/s3/briefs/{bid}/sign-screen", headers=tg1.ALICE)
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Проверил", html)
        self.assertNotIn(GOOD["question"], html)


class Hq062Tests(S3HttpBase):
    """HQ-062: P-91 плательщик, фильтр S3 в /s1/jobs, вход на /sign-screen. Только синтетика."""

    def post(self, payer):
        body = dict(GOOD) if payer is None else {**GOOD, "payer": payer}
        return self.c.post("/s3/briefs", headers=base.ADMIN, json=body)

    def test_payer_fund_and_brainbox_accepted_and_stored(self):
        for payer in ("fund", "brainbox_I"):
            r = self.post(payer)
            self.assertEqual(r.status_code, 201, r.get_json())
            st = self.c.get(f"/s3/briefs/{r.get_json()['id']}", headers=base.ADMIN).get_json()
            self.assertEqual(st["payer"], payer)

    def test_payer_nocvela_rounds_and_others_rejected(self):
        n = len(self.state.store.list(500, s3="only"))
        for bad in ("nocvela", "Nocvela", "rounds", "Rounds", "", "BrainBox_I", "brainbox_i", None, 5, ["fund"]):
            self.assertEqual(self.post(bad).status_code, 422, bad)
        self.assertEqual(len(self.state.store.list(500, s3="only")), n)  # отказ — запись не создаётся

    def test_s1_jobs_hides_s3_from_admin_and_outsiders(self):
        bid = self.create()
        self.assertEqual(self.c.get("/s1/jobs").status_code, 401)  # без входа
        self.assertEqual(self.c.get("/s1/jobs", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        admin = self.c.get("/s1/jobs", headers=base.ADMIN)
        self.assertEqual(admin.status_code, 200)
        self.assertNotIn(bid, [j["id"] for j in admin.get_json()])
        with mock.patch.dict("os.environ", {"S3_OWNERS": "bob"}):
            self.assertEqual(self.c.get("/s1/jobs", headers=tg1.ALICE).status_code, 403)  # чужая роль
        # прямой доступ к записи S3 через S1 закрыт
        self.assertEqual(self.c.get(f"/s1/jobs/{bid}", headers=base.ADMIN).status_code, 404)
        self.assertEqual(self.c.get(f"/s1/jobs/{bid}/readiness-pack.json", headers=base.ADMIN).status_code, 404)

    def test_s1_jobs_shows_s3_to_s3_owner_only_s3(self):
        bid = self.create()
        with mock.patch.dict("os.environ", {"S3_OWNERS": "bob"}):
            r = self.c.get("/s1/jobs", headers=tg1.BOB)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([j["id"] for j in r.get_json()], [bid])
        self.assertNotIn("data", r.get_json()[0])

    def test_sign_screen_requires_login(self):
        bid = self.create()
        url = f"/s3/briefs/{bid}/sign-screen"
        self.assertEqual(self.c.get(url).status_code, 401)
        self.assertEqual(self.c.get(url, headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.c.get(url, headers=base.ADMIN).status_code, 403)  # admin не подписант
        with mock.patch.dict("os.environ", {"S3_OWNERS": "bob"}):
            self.assertEqual(self.c.get(url, headers=tg1.ALICE).status_code, 403)
            self.assertEqual(self.c.get(url, headers=tg1.BOB).status_code, 200)


class NoOutboundCodeTests(unittest.TestCase):
    def test_no_send_capability_in_s3(self):
        pat = re.compile(r"smtplib|send_email|ping_webhook|requests\.|httpx|urllib\.request|socket|post_completion|webhook",
                         re.I)
        for f in (ROOT / "s3_expert").glob("*.py"):
            code = "\n".join(l for l in f.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))
            code = re.sub(r'""".*?"""', "", code, flags=re.S)  # докстринги объясняют запрет — не код
            self.assertIsNone(pat.search(code), f.name)

    def test_s1_scoring_core_untouched_by_s3(self):
        for f in (ROOT / "s3_expert").glob("*.py"):
            self.assertNotIn("s1_intake.scoring", f.read_text(encoding="utf-8"))

    def test_synthetic_only_marker(self):
        self.assertIn("ВСЕ КЕЙСЫ ВЫМЫШЛЕНЫ", Path(cases.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
